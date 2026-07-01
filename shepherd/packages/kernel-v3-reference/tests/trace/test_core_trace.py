from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.continuation_objects import ContinuationRoot
from shepherd_kernel_v3_reference.schemas import AnySchema, TypeSchema
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.eval_direct import run
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed, Suspended
from shepherd_kernel_v3_reference.source.syntax import Abort, Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace.machine import TraceSession, run_trace
from shepherd_kernel_v3_reference.trace.records import (
    ContinuationResume,
    EffectCapture,
    EffectDeclaration,
    HandlerSelection,
    ResumeReturn,
    ResumptionHandle,
    SelectionClosed,
)
from shepherd_kernel_v3_reference.trace.serde import dumps_trace, loads_trace
from shepherd_kernel_v3_reference.trace.validate import (
    TraceValidationError,
    validate_core0_trace,
    validate_core0_trace_prefix,
    validate_core_a_trace,
    validate_core_a_trace_prefix,
    validate_core_trace,
    validate_core_trace_prefix,
    validate_generated_trace_against_program,
    validate_runtime_trace,
    validate_runtime_trace_prefix,
)


def install(effect_kind: str, body, handler_id: str = "h.v1") -> StaticHandlerInstall:
    return StaticHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        body=body,
        payload_name="_payload",
    )


def test_trace_records_successful_callable_lifecycle() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )

    kernel = elaborate(term)
    result = run_trace(kernel, include_debug_evidence=True)

    assert result.outcome == run(term) == Completed("value")
    assert [type(record) for record in result.trace] == [
        EffectDeclaration,
        HandlerSelection,
        ResumptionHandle,
        ContinuationResume,
        ResumeReturn,
        EffectCapture,
    ]
    declaration = next(r for r in result.trace if isinstance(r, EffectDeclaration))
    selection = next(r for r in result.trace if isinstance(r, HandlerSelection))
    handle = next(r for r in result.trace if isinstance(r, ResumptionHandle))
    resume = next(r for r in result.trace if isinstance(r, ContinuationResume))
    resume_return = next(r for r in result.trace if isinstance(r, ResumeReturn))
    capture = next(r for r in result.trace if isinstance(r, EffectCapture))
    assert declaration.full_continuation_ref.startswith("continuation:runtime:")
    assert selection.captured_continuation_ref == handle.continuation_ref
    assert selection.captured_continuation_ref.startswith("continuation:runtime:")
    assert isinstance(
        result.require_debug_evidence().get_continuation_object(declaration.full_continuation_ref), ContinuationRoot
    )
    assert isinstance(
        result.require_debug_evidence().get_continuation_object(selection.captured_continuation_ref), ContinuationRoot
    )
    assert declaration.execution_context_ref is not None
    assert selection.worker_context_ref == declaration.execution_context_ref
    assert resume.worker_context_ref == selection.worker_context_ref
    assert resume_return.handler_context_ref == selection.handler_context_ref
    assert capture.outer_context_ref == selection.outer_context_ref
    validate_core0_trace(result.trace)
    validate_core_a_trace(result.trace)
    validate_core_trace(result.trace)
    validate_generated_trace_against_program(kernel, result.trace, include_debug_evidence=True)


def test_trace_separates_resume_return_from_handler_capture() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("worker-value")), Return(Lit("answer"))),
                ),
            )
        ),
    )

    result = run_trace(elaborate(term))
    resume_return = next(r for r in result.trace if isinstance(r, ResumeReturn))
    capture = next(r for r in result.trace if isinstance(r, EffectCapture))

    assert resume_return.value == "worker-value"
    assert capture.action_payload == "answer"
    validate_core_trace(result.trace)


def test_trace_records_abort_as_capture_without_resume() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", Abort(Lit("blocked"))),)),
    )

    result = run_trace(elaborate(term))

    assert result.outcome == Completed("blocked")
    assert [type(record) for record in result.trace] == [
        EffectDeclaration,
        HandlerSelection,
        ResumptionHandle,
        EffectCapture,
    ]
    capture = result.trace[-1]
    assert isinstance(capture, EffectCapture)
    assert capture.action_kind == "abort"
    assert capture.continuation_disposition == "aborted"
    with pytest.raises(TraceValidationError, match="Core-0"):
        validate_core0_trace(result.trace)
    validate_core_a_trace(result.trace)
    validate_core_trace(result.trace)


def _runtime_operational_closure_trace(reason: str) -> tuple:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", Abort(Lit("blocked"))),)),
    )
    result = run_trace(elaborate(term))
    capture = next(record for record in result.trace if isinstance(record, EffectCapture))
    return tuple(result.trace) + (
        SelectionClosed(
            ref=f"selection-closed:{reason}",
            selection_ref=capture.selection_ref,
            selection_path_ref=capture.selection_path_ref,
            branch_ref=capture.branch_ref,
            reason=reason,
            caused_by_ref=capture.ref,
            caused_by_record_type="EffectCapture",
            closed_by_selection_ref=capture.selection_ref,
            closed_by_selection_path_ref=capture.selection_path_ref,
            branch_scope_ref=capture.branch_scope_ref,
        ),
    )


def _runtime_operational_closure_after_resume_trace(reason: str) -> tuple:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    records = list(run_trace(elaborate(term)).trace)
    resume_return_idx = next(idx for idx, record in enumerate(records) if isinstance(record, ResumeReturn))
    records.pop(resume_return_idx)
    capture_idx = next(idx for idx, record in enumerate(records) if isinstance(record, EffectCapture))
    capture = records[capture_idx]
    abort_capture = replace(
        capture,
        ref=f"capture:{reason}:after-resume",
        action_kind="abort",
        action_payload={"reason": reason},
        continuation_disposition="aborted",
    )
    records[capture_idx] = abort_capture
    return tuple(records) + (
        SelectionClosed(
            ref=f"selection-closed:{reason}:after-resume",
            selection_ref=abort_capture.selection_ref,
            selection_path_ref=abort_capture.selection_path_ref,
            branch_ref=abort_capture.branch_ref,
            reason=reason,
            caused_by_ref=abort_capture.ref,
            caused_by_record_type="EffectCapture",
            closed_by_selection_ref=abort_capture.selection_ref,
            closed_by_selection_path_ref=abort_capture.selection_path_ref,
            branch_scope_ref=abort_capture.branch_scope_ref,
        ),
    )


@pytest.mark.parametrize("reason", ["runtime_failure", "cancelled"])
def test_runtime_trace_accepts_same_path_operational_closure(reason: str) -> None:
    trace = _runtime_operational_closure_trace(reason)
    decoded = loads_trace(dumps_trace(trace))

    assert decoded == trace
    validate_runtime_trace_prefix(decoded)
    validate_runtime_trace(decoded)
    with pytest.raises(TraceValidationError, match="runtime-operational"):
        validate_core_trace(decoded)


@pytest.mark.parametrize("reason", ["runtime_failure", "cancelled"])
def test_runtime_trace_accepts_resumed_path_operational_closure(reason: str) -> None:
    trace = _runtime_operational_closure_after_resume_trace(reason)
    decoded = loads_trace(dumps_trace(trace))

    assert decoded == trace
    validate_runtime_trace(decoded)
    validate_runtime_trace_prefix(decoded)
    with pytest.raises(TraceValidationError, match="capture cannot precede matching ResumeReturn"):
        validate_core_trace(decoded)
    with pytest.raises(TraceValidationError, match="ResumeReturn or a terminal SelectionClosed"):
        validate_runtime_trace(decoded[:-1])


def test_runtime_trace_rejects_standalone_operational_closure() -> None:
    trace = _runtime_operational_closure_trace("runtime_failure")
    without_capture = tuple(record for record in trace if not isinstance(record, EffectCapture))

    with pytest.raises(TraceValidationError, match="missing cause"):
        validate_runtime_trace(without_capture)


def test_runtime_trace_rejects_non_abort_operational_closure_cause() -> None:
    trace = _runtime_operational_closure_trace("runtime_failure")
    bad_trace = tuple(
        replace(record, action_kind="return", continuation_disposition="completed")
        if isinstance(record, EffectCapture)
        else record
        for record in trace
    )

    with pytest.raises(TraceValidationError, match="reason disagrees with cause"):
        validate_runtime_trace(bad_trace)


def test_runtime_trace_rejects_mismatched_operational_closure_path() -> None:
    trace = _runtime_operational_closure_trace("runtime_failure")
    bad_trace = tuple(
        replace(record, closed_by_selection_path_ref="path:other") if isinstance(record, SelectionClosed) else record
        for record in trace
    )

    with pytest.raises(TraceValidationError, match="missing selected path|must close its own"):
        validate_runtime_trace(bad_trace)


def test_trace_validation_rejects_missing_resume_return() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    result = run_trace(elaborate(term))
    without_return = tuple(record for record in result.trace if not isinstance(record, ResumeReturn))

    with pytest.raises(TraceValidationError, match="ResumeReturn"):
        validate_core_trace(without_return)


def test_completed_trace_rejects_open_selected_path() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", Abort(Lit("blocked"))),)),
    )
    result = run_trace(elaborate(term))
    without_capture = tuple(record for record in result.trace if not isinstance(record, EffectCapture))

    validate_core_trace_prefix(without_capture)
    with pytest.raises(TraceValidationError, match="open selected paths"):
        validate_core_trace(without_capture)


def test_trace_prefix_allows_resume_without_return_when_worker_suspends() -> None:
    term = Handle(
        Let(
            "x",
            Perform("eff.a", Lit(None)),
            Perform("eff.unhandled", Lit("after-resume")),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )

    result = run_trace(elaborate(term))

    assert isinstance(result.outcome, Suspended)
    assert any(isinstance(record, ContinuationResume) for record in result.trace)
    assert not any(isinstance(record, ResumeReturn) for record in result.trace)
    validate_core0_trace_prefix(result.trace)
    validate_core_trace_prefix(result.trace)
    with pytest.raises(TraceValidationError, match="unselected declarations"):
        validate_core_trace(result.trace)


def test_run_trace_returns_snapshot_when_top_level_suspension_resumes() -> None:
    term = Let(
        "x",
        Perform("eff.unhandled", Lit("payload")),
        Handle(
            Perform("eff.a", Var("x")),
            HandlerEnv((install("eff.a", Return(Lit("handled"))),)),
        ),
    )

    result = run_trace(elaborate(term))

    assert isinstance(result.outcome, Suspended)
    assert [type(record) for record in result.trace] == [EffectDeclaration]
    assert result.outcome.continuation.apply("resume-value") == Completed("handled")
    assert [type(record) for record in result.trace] == [EffectDeclaration]


def test_trace_session_exposes_records_after_suspended_continuation_resumes() -> None:
    term = Let(
        "x",
        Perform("eff.unhandled", Lit("payload")),
        Handle(
            Perform("eff.a", Var("x")),
            HandlerEnv((install("eff.a", Return(Lit("handled"))),)),
        ),
    )
    session = TraceSession(elaborate(term))

    result = session.run()

    assert isinstance(result.outcome, Suspended)
    assert [type(record) for record in result.trace] == [EffectDeclaration]
    assert result.outcome.continuation.apply("resume-value") == Completed("handled")
    assert [type(record) for record in session.trace] == [
        EffectDeclaration,
        EffectDeclaration,
        HandlerSelection,
        ResumptionHandle,
        EffectCapture,
    ]
    validate_core_trace_prefix(session.trace)
    with pytest.raises(TraceValidationError, match="unselected declarations"):
        validate_core_trace(session.trace)


def test_trace_session_run_is_single_use() -> None:
    session = TraceSession(elaborate(Return(Lit("done"))))

    assert session.run().outcome == Completed("done")
    with pytest.raises(RuntimeError, match="only once"):
        session.run()


def test_trace_validation_rejects_capture_before_resume_return() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    result = run_trace(elaborate(term))
    records = list(result.trace)
    resume_return_idx = next(idx for idx, record in enumerate(records) if isinstance(record, ResumeReturn))
    capture_idx = next(idx for idx, record in enumerate(records) if isinstance(record, EffectCapture))
    records[resume_return_idx], records[capture_idx] = (
        records[capture_idx],
        records[resume_return_idx],
    )

    with pytest.raises(TraceValidationError, match="capture cannot precede"):
        validate_core_trace(tuple(records))


def test_trace_validation_rejects_capture_on_wrong_selected_path() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, selection_path_ref="path:wrong") if isinstance(record, EffectCapture) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="capture selected path"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_missing_context_ref() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, execution_context_ref=None) if isinstance(record, EffectDeclaration) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="execution_context_ref"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_missing_program_ref() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, program_ref=None) if isinstance(record, EffectDeclaration) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="program_ref"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_selection_worker_context_mismatch() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, worker_context_ref="ctx:wrong") if isinstance(record, HandlerSelection) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="declaration execution context"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_handle_operation_schema_mismatch() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(str)))
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term, registry=registry))
    bad_trace = tuple(
        replace(record, operation_result_schema_ref="schema:wrong") if isinstance(record, ResumptionHandle) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="operation-result schema"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_handle_handled_schema_mismatch() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                StaticHandlerInstall(
                    effect_kind="eff.a",
                    handler_id="h.v1",
                    handled_result_schema=TypeSchema(str),
                    body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    payload_name="_payload",
                ),
            )
        ),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, handled_result_schema_ref="schema:wrong") if isinstance(record, ResumptionHandle) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="handled-result schema"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_resume_worker_context_mismatch() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, worker_context_ref="ctx:wrong") if isinstance(record, ContinuationResume) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="selection worker context"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_resume_return_handler_context_mismatch() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, handler_context_ref="ctx:wrong") if isinstance(record, ResumeReturn) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="resume handler context"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_capture_outer_context_mismatch() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, outer_context_ref="ctx:wrong") if isinstance(record, EffectCapture) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="selection outer context"):
        validate_core_trace(bad_trace)


def test_core_a_rejects_return_capture_with_aborted_disposition() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", Return(Lit("ok"))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, continuation_disposition="aborted") if isinstance(record, EffectCapture) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="return capture"):
        validate_core_a_trace(bad_trace)


def test_core_a_rejects_abort_capture_with_completed_disposition() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", Abort(Lit("blocked"))),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, continuation_disposition="completed") if isinstance(record, EffectCapture) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="abort capture"):
        validate_core_a_trace(bad_trace)


def test_outer_abort_closes_abandoned_inner_selection() -> None:
    term = Handle(
        Handle(
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let(
                            "x",
                            Perform("eff.outer", Lit("ask")),
                            Return(Var("x")),
                        ),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
    )

    result = run_trace(elaborate(term))

    assert result.outcome == Completed("outer-abort")
    closures = [r for r in result.trace if isinstance(r, SelectionClosed)]
    captures = [r for r in result.trace if isinstance(r, EffectCapture)]
    assert len(closures) == 1
    assert len(captures) == 1
    assert closures[0].reason == "skipped_by_outer_abort"
    assert closures[0].caused_by_ref == captures[0].ref
    assert closures[0].caused_by_record_type == "EffectCapture"
    with pytest.raises(TraceValidationError, match="Core-0"):
        validate_core0_trace(result.trace)
    validate_core_a_trace(result.trace)
    validate_core_trace(result.trace)


def test_outer_abort_closes_resumed_inner_selection_without_resume_return() -> None:
    term = Handle(
        Handle(
            Let(
                "x",
                Perform("eff.inner", Lit(None)),
                Perform("eff.outer", Lit("after-resume")),
            ),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let("r", Resume(Lit("worker-value")), Return(Var("r"))),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
    )

    result = run_trace(elaborate(term))

    assert result.outcome == Completed("outer-abort")
    assert any(isinstance(record, ContinuationResume) for record in result.trace)
    inner_closures = [
        record
        for record in result.trace
        if isinstance(record, SelectionClosed) and record.reason == "skipped_by_outer_abort"
    ]
    assert len(inner_closures) == 1
    outer_capture = next(record for record in result.trace if isinstance(record, EffectCapture))
    assert inner_closures[0].caused_by_ref == outer_capture.ref
    assert not any(
        isinstance(record, ResumeReturn) and record.selection_ref == inner_closures[0].selection_ref
        for record in result.trace
    )
    validate_core_trace(result.trace)


def test_outer_return_closes_abandoned_inner_selection() -> None:
    term = Handle(
        Handle(
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let(
                            "x",
                            Perform("eff.outer", Lit("ask")),
                            Return(Var("x")),
                        ),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Return(Lit("outer-return")), "h.outer"),)),
    )

    result = run_trace(elaborate(term))

    assert result.outcome == Completed("outer-return")
    closures = [record for record in result.trace if isinstance(record, SelectionClosed)]
    captures = [record for record in result.trace if isinstance(record, EffectCapture)]
    assert len(closures) == 1
    assert len(captures) == 1
    assert closures[0].reason == "abandoned"
    assert closures[0].caused_by_ref == captures[0].ref
    validate_core_trace(result.trace)


def test_trace_validation_rejects_selection_closure_without_cause() -> None:
    term = Handle(
        Handle(
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let(
                            "x",
                            Perform("eff.outer", Lit("ask")),
                            Return(Var("x")),
                        ),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, caused_by_ref="capture:missing") if isinstance(record, SelectionClosed) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="missing cause"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_selection_closure_before_cause() -> None:
    term = Handle(
        Handle(
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let(
                            "x",
                            Perform("eff.outer", Lit("ask")),
                            Return(Var("x")),
                        ),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
    )
    result = run_trace(elaborate(term))
    records = list(result.trace)
    closure_idx = next(idx for idx, record in enumerate(records) if isinstance(record, SelectionClosed))
    capture_idx = next(
        idx
        for idx, record in enumerate(records)
        if isinstance(record, EffectCapture) and record.ref == records[closure_idx].caused_by_ref
    )
    records[closure_idx], records[capture_idx] = records[capture_idx], records[closure_idx]

    with pytest.raises(TraceValidationError, match="missing cause"):
        validate_core_trace(tuple(records))


def test_trace_validation_rejects_selection_closure_reason_disagreement() -> None:
    term = Handle(
        Handle(
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let(
                            "x",
                            Perform("eff.outer", Lit("ask")),
                            Return(Var("x")),
                        ),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
    )
    result = run_trace(elaborate(term))
    bad_trace = tuple(
        replace(record, reason="abandoned") if isinstance(record, SelectionClosed) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="reason disagrees"):
        validate_core_trace(bad_trace)


def test_trace_validation_rejects_closure_caused_before_path_opened() -> None:
    first = Handle(
        Perform("eff.a", Lit("first")),
        HandlerEnv((install("eff.a", Return(Lit("first-answer"))),)),
    )
    second = Handle(
        Perform("eff.a", Lit("second")),
        HandlerEnv((install("eff.a", Return(Lit("second-answer"))),)),
    )
    term = Let(
        "x",
        first,
        Let(
            "y",
            second,
            Return(Var("y")),
        ),
    )
    result = run_trace(elaborate(term))
    records = list(result.trace)
    captures = [(idx, record) for idx, record in enumerate(records) if isinstance(record, EffectCapture)]
    assert len(captures) == 2
    first_capture = captures[0][1]
    second_idx, second_capture = captures[1]
    records[second_idx] = SelectionClosed(
        ref="selection-closed:bad",
        selection_ref=second_capture.selection_ref,
        selection_path_ref=second_capture.selection_path_ref,
        branch_ref=second_capture.branch_ref,
        reason="abandoned",
        caused_by_ref=first_capture.ref,
        caused_by_record_type="EffectCapture",
        closed_by_selection_ref=first_capture.selection_ref,
        closed_by_selection_path_ref=first_capture.selection_path_ref,
    )

    with pytest.raises(TraceValidationError, match="after closed path opens"):
        validate_core_trace(tuple(records))


def test_trace_validation_rejects_unrelated_selection_closure_cause() -> None:
    first = Handle(
        Perform("eff.a", Lit("first")),
        HandlerEnv((install("eff.a", Return(Lit("first-answer")), "h.first"),)),
    )
    second = Handle(
        Perform("eff.b", Lit("second")),
        HandlerEnv((install("eff.b", Return(Lit("second-answer")), "h.second"),)),
    )
    term = Let("x", first, Let("y", second, Return(Var("y"))))
    result = run_trace(elaborate(term))
    records = list(result.trace)
    captures = [(idx, record) for idx, record in enumerate(records) if isinstance(record, EffectCapture)]
    assert len(captures) == 2
    first_idx, first_capture = captures[0]
    _, second_capture = captures[1]
    records.pop(first_idx)

    corrupted = tuple(
        records
        + [
            SelectionClosed(
                ref="selection-closed:unrelated",
                selection_ref=first_capture.selection_ref,
                selection_path_ref=first_capture.selection_path_ref,
                branch_ref="branch:root",
                reason="abandoned",
                caused_by_ref=second_capture.ref,
                caused_by_record_type="EffectCapture",
                closed_by_selection_ref=second_capture.selection_ref,
                closed_by_selection_path_ref=second_capture.selection_path_ref,
            )
        ]
    )

    with pytest.raises(TraceValidationError, match="not dynamically nested"):
        validate_core_trace(corrupted)


def test_trace_validation_rejects_context_alias_without_control_ancestry() -> None:
    first = Handle(
        Perform("eff.a", Lit("first")),
        HandlerEnv((install("eff.a", Return(Lit("first-answer")), "h.first"),)),
    )
    second = Handle(
        Perform("eff.b", Lit("second")),
        HandlerEnv((install("eff.b", Return(Lit("second-answer")), "h.second"),)),
    )
    term = Let("x", first, Let("y", second, Return(Var("y"))))
    result = run_trace(elaborate(term))
    records = list(result.trace)
    selections = [(idx, record) for idx, record in enumerate(records) if isinstance(record, HandlerSelection)]
    captures = [(idx, record) for idx, record in enumerate(records) if isinstance(record, EffectCapture)]
    assert len(selections) == 2
    assert len(captures) == 2
    first_selection_idx, first_selection = selections[0]
    second_selection = selections[1][1]
    first_capture_idx, first_capture = captures[0]
    _, second_capture = captures[1]

    records[first_selection_idx] = replace(
        first_selection,
        handler_context_ref=second_selection.worker_context_ref,
    )
    records.pop(first_capture_idx)

    corrupted = tuple(
        records
        + [
            SelectionClosed(
                ref="selection-closed:context-alias",
                selection_ref=first_capture.selection_ref,
                selection_path_ref=first_capture.selection_path_ref,
                branch_ref="branch:root",
                reason="abandoned",
                caused_by_ref=second_capture.ref,
                caused_by_record_type="EffectCapture",
                closed_by_selection_ref=second_capture.selection_ref,
                closed_by_selection_path_ref=second_capture.selection_path_ref,
            )
        ]
    )

    with pytest.raises(TraceValidationError, match="not dynamically nested"):
        validate_core_trace(corrupted)


def test_completed_trace_rejects_selection_without_resumption_handle() -> None:
    trace = (
        EffectDeclaration(
            ref="declaration:0",
            program_ref="program:example",
            effect_kind="eff.a",
            payload=None,
            full_continuation_ref="kont:root",
            branch_ref="branch:root",
            payload_schema_ref=None,
            operation_result_schema_ref=None,
            execution_context_ref="ctx:worker",
        ),
        HandlerSelection(
            ref="selection:0",
            declaration_ref="declaration:0",
            selected_binding_ref="install:0",
            handler_id="h.v1",
            handler_frame_ref="handler-env:0",
            captured_continuation_ref="kont:captured",
            outer_continuation_ref="kont:outer",
            captured_continuation_control_ref="continuation-control:captured",
            outer_continuation_control_ref="continuation-control:outer",
            handled_result_schema_ref="schema:handled",
            worker_context_ref="ctx:worker",
            handler_context_ref="ctx:handler",
            outer_context_ref="ctx:outer",
        ),
    )

    validate_core_trace_prefix(trace)
    validate_core_a_trace_prefix(trace)
    with pytest.raises(TraceValidationError, match="ResumptionHandle"):
        validate_core_trace(trace)


def test_trace_validation_rejects_duplicate_terminal_record_ref() -> None:
    term = Handle(
        Let(
            "x",
            Perform("eff.a", Lit("first")),
            Let("y", Perform("eff.a", Lit("second")), Return(Var("y"))),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("R")), Return(Var("r"))),
                ),
            )
        ),
    )
    result = run_trace(elaborate(term))
    captures = [(idx, record) for idx, record in enumerate(result.trace) if isinstance(record, EffectCapture)]
    assert len(captures) == 2
    records = list(result.trace)
    records[captures[1][0]] = replace(captures[1][1], ref=captures[0][1].ref)

    with pytest.raises(TraceValidationError, match="duplicate trace record ref"):
        validate_core_trace(tuple(records))


def test_trace_validation_rejects_duplicate_selection_for_declaration() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    result = run_trace(elaborate(term))
    records = list(result.trace)
    selection = next(record for record in records if isinstance(record, HandlerSelection))
    handle = next(record for record in records if isinstance(record, ResumptionHandle))
    resume = next(record for record in records if isinstance(record, ContinuationResume))
    resume_return = next(record for record in records if isinstance(record, ResumeReturn))
    capture = next(record for record in records if isinstance(record, EffectCapture))

    duplicate_selection_ref = "selection:duplicate"
    duplicate_handle_ref = "resumption:duplicate"
    duplicate_path_ref = f"path:{duplicate_selection_ref}/{duplicate_handle_ref}/branch:root"
    corrupted = tuple(
        records
        + [
            replace(selection, ref=duplicate_selection_ref),
            replace(
                handle,
                ref=duplicate_handle_ref,
                selection_ref=duplicate_selection_ref,
            ),
            replace(
                resume,
                ref="resume:duplicate",
                source_ref=duplicate_handle_ref,
                selection_ref=duplicate_selection_ref,
                selection_path_ref=duplicate_path_ref,
            ),
            replace(
                resume_return,
                ref="resume-return:duplicate",
                resume_ref="resume:duplicate",
                selection_ref=duplicate_selection_ref,
                selection_path_ref=duplicate_path_ref,
            ),
            replace(
                capture,
                ref="capture:duplicate",
                selection_ref=duplicate_selection_ref,
                selection_path_ref=duplicate_path_ref,
            ),
        ]
    )

    with pytest.raises(TraceValidationError, match="duplicate handler selections"):
        validate_core_trace(corrupted)


def test_trace_validation_rejects_duplicate_resume_for_one_shot_handle() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    result = run_trace(elaborate(term))
    records = list(result.trace)
    resume = next(record for record in records if isinstance(record, ContinuationResume))
    resume_return = next(record for record in records if isinstance(record, ResumeReturn))
    capture_idx = next(idx for idx, record in enumerate(records) if isinstance(record, EffectCapture))
    corrupted = tuple(
        records[:capture_idx]
        + [
            replace(resume, ref="resume:duplicate"),
            replace(
                resume_return,
                ref="resume-return:duplicate",
                resume_ref="resume:duplicate",
            ),
        ]
        + records[capture_idx:]
    )

    with pytest.raises(TraceValidationError, match="one-shot core"):
        validate_core_trace(corrupted)


def test_trace_validation_rejects_non_root_branch_refs() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))
    corrupted = tuple(
        replace(record, branch_ref="branch:alternate") if isinstance(record, EffectDeclaration) else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="single-root core"):
        validate_core_trace(corrupted)


def test_program_aware_trace_validation_rejects_semantic_corruption() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    kernel = elaborate(term)
    result = run_trace(kernel)
    corrupted = tuple(
        replace(record, selected_binding_ref="install:wrong") if isinstance(record, HandlerSelection) else record
        for record in result.trace
    )

    validate_core_trace(corrupted)
    with pytest.raises(TraceValidationError, match="program-generated"):
        validate_generated_trace_against_program(kernel, corrupted)


def test_program_refs_distinguish_different_outer_continuation_code() -> None:
    def program(final_value: str):
        return Let(
            "x",
            Handle(
                Perform("eff.a", Lit(None)),
                HandlerEnv((install("eff.a", Return(Lit("handled"))),)),
            ),
            Return(Lit(final_value)),
        )

    left_kernel = elaborate(program("left"))
    right_kernel = elaborate(program("right"))
    left = run_trace(left_kernel, include_debug_evidence=True)
    right = run_trace(right_kernel, include_debug_evidence=True)

    assert left.outcome == Completed("left")
    assert right.outcome == Completed("right")
    assert left.trace != right.trace

    left_declaration = next(record for record in left.trace if isinstance(record, EffectDeclaration))
    right_declaration = next(record for record in right.trace if isinstance(record, EffectDeclaration))
    assert left_declaration.program_ref != right_declaration.program_ref
    assert (
        left.require_debug_evidence().continuation_ref_map[left_declaration.full_continuation_ref]
        != right.require_debug_evidence().continuation_ref_map[right_declaration.full_continuation_ref]
    )

    with pytest.raises(TraceValidationError, match="program-generated"):
        validate_generated_trace_against_program(right_kernel, left.trace, include_debug_evidence=True)


def test_program_refs_distinguish_payload_schema_identity() -> None:
    term = Perform("eff.a", Lit("payload"))
    string_payload = EffectRegistry()
    string_payload.register(EffectSignature("eff.a", TypeSchema(str), AnySchema()))
    any_payload = EffectRegistry()
    any_payload.register(EffectSignature("eff.a", AnySchema(), AnySchema()))

    string_kernel = elaborate(term, registry=string_payload)
    any_kernel = elaborate(term, registry=any_payload)
    string_trace = run_trace(string_kernel).trace
    any_trace = run_trace(any_kernel).trace

    assert string_trace != any_trace
    assert string_trace[0].program_ref != any_trace[0].program_ref
    with pytest.raises(TraceValidationError, match="program-generated"):
        validate_generated_trace_against_program(any_kernel, string_trace, completed=False)


def test_program_refs_distinguish_operation_result_schema_identity() -> None:
    term = Perform("eff.a", Lit("payload"))
    int_result = EffectRegistry()
    int_result.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    string_result = EffectRegistry()
    string_result.register(EffectSignature("eff.a", AnySchema(), TypeSchema(str)))

    int_kernel = elaborate(term, registry=int_result)
    string_kernel = elaborate(term, registry=string_result)
    int_trace = run_trace(int_kernel).trace
    string_trace = run_trace(string_kernel).trace

    assert int_trace != string_trace
    assert int_trace[0].program_ref != string_trace[0].program_ref
    with pytest.raises(TraceValidationError, match="program-generated"):
        validate_generated_trace_against_program(string_kernel, int_trace, completed=False)


def test_program_refs_distinguish_handled_result_schema_identity() -> None:
    def program(handled_result_schema):
        return Handle(
            Perform("eff.a", Lit(None)),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="h.v1",
                        handled_result_schema=handled_result_schema,
                        payload_name="_payload",
                        body=Return(Lit("ok")),
                    ),
                )
            ),
        )

    any_kernel = elaborate(program(AnySchema()))
    string_kernel = elaborate(program(TypeSchema(str)))
    any_trace = run_trace(any_kernel).trace
    string_trace = run_trace(string_kernel).trace

    any_declaration = next(record for record in any_trace if isinstance(record, EffectDeclaration))
    string_declaration = next(record for record in string_trace if isinstance(record, EffectDeclaration))
    assert any_trace != string_trace
    assert any_declaration.program_ref != string_declaration.program_ref
    with pytest.raises(TraceValidationError, match="program-generated"):
        validate_generated_trace_against_program(string_kernel, any_trace)


def test_program_aware_trace_validation_accepts_initial_suspension_prefix() -> None:
    term = Let("x", Perform("eff.unhandled", Lit("payload")), Return(Var("x")))
    kernel = elaborate(term)
    result = run_trace(kernel)

    assert isinstance(result.outcome, Suspended)
    with pytest.raises(TraceValidationError, match="unselected declarations"):
        validate_core_trace(result.trace)
    with pytest.raises(TraceValidationError, match="did not complete"):
        validate_generated_trace_against_program(kernel, result.trace)
    validate_generated_trace_against_program(kernel, result.trace, completed=False)
