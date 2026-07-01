from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from shepherd_kernel_v3_reference.conformance import artifact_from_trace_result, conformance_artifact_to_json
from shepherd_kernel_v3_reference.kernel import elaborate, elaborate_publication_experimental
from shepherd_kernel_v3_reference.kernel.context import ExecutionContext
from shepherd_kernel_v3_reference.kernel.continuation_objects import ContinuationRoot
from shepherd_kernel_v3_reference.kernel.events import (
    ContinuationResumed,
    EffectDeclared,
    HandlerSelected,
    ResumptionCreated,
    WorkerReturned,
)
from shepherd_kernel_v3_reference.kernel.frame_state import (
    BindFrame,
    HandlerFrame,
    HandlerReturnFrame,
    KontState,
    ResumeReturnFrame,
    _require_ref,
)
from shepherd_kernel_v3_reference.kernel.ir import (
    KBind,
    KComputation,
    KHandle,
    KPerform,
    KPure,
    KResumeWith,
)
from shepherd_kernel_v3_reference.kernel.recursive_machine import RecursiveKernelEvaluator
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.eval_direct import eval_expr
from shepherd_kernel_v3_reference.source.experimental import TerminalFork
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed, Forked, SourceOutcome, Suspended
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.source.values import Env
from shepherd_kernel_v3_reference.trace.machine import (
    TraceDebugEvidence,
    TraceResult,
    TraceSession,
    record_from_event,
    run_trace,
)
from shepherd_kernel_v3_reference.trace.records import EffectDeclaration, TerminalResumeResult
from shepherd_kernel_v3_reference.trace.validate import validate_publication_experimental_trace_prefix


def install(effect_kind: str, body: object, handler_id: str = "h.v1") -> StaticHandlerInstall:
    return StaticHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        body=body,
        payload_name="_payload",
    )


def test_step_machine_subclass_can_reuse_helpers_without_recursive_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    program = elaborate(_successful_resumption_term())
    recursive = run_trace(program, engine="recursive", include_debug_evidence=True)
    records = []
    evaluator = _SuccessfulResumptionStepProbe(
        program,
        event_sink=lambda event: records.append(record_from_event(event)),
        evidence_mode="sidecar",
    )
    blocked = {
        "_eval",
        "_continue_value",
        "_perform",
        "_select_handler",
        "_resume",
        "_abort",
        "_forward",
        "_terminal_delay",
        "_terminal_fork",
    }
    for name in blocked:
        monkeypatch.setattr(
            evaluator,
            name,
            _blocked_recursive_scheduler(name),
        )

    outcome = evaluator.run_step()

    stepped = TraceResult(
        outcome=outcome,
        trace=tuple(records),
        debug_evidence=TraceDebugEvidence(
            continuation_root_refs=evaluator.continuation_root_refs,
            continuation_objects=evaluator.continuation_objects,
            program_ref=evaluator.program_ref,
            continuation_ref_map=evaluator.continuation_ref_map,
            continuation_control_ref_map=evaluator.continuation_control_ref_map,
            context_ref_map=evaluator.context_ref_map,
        ),
    )
    assert stepped.outcome == recursive.outcome
    assert stepped.trace == recursive.trace
    assert (
        stepped.require_debug_evidence().continuation_root_refs
        == recursive.require_debug_evidence().continuation_root_refs
    )
    assert (
        stepped.require_debug_evidence().continuation_objects == recursive.require_debug_evidence().continuation_objects
    )
    assert conformance_artifact_to_json(artifact_from_trace_result(stepped)) == conformance_artifact_to_json(
        artifact_from_trace_result(recursive)
    )


def test_escaped_terminal_fork_suspension_reentry_preserves_branch_context() -> None:
    session = TraceSession(
        elaborate_publication_experimental(_fork_branch_suspends_then_handles_downstream_term()),
        include_debug_evidence=True,
    )

    initial = session.run()
    assert isinstance(initial.outcome, Forked)
    branch_outcome = initial.outcome.branches["branch:A"]
    assert isinstance(branch_outcome, Suspended)

    resumed = branch_outcome.continuation.apply("resumed-unhandled")

    assert resumed == Completed("handled-b")
    unhandled_declaration = _effect_declaration(session.trace, "eff.unhandled")
    downstream_declaration = _effect_declaration(session.trace, "eff.b")
    assert unhandled_declaration.branch_ref == "branch:A"
    assert unhandled_declaration.branch_scope_ref is not None
    assert downstream_declaration.branch_ref == unhandled_declaration.branch_ref
    assert downstream_declaration.branch_scope_ref == unhandled_declaration.branch_scope_ref
    evidence = session.debug_evidence
    assert evidence is not None
    downstream_root = evidence.get_continuation_object(downstream_declaration.full_continuation_ref)
    assert isinstance(downstream_root, ContinuationRoot)
    assert downstream_root.branch_ref == downstream_declaration.branch_ref
    assert downstream_root.branch_scope_ref == downstream_declaration.branch_scope_ref
    terminal_result = _terminal_resume_result(session.trace)
    assert terminal_result.resume_ref == unhandled_declaration.branch_scope_ref
    assert terminal_result.branch_ref == unhandled_declaration.branch_ref
    assert terminal_result.branch_scope_ref == unhandled_declaration.branch_scope_ref
    assert terminal_result.value == "handled-b"
    validate_publication_experimental_trace_prefix(session.trace)


def test_multi_hop_escaped_terminal_fork_suspension_keeps_terminal_context() -> None:
    session = TraceSession(
        elaborate_publication_experimental(_fork_branch_suspends_twice_then_handles_downstream_term()),
        include_debug_evidence=True,
    )

    initial = session.run()
    assert isinstance(initial.outcome, Forked)
    first_branch_outcome = initial.outcome.branches["branch:A"]
    assert isinstance(first_branch_outcome, Suspended)

    second_suspension = first_branch_outcome.continuation.apply("first-resume")
    assert isinstance(second_suspension, Suspended)
    assert not _terminal_resume_results(session.trace)

    completed = second_suspension.continuation.apply("second-resume")

    assert completed == Completed("handled-b")
    first_declaration = _effect_declaration(session.trace, "eff.unhandled1")
    second_declaration = _effect_declaration(session.trace, "eff.unhandled2")
    downstream_declaration = _effect_declaration(session.trace, "eff.b")
    assert first_declaration.branch_ref == "branch:A"
    assert first_declaration.branch_scope_ref is not None
    assert second_declaration.branch_ref == first_declaration.branch_ref
    assert second_declaration.branch_scope_ref == first_declaration.branch_scope_ref
    assert downstream_declaration.branch_ref == first_declaration.branch_ref
    assert downstream_declaration.branch_scope_ref == first_declaration.branch_scope_ref
    evidence = session.debug_evidence
    assert evidence is not None
    downstream_root = evidence.get_continuation_object(downstream_declaration.full_continuation_ref)
    assert isinstance(downstream_root, ContinuationRoot)
    assert downstream_root.branch_ref == first_declaration.branch_ref
    assert downstream_root.branch_scope_ref == first_declaration.branch_scope_ref
    terminal_results = _terminal_resume_results(session.trace)
    assert len(terminal_results) == 1
    assert terminal_results[0].resume_ref == first_declaration.branch_scope_ref
    assert terminal_results[0].branch_ref == first_declaration.branch_ref
    assert terminal_results[0].branch_scope_ref == first_declaration.branch_scope_ref
    assert terminal_results[0].value == "handled-b"
    validate_publication_experimental_trace_prefix(session.trace)


def _successful_resumption_term() -> object:
    return Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                    "h.resume",
                ),
            )
        ),
    )


def _fork_branch_suspends_then_handles_downstream_term() -> object:
    return Handle(
        Let(
            "y",
            Perform("eff.a", Lit("payload")),
            Let("z", Perform("eff.unhandled", Var("y")), Perform("eff.b", Var("z"))),
        ),
        HandlerEnv(
            (
                install("eff.a", TerminalFork((("branch:A", Lit("fork-value")),)), "h.fork"),
                install("eff.b", Return(Lit("handled-b")), "h.b"),
            )
        ),
    )


def _fork_branch_suspends_twice_then_handles_downstream_term() -> object:
    return Handle(
        Let(
            "y",
            Perform("eff.a", Lit("payload")),
            Let(
                "z",
                Perform("eff.unhandled1", Var("y")),
                Let("w", Perform("eff.unhandled2", Var("z")), Perform("eff.b", Var("w"))),
            ),
        ),
        HandlerEnv(
            (
                install("eff.a", TerminalFork((("branch:A", Lit("fork-value")),)), "h.fork"),
                install("eff.b", Return(Lit("handled-b")), "h.b"),
            )
        ),
    )


def _effect_declaration(trace: tuple[object, ...], effect_kind: str) -> EffectDeclaration:
    return next(
        record for record in trace if isinstance(record, EffectDeclaration) and record.effect_kind == effect_kind
    )


def _terminal_resume_result(trace: tuple[object, ...]) -> TerminalResumeResult:
    return next(record for record in trace if isinstance(record, TerminalResumeResult))


def _terminal_resume_results(trace: tuple[object, ...]) -> list[TerminalResumeResult]:
    return [record for record in trace if isinstance(record, TerminalResumeResult)]


def _blocked_recursive_scheduler(name: str):
    def blocked(*args: object, **kwargs: object) -> SourceOutcome:
        raise AssertionError(f"step-machine spike called recursive scheduler {name}")

    return blocked


@dataclass(frozen=True)
class _Eval:
    control: KComputation
    env: Env
    kont: KontState
    context: ExecutionContext


@dataclass(frozen=True)
class _Continue:
    value: Any
    kont: KontState
    context: ExecutionContext


@dataclass(frozen=True)
class _Perform:
    op: KPerform
    payload: Any
    kont: KontState
    context: ExecutionContext


@dataclass(frozen=True)
class _Select:
    declaration_ref: str
    op: KPerform
    payload: Any
    captured: KontState
    handler_frame: HandlerFrame
    handler_frame_ref: str
    outer: KontState
    install: object
    worker_context: ExecutionContext


@dataclass(frozen=True)
class _Resume:
    value: Any
    kont: KontState
    context: ExecutionContext


@dataclass(frozen=True)
class _Done:
    outcome: SourceOutcome


_Step = _Eval | _Continue | _Perform | _Select | _Resume | _Done


class _SuccessfulResumptionStepProbe(RecursiveKernelEvaluator):
    """Test-local S1 feasibility probe for the successful handled-resumption path."""

    def run_step(self, env: Env | None = None) -> SourceOutcome:
        root_env = env or Env()
        root_context = ExecutionContext().with_binding_env_ref(self._env_ref(root_env))
        state: _Step = _Eval(self.program.root, root_env, self._empty_kont_state(), root_context)
        while not isinstance(state, _Done):
            state = self._step(state)
        return state.outcome

    def _step(self, state: _Step) -> _Step:
        if isinstance(state, _Eval):
            return self._step_eval(state)
        if isinstance(state, _Continue):
            return self._step_continue(state)
        if isinstance(state, _Perform):
            return self._step_perform(state)
        if isinstance(state, _Select):
            return self._step_select(state)
        if isinstance(state, _Resume):
            return self._step_resume(state)
        raise TypeError(f"unknown step state: {state!r}")

    def _step_eval(self, state: _Eval) -> _Step:
        control = state.control
        if isinstance(control, KPure):
            return _Continue(eval_expr(control.expr, state.env), state.kont, state.context)
        if isinstance(control, KBind):
            return _Eval(
                control.bound,
                state.env,
                self._push_kont_frame(BindFrame(control.binder_id, state.env, state.context), state.kont),
                state.context,
            )
        if isinstance(control, KHandle):
            entry_context = state.context.with_region_ref(control.region_ref).with_binding_env_ref(
                self._env_ref(state.env)
            )
            return _Eval(
                control.body,
                state.env,
                self._push_kont_frame(
                    HandlerFrame(
                        control.handler_env_ref,
                        state.env,
                        control.region_ref,
                        entry_context,
                        state.context,
                    ),
                    state.kont,
                ),
                entry_context,
            )
        if isinstance(control, KPerform):
            payload = eval_expr(control.payload, state.env)
            self._check_schema(control.payload_schema_ref, payload, context=f"perform({control.effect_kind!r}) payload")
            return _Perform(control, payload, state.kont, state.context)
        if isinstance(control, KResumeWith):
            return _Resume(eval_expr(control.value, state.env), state.kont, state.context)
        raise AssertionError(f"S1 probe does not cover control {control!r}")

    def _step_continue(self, state: _Continue) -> _Step:
        if not state.kont.frames:
            return _Done(Completed(state.value))

        head = state.kont.frames[0]
        tail = state.kont.suffix(1)
        if isinstance(head, BindFrame):
            binder = self.program.binders[head.binder_id]
            next_env = head.env.extend(binder.param_name, state.value)
            return _Eval(
                binder.body,
                next_env,
                tail,
                head.context.with_binding_env_ref(self._env_ref(next_env)),
            )
        if isinstance(head, HandlerFrame):
            return _Continue(state.value, tail, head.outer_context)
        if isinstance(head, ResumeReturnFrame):
            if (
                head.resume_ref is not None
                and head.selection_path_ref is not None
                and head.handler_return_frame.selection_ref is not None
                and head.handler_continuation_ref is not None
                and head.handler_dynamic_tail_ref is not None
            ):
                self._emit(
                    WorkerReturned(
                        ref=self._fresh_ref("resume-return"),
                        resume_ref=head.resume_ref,
                        selection_ref=head.handler_return_frame.selection_ref,
                        selection_path_ref=head.selection_path_ref,
                        branch_ref=self._state.branch_ref,
                        handler_continuation_ref=head.handler_continuation_ref,
                        handler_dynamic_tail_ref=head.handler_dynamic_tail_ref,
                        value=state.value,
                        handler_context_ref=self._context_ref(head.handler_context),
                        branch_scope_ref=self._state.branch_scope_ref,
                    )
                )
            next_kont = self._kont_state_from_frame_refs(
                head.handler_continuation + (head.handler_return_frame,),
                head.handler_continuation_frame_refs
                + (_require_ref(head.handler_return_frame_ref, "ResumeReturnFrame.handler_return_frame_ref"),),
                tail=self._kont_state_from_frame_refs(
                    head.handler_dynamic_tail,
                    head.handler_dynamic_tail_frame_refs,
                    expected_stack_ref=head.handler_dynamic_tail_stack_ref,
                ),
            )
            return _Continue(state.value, next_kont, head.handler_context)
        if isinstance(head, HandlerReturnFrame):
            self._check_schema(
                head.install.handled_result_schema_ref,
                state.value,
                context=f"handler({head.install.handler_id!r}) answer",
            )
            if head.selection_ref is not None and head.selection_path_ref is not None:
                capture_ref = self._emit_capture(
                    head,
                    action_kind="return",
                    action_payload=state.value,
                    continuation_disposition="completed",
                )
                self._close_abandoned_selections(
                    head.captured_kont,
                    reason="abandoned",
                    caused_by_ref=capture_ref,
                    caused_by_record_type="EffectCapture",
                    closed_by_selection_ref=head.selection_ref,
                    closed_by_selection_path_ref=head.selection_path_ref,
                )
            return _Continue(state.value, tail, head.outer_context)
        raise TypeError(f"unknown continuation frame: {head!r}")

    def _step_perform(self, state: _Perform) -> _Step:
        declaration_ref = self._fresh_ref("declaration")
        full_continuation_ref = self._kont_ref(
            state.kont,
            continuation_kind="full",
            context=state.context,
            result_schema_ref=state.op.operation_result_schema_ref,
        )
        self._emit(
            EffectDeclared(
                ref=declaration_ref,
                program_ref=self._program_ref(),
                effect_kind=state.op.effect_kind,
                payload=state.payload,
                full_continuation_ref=full_continuation_ref,
                branch_ref=self._state.branch_ref,
                payload_schema_ref=state.op.payload_schema_ref,
                operation_result_schema_ref=state.op.operation_result_schema_ref,
                execution_context_ref=self._context_ref(state.context),
                branch_scope_ref=self._state.branch_scope_ref,
            )
        )
        split = self._find_handler(state.op.effect_kind, state.kont)
        if split is None:
            raise AssertionError("S1 probe only covers handled operations")
        captured, handler_frame, handler_frame_ref, outer, install = split
        return _Select(
            declaration_ref,
            state.op,
            state.payload,
            captured,
            handler_frame,
            handler_frame_ref,
            outer,
            install,
            state.context,
        )

    def _step_select(self, state: _Select) -> _Step:
        selection_ref = self._fresh_ref("selection")
        captured_ref = self._kont_ref(
            state.captured,
            continuation_kind="captured-worker",
            context=state.worker_context,
            result_schema_ref=state.op.operation_result_schema_ref,
        )
        captured_control_ref = self._kont_control_ref(state.captured)
        outer_ref = self._kont_ref(
            state.outer,
            continuation_kind="outer",
            context=state.handler_frame.outer_context,
            result_schema_ref=state.install.handled_result_schema_ref,
        )
        outer_control_ref = self._kont_control_ref(state.outer)
        handler_env = state.handler_frame.env.extend(state.install.payload_name, state.payload)
        handler_context = state.handler_frame.entry_context.with_binding_env_ref(self._env_ref(handler_env))
        self._emit(
            HandlerSelected(
                ref=selection_ref,
                declaration_ref=state.declaration_ref,
                selected_binding_ref=state.install.install_ref,
                handler_id=state.install.handler_id,
                handler_frame_ref=state.handler_frame.handler_env_ref,
                captured_continuation_ref=captured_ref,
                outer_continuation_ref=outer_ref,
                captured_continuation_control_ref=captured_control_ref,
                outer_continuation_control_ref=outer_control_ref,
                handled_result_schema_ref=state.install.handled_result_schema_ref,
                worker_context_ref=self._context_ref(state.worker_context),
                handler_context_ref=self._context_ref(handler_context),
                outer_context_ref=self._context_ref(state.handler_frame.outer_context),
                branch_scope_ref=self._state.branch_scope_ref,
            )
        )
        resumption_handle_ref = self._fresh_ref("resumption")
        self._emit(
            ResumptionCreated(
                ref=resumption_handle_ref,
                declaration_ref=state.declaration_ref,
                selection_ref=selection_ref,
                continuation_ref=captured_ref,
                operation_result_schema_ref=state.op.operation_result_schema_ref,
                handled_result_schema_ref=state.install.handled_result_schema_ref,
                branch_scope_ref=self._state.branch_scope_ref,
            )
        )
        selection_path_ref = self._source_path_ref(selection_ref, resumption_handle_ref)
        handler_return = HandlerReturnFrame(
            install=state.install,
            captured_kont=state.captured.frames,
            captured_frame_refs=state.captured.frame_refs,
            captured_stack_ref=state.captured.cursor.stack_ref,
            selected_handler_frame=state.handler_frame,
            selected_handler_frame_ref=state.handler_frame_ref,
            outer_kont=state.outer.frames,
            outer_frame_refs=state.outer.frame_refs,
            outer_stack_ref=state.outer.cursor.stack_ref,
            handler_env=handler_env,
            worker_context=state.worker_context,
            handler_context=handler_context,
            outer_context=state.handler_frame.outer_context,
            declaration_ref=state.declaration_ref,
            selection_ref=selection_ref,
            resumption_handle_ref=resumption_handle_ref,
            selection_path_ref=selection_path_ref,
            captured_continuation_ref=captured_ref,
            outer_continuation_ref=outer_ref,
            captured_continuation_control_ref=captured_control_ref,
            outer_continuation_control_ref=outer_control_ref,
            operation_result_schema_ref=state.op.operation_result_schema_ref,
            handled_result_schema_ref=state.install.handled_result_schema_ref,
        )
        return _Eval(
            state.install.body,
            handler_env,
            self._push_kont_frame(handler_return, state.outer),
            handler_context,
        )

    def _step_resume(self, state: _Resume) -> _Step:
        split = self._split_at_handler_return(state.kont)
        if split is None:
            raise RuntimeError("Resume(value) used outside any handler body")
        handler_continuation, handler_return, handler_return_frame_ref, handler_dynamic_tail = split
        if handler_return.selection_path_ref is not None and not self._state.consume_source_path(
            handler_return.selection_path_ref
        ):
            raise AssertionError("S1 probe does not cover reused resumptions")
        self._check_schema(
            handler_return.operation_result_schema_ref,
            state.value,
            context=f"resume({handler_return.install.effect_kind!r})",
        )
        resume_ref = self._fresh_ref("resume")
        handler_continuation_ref = self._kont_ref(
            handler_continuation,
            continuation_kind="handler-continuation",
            context=state.context,
            result_schema_ref=handler_return.operation_result_schema_ref,
        )
        handler_dynamic_tail_ref = self._kont_ref(
            handler_dynamic_tail,
            continuation_kind="handler-dynamic-tail",
            context=handler_return.outer_context,
            result_schema_ref=handler_return.handled_result_schema_ref,
        )
        if (
            handler_return.resumption_handle_ref is not None
            and handler_return.declaration_ref is not None
            and handler_return.selection_ref is not None
            and handler_return.selection_path_ref is not None
            and handler_return.captured_continuation_ref is not None
        ):
            self._emit(
                ContinuationResumed(
                    ref=resume_ref,
                    source_ref=handler_return.resumption_handle_ref,
                    source_record_type="ResumptionHandle",
                    declaration_ref=handler_return.declaration_ref,
                    selection_ref=handler_return.selection_ref,
                    selection_path_ref=handler_return.selection_path_ref,
                    continuation_ref=handler_return.captured_continuation_ref,
                    handler_continuation_ref=handler_continuation_ref,
                    handler_dynamic_tail_ref=handler_dynamic_tail_ref,
                    branch_ref=self._state.branch_ref,
                    value=state.value,
                    returns_to_handler=True,
                    worker_context_ref=self._context_ref(handler_return.worker_context),
                    handler_context_ref=self._context_ref(state.context),
                    branch_scope_ref=self._state.branch_scope_ref,
                )
            )
        resume_return = ResumeReturnFrame(
            resume_ref=resume_ref,
            selection_path_ref=handler_return.selection_path_ref,
            handler_continuation_ref=handler_continuation_ref,
            handler_dynamic_tail_ref=handler_dynamic_tail_ref,
            handler_continuation=handler_continuation.frames,
            handler_continuation_frame_refs=handler_continuation.frame_refs,
            handler_continuation_stack_ref=handler_continuation.cursor.stack_ref,
            handler_return_frame=handler_return,
            handler_return_frame_ref=handler_return_frame_ref,
            handler_dynamic_tail=handler_dynamic_tail.frames,
            handler_dynamic_tail_frame_refs=handler_dynamic_tail.frame_refs,
            handler_dynamic_tail_stack_ref=handler_dynamic_tail.cursor.stack_ref,
            handler_context=state.context,
        )
        worker_kont = self._kont_state_from_frame_refs(
            handler_return.captured_kont + (handler_return.selected_handler_frame, resume_return),
            handler_return.captured_frame_refs
            + (
                _require_ref(
                    handler_return.selected_handler_frame_ref, "HandlerReturnFrame.selected_handler_frame_ref"
                ),
            )
            + (self._continuation_frame_ref(resume_return),),
            tail=handler_dynamic_tail,
        )
        return _Continue(state.value, worker_kont, handler_return.worker_context)
