import json
from dataclasses import fields

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.context import ExecutionContext
from shepherd_kernel_v3_reference.kernel.continuation_objects import (
    CONTINUATION_CONTROL_DAG_SCHEMA_VERSION,
    CONTINUATION_OBJECT_SCHEMA_VERSION,
    BindFramePayload,
    ContinuationControlIdentity,
    ContinuationEmptyStack,
    ContinuationEnvEmpty,
    ContinuationFrameNode,
    ContinuationFrameSummary,
    ContinuationObjectBuilder,
    ContinuationRoot,
    ContinuationStackConcat,
    ContinuationStackCursor,
    ContinuationStackSummary,
    HandlerFramePayload,
    HandlerReturnFramePayload,
    KernelContinuationObjectProjector,
    continuation_control_identity_ref,
    continuation_frame_payload_child_refs,
    continuation_frame_payload_child_roles,
    continuation_object_from_json,
    continuation_object_ref,
    continuation_object_to_json,
)
from shepherd_kernel_v3_reference.kernel.frame_state import BindFrame
from shepherd_kernel_v3_reference.kernel.recursive_machine import RecursiveKernelEvaluator
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.source.values import Env
from shepherd_kernel_v3_reference.trace.machine import record_from_event, run_trace
from shepherd_kernel_v3_reference.trace.validate import validate_core_trace, validate_runtime_trace

_EMPTY_ENV_REF = continuation_object_ref(ContinuationEnvEmpty())


def test_continuation_object_store_refs_and_round_trips_json() -> None:
    builder = ContinuationObjectBuilder()
    frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:0",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:0",
            context=_context_payload(),
        )
    )
    stack = builder.push_frame(frame_ref, builder.empty_stack)
    root_ref = builder.put_root(
        stack,
        program_ref="program:0",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref="ctx:entry",
        execution_context=_context_payload(),
        result_schema_ref=None,
    )

    assert frame_ref.startswith("continuation-object:sha256:")
    assert root_ref.startswith("continuation-object:sha256:")
    assert isinstance(builder.store.get(builder.empty_stack_ref), ContinuationEmptyStack)
    assert isinstance(builder.store.get(root_ref), ContinuationRoot)
    assert builder.store.get(frame_ref).summary.code_identity_refs == ("binder:0",)
    assert stack.summary.depth == 1

    snapshot = builder.store.snapshot((root_ref,))
    assert list(snapshot) == sorted(snapshot)
    assert set(snapshot) == {builder.empty_env_ref, builder.empty_stack_ref, frame_ref, stack.stack_ref, root_ref}
    with pytest.raises(TypeError, match="not a ContinuationRoot"):
        builder.store.snapshot((frame_ref,))

    for ref, obj in snapshot.items():
        encoded = continuation_object_to_json(obj)
        decoded = continuation_object_from_json(json.loads(json.dumps(encoded)))
        assert continuation_object_ref(decoded) == ref
        assert decoded == obj

    frame = builder.store.get(frame_ref)
    assert isinstance(frame, ContinuationFrameNode)
    with pytest.raises(TypeError, match="immutable"):
        frame.payload.context["binding_env_ref"] = "env:changed"  # type: ignore[index]


def test_push_frame_stack_cursor_cache_preserves_validation() -> None:
    builder = ContinuationObjectBuilder()
    frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:cache",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:cache",
            context=_context_payload(),
        )
    )

    first = builder.push_frame(frame_ref, builder.empty_stack)
    second = builder.push_frame(frame_ref, builder.empty_stack)

    assert second == first
    assert builder._diagnostics.stack_cursor_cache_hits == 1
    assert builder._diagnostics.stack_cursor_cache_misses == 1
    assert builder._diagnostics.stack_cursor_cache_misses == len(builder._stack_cursor_cache)

    stale_tail = ContinuationStackCursor(
        stack_ref=builder.empty_stack.stack_ref,
        summary=ContinuationStackSummary(
            depth=builder.empty_stack.summary.depth + 1,
            required_schema_refs=builder.empty_stack.summary.required_schema_refs,
            code_identity_refs=builder.empty_stack.summary.code_identity_refs,
        ),
    )
    with pytest.raises(ValueError, match="ContinuationStackCursor.summary"):
        builder.push_frame(frame_ref, stale_tail)


def test_concat_stack_cursor_names_prefix_over_tail_without_replaying_nodes() -> None:
    builder = ContinuationObjectBuilder()
    prefix_frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:prefix",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:prefix",
            context=_context_payload(),
        )
    )
    tail_frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:tail",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:tail",
            context=_context_payload(),
        )
    )
    prefix = builder.push_frame(prefix_frame_ref, builder.empty_stack)
    tail = builder.push_frame(tail_frame_ref, builder.empty_stack)

    concat = builder.concat_stack(prefix, tail)
    cached = builder.concat_stack(prefix, tail)

    assert cached == concat
    assert concat.summary.depth == 2
    assert concat.summary.code_identity_refs == ()
    concat_obj = builder.store.get(concat.stack_ref)
    assert isinstance(concat_obj, ContinuationStackConcat)
    assert concat_obj.prefix_stack_ref == prefix.stack_ref
    assert concat_obj.tail_stack_ref == tail.stack_ref
    assert builder.concat_stack(builder.empty_stack, tail) == tail
    assert builder.concat_stack(prefix, builder.empty_stack) == prefix

    encoded = continuation_object_to_json(concat_obj)
    decoded = continuation_object_from_json(json.loads(json.dumps(encoded)))
    assert decoded == concat_obj
    assert continuation_object_ref(decoded) == concat.stack_ref


def test_snapshot_reachable_subgraph_uses_explicit_child_ref_table() -> None:
    builder = ContinuationObjectBuilder()
    selected_handler_ref = builder.put_frame(
        HandlerFramePayload(
            handler_env_ref="handler-env:0",
            handler_env_def_ref="handler-env-def:0",
            region_ref="region:root",
            env_ref=builder.empty_env_ref,
            entry_context_ref="ctx:entry",
            entry_context=_context_payload(),
            outer_context_ref="ctx:outer",
            outer_context=_context_payload(),
        )
    )
    captured = builder.empty_stack
    outer_frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:outer",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:outer",
            context=_context_payload(),
        )
    )
    outer = builder.push_frame(outer_frame_ref, builder.empty_stack)
    handler_return_ref = builder.put_frame(
        HandlerReturnFramePayload(
            captured_stack_ref=captured.stack_ref,
            selected_handler_frame_ref=selected_handler_ref,
            outer_stack_ref=outer.stack_ref,
            install_ref="install:0",
            install_def_ref="install-def:0",
            handler_binding_env_ref=builder.empty_env_ref,
            worker_context_ref="ctx:worker",
            worker_context=_context_payload(),
            handler_context_ref="ctx:handler",
            handler_context=_context_payload(),
            outer_context_ref="ctx:outer",
            outer_context=_context_payload(),
            declaration_ref="declaration:0",
            selection_ref="selection:0",
            resumption_handle_ref="resumption:0",
            selection_path_ref="path:selection:0/resumption:0/branch:root",
            captured_continuation_control_ref="continuation-control:captured",
            outer_continuation_control_ref="continuation-control:outer",
            operation_result_schema_ref="schema:operation",
            handled_result_schema_ref="schema:handled",
        )
    )
    stack = builder.push_frame(handler_return_ref, builder.empty_stack)
    root_ref = builder.put_root(
        stack,
        program_ref="program:0",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="captured-worker",
        execution_context_ref="ctx:entry",
        execution_context=_context_payload(),
        result_schema_ref="schema:handled",
    )

    handler_frame = builder.store.get(handler_return_ref)
    assert isinstance(handler_frame, ContinuationFrameNode)
    assert continuation_frame_payload_child_refs(handler_frame.payload) == (
        captured.stack_ref,
        selected_handler_ref,
        outer.stack_ref,
        builder.empty_env_ref,
        builder.empty_env_ref,
        builder.empty_env_ref,
        builder.empty_env_ref,
    )
    assert continuation_frame_payload_child_roles(handler_frame.payload) == (
        (captured.stack_ref, "stack"),
        (selected_handler_ref, "frame"),
        (outer.stack_ref, "stack"),
        (builder.empty_env_ref, "env"),
        (builder.empty_env_ref, "env"),
        (builder.empty_env_ref, "env"),
        (builder.empty_env_ref, "env"),
    )
    assert handler_frame.summary.required_schema_refs == ("schema:handled", "schema:operation")
    assert handler_frame.summary.code_identity_refs == ("install-def:0",)

    unrelated_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:unrelated",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:unrelated",
            context=_context_payload(),
        )
    )
    snapshot = builder.store.snapshot((root_ref,))
    assert unrelated_ref not in snapshot
    assert {captured.stack_ref, selected_handler_ref, outer_frame_ref, outer.stack_ref, handler_return_ref} <= set(
        snapshot
    )


def test_builder_rejects_stack_child_ref_that_resolves_to_frame() -> None:
    builder = ContinuationObjectBuilder()
    wrong_stack_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:not-stack",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:not-stack",
            context=_context_payload(),
        )
    )

    with pytest.raises(TypeError, match="not a stack"):
        builder.put_frame(_handler_return_payload(builder, captured_stack_ref=wrong_stack_ref))


def test_builder_rejects_frame_child_ref_that_resolves_to_stack() -> None:
    builder = ContinuationObjectBuilder()

    with pytest.raises(TypeError, match="not a frame"):
        builder.put_frame(_handler_return_payload(builder, selected_handler_frame_ref=builder.empty_stack_ref))


def test_builder_rejects_root_ref_as_frame_payload_child() -> None:
    builder = ContinuationObjectBuilder()
    root_ref = builder.put_root(
        builder.empty_stack,
        program_ref="program:0",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref="ctx:entry",
        execution_context=_context_payload(),
        result_schema_ref=None,
    )

    with pytest.raises(TypeError, match="not a stack"):
        builder.put_frame(_handler_return_payload(builder, captured_stack_ref=root_ref))


def test_builder_rejects_frame_payload_env_refs_that_do_not_resolve_to_env_objects() -> None:
    builder = ContinuationObjectBuilder()

    with pytest.raises(TypeError, match="not an env"):
        builder.put_frame(
            BindFramePayload(
                binder_ref="binder:not-env",
                env_ref=builder.empty_stack_ref,
                context_ref="ctx:not-env",
                context=_context_payload(),
            )
        )

    with pytest.raises(KeyError):
        builder.put_frame(
            BindFramePayload(
                binder_ref="binder:missing-env",
                env_ref="continuation-object:sha256:missing-env",
                context_ref="ctx:missing-env",
                context=_context_payload(),
            )
        )

    with pytest.raises(KeyError):
        builder.put_frame(
            BindFramePayload(
                binder_ref="binder:missing-context-env",
                env_ref=builder.empty_env_ref,
                context_ref="ctx:missing-context-env",
                context=_context_payload(binding_env_ref="continuation-object:sha256:missing-context-env"),
            )
        )


def test_builder_rejects_stack_cursor_that_resolves_to_frame() -> None:
    builder = ContinuationObjectBuilder()
    frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:not-stack",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:not-stack",
            context=_context_payload(),
        )
    )
    forged_tail = ContinuationStackCursor(stack_ref=frame_ref, summary=builder.empty_stack.summary)

    with pytest.raises(TypeError, match="not a stack"):
        builder.push_frame(frame_ref, forged_tail)


def test_builder_rejects_stack_cursor_that_resolves_to_root() -> None:
    builder = ContinuationObjectBuilder()
    root_ref = builder.put_root(
        builder.empty_stack,
        program_ref="program:0",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref="ctx:entry",
        execution_context=_context_payload(),
        result_schema_ref=None,
    )
    forged_stack = ContinuationStackCursor(stack_ref=root_ref, summary=builder.empty_stack.summary)

    with pytest.raises(TypeError, match="not a stack"):
        builder.put_root(
            forged_stack,
            program_ref="program:0",
            branch_ref="branch:root",
            branch_scope_ref=None,
            continuation_kind="full",
            execution_context_ref="ctx:entry",
            execution_context=_context_payload(),
            result_schema_ref=None,
        )


def test_builder_rejects_stack_cursor_that_resolves_to_missing_ref() -> None:
    builder = ContinuationObjectBuilder()
    forged_stack = ContinuationStackCursor(
        stack_ref="continuation-object:missing",
        summary=builder.empty_stack.summary,
    )

    with pytest.raises(KeyError):
        builder.put_control_identity(
            forged_stack,
            program_ref="program:0",
            branch_ref="branch:root",
            branch_scope_ref=None,
        )


def test_builder_rejects_stack_cursor_with_stale_summary() -> None:
    builder = ContinuationObjectBuilder()
    frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:0",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:0",
            context=_context_payload(),
        )
    )
    stack = builder.push_frame(frame_ref, builder.empty_stack)
    forged_stack = ContinuationStackCursor(
        stack_ref=stack.stack_ref,
        summary=ContinuationStackSummary(),
    )

    with pytest.raises(ValueError, match="summary does not match"):
        builder.put_root(
            forged_stack,
            program_ref="program:0",
            branch_ref="branch:root",
            branch_scope_ref=None,
            continuation_kind="full",
            execution_context_ref="ctx:entry",
            execution_context=_context_payload(),
            result_schema_ref=None,
        )


def test_root_rejects_unknown_continuation_kind() -> None:
    with pytest.raises(ValueError, match="unknown ContinuationRoot.continuation_kind"):
        ContinuationRoot(
            program_ref="program:0",
            branch_ref="branch:root",
            branch_scope_ref=None,
            position="value",
            continuation_kind="not-a-kind",  # type: ignore[arg-type]
            execution_context_ref="ctx:entry",
            execution_context=_context_payload(),
            result_schema_ref=None,
            stack_ref="continuation-object:stack",
        )


def test_root_json_decode_rejects_unknown_continuation_kind() -> None:
    builder = ContinuationObjectBuilder()
    root_ref = builder.put_root(
        builder.empty_stack,
        program_ref="program:0",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref="ctx:entry",
        execution_context=_context_payload(),
        result_schema_ref=None,
    )
    root_json = continuation_object_to_json(builder.store.get(root_ref))
    root_json["continuation_kind"] = "not-a-kind"

    with pytest.raises(ValueError, match="unknown ContinuationRoot.continuation_kind"):
        continuation_object_from_json(root_json)


def test_incremental_builder_projects_large_stack_without_hot_path_scans() -> None:
    builder = ContinuationObjectBuilder()
    stack = builder.empty_stack
    roots: list[str] = []

    for idx in range(50):
        frame_ref = builder.put_frame(
            BindFramePayload(
                binder_ref=f"binder:{idx}",
                env_ref=builder.empty_env_ref,
                context_ref=f"ctx:{idx}",
                context=_context_payload(),
            )
        )
        stack = builder.push_frame(frame_ref, stack)
        roots.append(
            builder.put_root(
                stack,
                program_ref="program:50-effect-style",
                branch_ref="branch:root",
                branch_scope_ref=None,
                continuation_kind="full",
                execution_context_ref=f"ctx:{idx}",
                execution_context=_context_payload(),
                result_schema_ref=None,
            )
        )

    assert builder.stats.full_stack_tuple_scans == 0
    assert builder.stats.reachable_summary_walks == 0
    assert stack.summary.depth == 50
    assert stack.summary.code_identity_refs == ()
    assert len(builder.store.snapshot()) == 152
    assert max(_json_size(continuation_object_to_json(obj)) for _, obj in builder.store.items()) < 2_000
    assert isinstance(builder.store.get(roots[-1]), ContinuationRoot)


def test_snapshot_reachable_subgraph_handles_deep_stack_without_recursion_failure() -> None:
    builder = ContinuationObjectBuilder()
    stack = builder.empty_stack

    for idx in range(1_100):
        frame_ref = builder.put_frame(
            BindFramePayload(
                binder_ref=f"binder:{idx}",
                env_ref=builder.empty_env_ref,
                context_ref=f"ctx:{idx}",
                context=_context_payload(),
            )
        )
        stack = builder.push_frame(frame_ref, stack)

    root_ref = builder.put_root(
        stack,
        program_ref="program:deep",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref="ctx:entry",
        execution_context=_context_payload(),
        result_schema_ref=None,
    )
    snapshot = builder.store.snapshot((root_ref,))

    assert root_ref in snapshot
    assert len(snapshot) == 1 + 1 + 1 + (1_100 * 2)


def test_summary_ref_tuples_are_sorted_and_deduplicated() -> None:
    frame_summary = ContinuationFrameSummary(
        required_schema_refs=("schema:z", "schema:a", "schema:a"),
        code_identity_refs=("binder:b", "binder:a", "binder:b"),
    )
    stack_summary = ContinuationStackSummary(
        depth=2,
        required_schema_refs=("schema:b", "schema:a", "schema:b"),
        code_identity_refs=("handler:z", "handler:a", "handler:z"),
    )

    assert frame_summary.required_schema_refs == ("schema:a", "schema:z")
    assert frame_summary.code_identity_refs == ("binder:a", "binder:b")
    assert stack_summary.required_schema_refs == ("schema:a", "schema:b")
    assert stack_summary.code_identity_refs == ("handler:a", "handler:z")


def test_builder_stack_summary_aggregation_is_canonical() -> None:
    builder = ContinuationObjectBuilder()
    stack = builder.empty_stack
    for binder_ref, idx in (("binder:b", 1), ("binder:a", 2), ("binder:b", 3)):
        frame_ref = builder.put_frame(
            BindFramePayload(
                binder_ref=binder_ref,
                env_ref=builder.empty_env_ref,
                context_ref=f"ctx:{idx}",
                context=_context_payload(),
            )
        )
        stack = builder.push_frame(frame_ref, stack)

    assert stack.summary.depth == 3
    assert stack.summary.code_identity_refs == ()


def test_kernel_evaluator_frame_cache_rejects_stale_id_entry() -> None:
    evaluator, frame, stale_frame = _evaluator_with_bind_frames()
    stale_ref = "continuation-object:sha256:stale"
    evaluator._continuation_frame_cache[id(frame)] = (stale_frame, stale_ref)

    ref = evaluator._continuation_frame_ref(frame)

    assert ref != stale_ref
    assert evaluator._continuation_frame_cache[id(frame)] == (frame, ref)


def test_kernel_projector_frame_cache_rejects_stale_id_entry() -> None:
    evaluator, frame, stale_frame = _evaluator_with_bind_frames()
    projector = KernelContinuationObjectProjector(evaluator)
    stale_ref = "continuation-object:sha256:stale"
    projector._frame_cache[id(frame)] = (stale_frame, stale_ref)

    ref = projector.project_frame(frame)

    assert ref != stale_ref
    assert projector._frame_cache[id(frame)] == (frame, ref)


def test_control_identity_ref_is_compact_stack_identity() -> None:
    builder = ContinuationObjectBuilder()
    identity = ContinuationControlIdentity(
        program_ref="program:0",
        branch_ref="branch:root",
        branch_scope_ref=None,
        position="value",
        stack_ref=builder.empty_stack.stack_ref,
    )

    assert identity.control_schema_version == CONTINUATION_CONTROL_DAG_SCHEMA_VERSION
    assert continuation_control_identity_ref(identity).startswith("continuation-control:sha256:")
    assert "continuation_kind" not in identity.__dict__
    assert "execution_context" not in identity.__dict__


def test_shadow_projection_preserves_existing_trace_records() -> None:
    program = _sequential_handled_effect_program(2)

    baseline = run_trace(program, include_debug_evidence=True)

    assert baseline.outcome == Completed("value")
    validate_core_trace(baseline.trace)
    validate_runtime_trace(baseline.trace)

    trace_refs = _trace_continuation_refs(baseline.trace)
    evidence = baseline.require_debug_evidence()
    assert trace_refs <= set(evidence.continuation_ref_map)
    assert set(evidence.continuation_root_refs) >= {evidence.continuation_ref_map[ref] for ref in trace_refs}
    for root_ref in trace_refs:
        root = evidence.get_continuation_object(root_ref)
        assert isinstance(root, ContinuationRoot)
        assert root.object_schema_version == CONTINUATION_OBJECT_SCHEMA_VERSION


def test_kernel_evaluator_pairs_frame_tuples_with_incremental_stack_cursors() -> None:
    program = _sequential_handled_effect_program(5)
    builder = ContinuationObjectBuilder()
    records = []
    evaluator = RecursiveKernelEvaluator(
        program,
        event_sink=lambda event: records.append(record_from_event(event)),
        continuation_builder=builder,
    )

    outcome = evaluator.run()

    assert outcome == Completed("value")
    validate_runtime_trace(tuple(records))
    assert builder.stats.full_stack_tuple_scans == 0
    assert builder.stats.reachable_summary_walks == 0
    assert _trace_continuation_refs(records) <= set(evaluator.continuation_objects)


class _ShadowProjectionEvaluator(RecursiveKernelEvaluator):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.projector = KernelContinuationObjectProjector(self)
        self.aliases: dict[str, str] = {}

    @property
    def continuation_objects(self):
        return self.projector.store.snapshot()

    def _kont_ref(self, kont, *, continuation_kind, context, result_schema_ref=None):
        root_ref = self.projector.project_root(
            kont.frames,
            continuation_kind=continuation_kind,
            context=context,
            result_schema_ref=result_schema_ref,
        )
        legacy_ref = super()._kont_ref(
            kont,
            continuation_kind=continuation_kind,
            context=context,
            result_schema_ref=result_schema_ref,
        )
        self.aliases[legacy_ref] = root_ref
        return legacy_ref

    def _kont_control_ref(self, kont):
        self.projector.project_control_ref(kont.frames)
        return super()._kont_control_ref(kont)


class _ShadowProjectionResult:
    def __init__(self, *, outcome, trace, continuation_objects, aliases) -> None:
        self.outcome = outcome
        self.trace = trace
        self.continuation_objects = continuation_objects
        self.aliases = aliases


def _run_shadow_projection(program):
    records = []
    evaluator = _ShadowProjectionEvaluator(
        program,
        event_sink=lambda event: records.append(record_from_event(event)),
    )
    outcome = evaluator.run()
    return _ShadowProjectionResult(
        outcome=outcome,
        trace=tuple(records),
        continuation_objects=evaluator.continuation_objects,
        aliases=dict(evaluator.aliases),
    )


def _sequential_handled_effect_program(effect_count: int):
    term = Return(Var(f"y{effect_count - 1}")) if effect_count else Return(Lit(None))
    for i in reversed(range(effect_count)):
        term = Let(f"y{i}", Perform("eff.a", Lit({"i": i})), term)
    return elaborate(
        Handle(
            term,
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="continuation-object-test.handler.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )


def _trace_continuation_refs(trace) -> set[str]:
    refs: set[str] = set()
    for record in trace:
        for field in fields(record):
            if field.name.endswith("continuation_ref"):
                value = getattr(record, field.name)
                if isinstance(value, str):
                    refs.add(value)
    return refs


def _context_payload(
    *,
    binding_env_ref: str = _EMPTY_ENV_REF,
    region_ref: str = "region:root",
    authority_ref: str = "authority:root",
) -> dict[str, str]:
    return {
        "binding_env_ref": binding_env_ref,
        "region_ref": region_ref,
        "authority_ref": authority_ref,
    }


def _evaluator_with_bind_frames() -> tuple[RecursiveKernelEvaluator, BindFrame, BindFrame]:
    program = elaborate(
        Handle(
            Let("x", Perform("eff.cache", Lit(None)), Return(Var("x"))),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.cache",
                        handler_id="cache-test.handler.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )
    binder_id = next(iter(program.binders))
    evaluator = RecursiveKernelEvaluator(program)
    frame = BindFrame(binder_id, Env(), ExecutionContext())
    stale_frame = BindFrame(binder_id, Env((("stale", 1),)), ExecutionContext())
    return evaluator, frame, stale_frame


def _handler_return_payload(
    builder: ContinuationObjectBuilder,
    **overrides: object,
) -> HandlerReturnFramePayload:
    selected_handler_frame_ref = builder.put_frame(
        HandlerFramePayload(
            handler_env_ref="handler-env:0",
            handler_env_def_ref="handler-env-def:0",
            region_ref="region:root",
            env_ref=builder.empty_env_ref,
            entry_context_ref="ctx:entry",
            entry_context=_context_payload(),
            outer_context_ref="ctx:outer",
            outer_context=_context_payload(),
        )
    )
    fields = {
        "captured_stack_ref": builder.empty_stack_ref,
        "selected_handler_frame_ref": selected_handler_frame_ref,
        "outer_stack_ref": builder.empty_stack_ref,
        "install_ref": "install:0",
        "install_def_ref": "install-def:0",
        "handler_binding_env_ref": builder.empty_env_ref,
        "worker_context_ref": "ctx:worker",
        "worker_context": _context_payload(),
        "handler_context_ref": "ctx:handler",
        "handler_context": _context_payload(),
        "outer_context_ref": "ctx:outer",
        "outer_context": _context_payload(),
        "declaration_ref": "declaration:0",
        "selection_ref": "selection:0",
        "resumption_handle_ref": "resumption:0",
        "selection_path_ref": "path:selection:0/resumption:0/branch:root",
        "captured_continuation_control_ref": "continuation-control:captured",
        "outer_continuation_control_ref": "continuation-control:outer",
        "operation_result_schema_ref": "schema:operation",
        "handled_result_schema_ref": "schema:handled",
    }
    fields.update(overrides)
    return HandlerReturnFramePayload(**fields)  # type: ignore[arg-type]


def _json_size(payload: object) -> int:
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
