from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate, elaborate_publication_experimental
from shepherd_kernel_v3_reference.kernel.continuation_objects import (
    BindFramePayload,
    ContinuationEmptyStack,
    ContinuationEnvEmpty,
    ContinuationEnvNode,
    ContinuationFrameNode,
    ContinuationObjectBuilder,
    ContinuationRoot,
    ContinuationStackNode,
    ContinuationStackSummary,
    continuation_object_ref,
)
from shepherd_kernel_v3_reference.kernel.refs import content_ref
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.experimental import TerminalFork
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace import (
    ContinuationResume,
    EffectDeclaration,
    HandlerSelection,
)
from shepherd_kernel_v3_reference.trace.machine import TraceResult, run_trace
from shepherd_kernel_v3_reference.trace.validate import (
    TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    TraceEvidenceBundle,
    TraceValidationError,
    _ContinuationEvidenceValidator,
    validate_trace_evidence,
)

_EMPTY_ENV_REF = continuation_object_ref(ContinuationEnvEmpty())

_CONTINUATION_FIELD_NAMES = (
    "full_continuation_ref",
    "captured_continuation_ref",
    "outer_continuation_ref",
    "continuation_ref",
    "handler_continuation_ref",
    "handler_dynamic_tail_ref",
    "terminal_continuation_ref",
)


def test_validate_trace_evidence_accepts_runtime_trace_with_continuation_objects() -> None:
    result = _trace_result()

    validate_trace_evidence(_bundle(result))


def test_validate_trace_evidence_rejects_publication_experimental_evidence_explicitly() -> None:
    result = run_trace(
        elaborate_publication_experimental(
            Handle(
                Perform("eff.fork", Lit(None)),
                HandlerEnv(
                    (
                        StaticHandlerInstall(
                            effect_kind="eff.fork",
                            handler_id="trace-evidence-publication.handler.v1",
                            handled_result_schema=AnySchema(),
                            payload_name="_payload",
                            body=TerminalFork((("left", Lit("left")), ("right", Lit("right")))),
                        ),
                    )
                ),
            )
        ),
        include_debug_evidence=True,
    )

    with pytest.raises(TraceValidationError, match="publication-experimental continuation evidence artifacts"):
        validate_trace_evidence(_bundle(result))


def test_validate_trace_evidence_accepts_shadowed_bind_frame_env_nodes() -> None:
    result = run_trace(
        elaborate(
            Handle(
                Let(
                    "x",
                    Return(Lit(1)),
                    Let(
                        "x",
                        Return(Lit(2)),
                        Let("y", Perform("eff.shadow", Lit(None)), Return(Var("y"))),
                    ),
                ),
                HandlerEnv(
                    (
                        StaticHandlerInstall(
                            effect_kind="eff.shadow",
                            handler_id="trace-evidence-shadow.handler.v1",
                            handled_result_schema=AnySchema(),
                            payload_name="_payload",
                            body=Resume(Lit("ok")),
                        ),
                    )
                ),
            )
        ),
        include_debug_evidence=True,
    )
    bind_payloads = (
        obj.payload
        for obj in result.require_debug_evidence().continuation_objects.values()
        if isinstance(obj, ContinuationFrameNode) and isinstance(obj.payload, BindFramePayload)
    )

    assert any(
        _env_bindings(result.require_debug_evidence().continuation_objects, payload.env_ref) == (("x", 1), ("x", 2))
        for payload in bind_payloads
    )
    validate_trace_evidence(_bundle(result))


def test_validate_trace_evidence_lifecycle_only_does_not_require_continuation_objects() -> None:
    result = _trace_result()

    validate_trace_evidence(
        TraceEvidenceBundle(
            bundle_schema_version=TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
            trace=result.trace,
            continuation_root_refs=(),
            continuation_objects={},
            validation_profile="lifecycle-only",
        )
    )


@pytest.mark.parametrize(
    ("field_name", "match"),
    [
        ("continuation_root_refs", "continuation_root_refs"),
        ("continuation_objects", "continuation_objects"),
        ("continuation_ref_map", "continuation_ref_map"),
        ("continuation_control_ref_map", "continuation_control_ref_map"),
        ("context_ref_map", "context_ref_map"),
    ],
)
def test_validate_trace_evidence_lifecycle_only_rejects_debug_evidence(field_name: str, match: str) -> None:
    result = _trace_result()
    evidence = result.require_debug_evidence()
    first_object_ref, first_object = next(iter(evidence.continuation_objects.items()))
    bundle_kwargs = {
        "bundle_schema_version": TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "trace": result.trace,
        "continuation_root_refs": (),
        "continuation_objects": {},
        "validation_profile": "lifecycle-only",
        "continuation_ref_map": {},
        "continuation_control_ref_map": {},
        "context_ref_map": {},
    }
    bundle_kwargs[field_name] = {
        "continuation_root_refs": evidence.continuation_root_refs[:1],
        "continuation_objects": {first_object_ref: first_object},
        "continuation_ref_map": dict(evidence.continuation_ref_map),
        "continuation_control_ref_map": dict(evidence.continuation_control_ref_map),
        "context_ref_map": dict(evidence.context_ref_map),
    }[field_name]

    with pytest.raises(TraceValidationError, match=match):
        validate_trace_evidence(TraceEvidenceBundle(**bundle_kwargs))


def test_validate_trace_evidence_rejects_root_set_mismatch() -> None:
    result = _trace_result()

    with pytest.raises(TraceValidationError, match="continuation_root_refs"):
        validate_trace_evidence(
            _bundle(result, continuation_root_refs=result.require_debug_evidence().continuation_root_refs[:-1])
        )


def test_validate_trace_evidence_rejects_missing_root_object() -> None:
    result = _trace_result()
    objects = dict(result.require_debug_evidence().continuation_objects)
    objects.pop(result.require_debug_evidence().continuation_root_refs[0])

    with pytest.raises(TraceValidationError, match="missing"):
        validate_trace_evidence(_bundle(result, continuation_objects=objects))


def test_validate_trace_evidence_rejects_non_root_trace_continuation_ref() -> None:
    result = _trace_result()
    empty_stack_ref = next(
        ref
        for ref, obj in result.require_debug_evidence().continuation_objects.items()
        if isinstance(obj, ContinuationEmptyStack)
    )
    trace = tuple(
        replace(record, full_continuation_ref=empty_stack_ref) if idx == 0 else record
        for idx, record in enumerate(result.trace)
    )
    continuation_ref_map = {
        ref: result.require_debug_evidence().continuation_ref_map.get(ref, ref)
        for ref in _trace_continuation_refs(trace)
    }
    continuation_ref_map[empty_stack_ref] = empty_stack_ref

    with pytest.raises(TraceValidationError, match="ContinuationRoot"):
        validate_trace_evidence(
            _bundle(
                result,
                trace=trace,
                continuation_root_refs=tuple(continuation_ref_map.values()),
                continuation_ref_map=continuation_ref_map,
            )
        )


def test_validate_trace_evidence_rejects_stale_object_map_key() -> None:
    result = _trace_result()
    ref, obj = next(iter(result.require_debug_evidence().continuation_objects.items()))
    objects = dict(result.require_debug_evidence().continuation_objects)
    del objects[ref]
    objects["continuation-object:sha256:stale"] = obj

    with pytest.raises(TraceValidationError, match="does not match content ref"):
        validate_trace_evidence(_bundle(result, continuation_objects=objects))


def test_validate_trace_evidence_rejects_missing_child_object() -> None:
    result = _trace_result()
    objects = dict(result.require_debug_evidence().continuation_objects)
    child_ref = next(ref for ref, obj in objects.items() if isinstance(obj, ContinuationFrameNode))
    del objects[child_ref]

    with pytest.raises(TraceValidationError, match="missing"):
        validate_trace_evidence(_bundle(result, continuation_objects=objects))


def test_validate_trace_evidence_rejects_bad_stack_summary() -> None:
    result = _trace_result()
    objects = dict(result.require_debug_evidence().continuation_objects)
    declaration = next(record for record in result.trace if isinstance(record, EffectDeclaration))
    root_ref = _continuation_evidence_ref(result, declaration.full_continuation_ref)
    root = objects[root_ref]
    assert isinstance(root, ContinuationRoot)
    stack = objects[root.stack_ref]
    assert isinstance(stack, ContinuationStackNode)
    bad_stack = replace(
        stack,
        summary=ContinuationStackSummary(
            depth=stack.summary.depth + 1,
            required_schema_refs=stack.summary.required_schema_refs,
            code_identity_refs=stack.summary.code_identity_refs,
        ),
    )
    bad_stack_ref = continuation_object_ref(bad_stack)
    bad_root = replace(root, stack_ref=bad_stack_ref)
    bad_root_ref = continuation_object_ref(bad_root)
    objects[bad_stack_ref] = bad_stack
    objects[bad_root_ref] = bad_root
    root_refs = _replace_ref_in_tuple(result.require_debug_evidence().continuation_root_refs, root_ref, bad_root_ref)
    continuation_ref_map = {
        **result.require_debug_evidence().continuation_ref_map,
        declaration.full_continuation_ref: bad_root_ref,
    }

    with pytest.raises(TraceValidationError, match="summary"):
        validate_trace_evidence(
            _bundle(
                result,
                continuation_root_refs=root_refs,
                continuation_objects=objects,
                continuation_ref_map=continuation_ref_map,
            )
        )


def test_validator_rechecks_expected_role_for_memoized_object_refs() -> None:
    builder = ContinuationObjectBuilder()
    env_ref = builder.put_env_node(parent_env_ref=builder.empty_env_ref, name="x", value=1, depth=1)
    context = _context_payload(binding_env_ref=env_ref)
    frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:role-memo",
            env_ref=env_ref,
            context_ref=content_ref("ctx", context),
            context=context,
        )
    )
    stack = builder.push_frame(frame_ref, builder.empty_stack)
    good_root_ref = builder.put_root(
        stack,
        program_ref="program:role-memo",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref=content_ref("ctx", _context_payload()),
        execution_context=_context_payload(),
        result_schema_ref=None,
    )
    bad_root = ContinuationRoot(
        program_ref="program:role-memo",
        branch_ref="branch:root",
        branch_scope_ref=None,
        position="value",
        continuation_kind="full",
        execution_context_ref=content_ref("ctx", _context_payload()),
        execution_context=_context_payload(),
        result_schema_ref=None,
        stack_ref=frame_ref,
    )
    bad_root_ref = builder.store.put(bad_root)
    validator = _ContinuationEvidenceValidator(builder.store.snapshot())

    validator.validate_root(good_root_ref)
    with pytest.raises(TraceValidationError, match="stack object"):
        validator.validate_root(bad_root_ref)


def test_continuation_evidence_validator_caches_repeated_root_stack_control_walks() -> None:
    builder = ContinuationObjectBuilder()
    context = _context_payload()
    frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:cache",
            env_ref=builder.empty_env_ref,
            context_ref=content_ref("ctx", context),
            context=context,
        )
    )
    stack = builder.push_frame(frame_ref, builder.empty_stack)
    root_ref = builder.put_root(
        stack,
        program_ref="program:cache",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref=content_ref("ctx", _context_payload()),
        execution_context=_context_payload(),
        result_schema_ref=None,
    )
    same_stack_root_ref = builder.put_root(
        stack,
        program_ref="program:cache",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="captured-worker",
        execution_context_ref=content_ref("ctx", _context_payload()),
        execution_context=_context_payload(),
        result_schema_ref="schema:worker",
    )
    validator = _ContinuationEvidenceValidator(builder.store.snapshot())

    validator.validate_root(root_ref)
    assert len(validator._validated_frame_control_stacks) == 1

    validator.validate_root(root_ref)
    assert len(validator._validated_frame_control_stacks) == 1

    validator.validate_root(same_stack_root_ref)
    assert len(validator._validated_frame_control_stacks) == 1
    assert set(validator._validated_roots) == {root_ref, same_stack_root_ref}


def test_continuation_evidence_validator_caches_shared_tail_control_walks() -> None:
    builder = ContinuationObjectBuilder()
    context = _context_payload()
    tail = builder.empty_stack
    for idx in range(8):
        frame_ref = builder.put_frame(
            BindFramePayload(
                binder_ref=f"binder:tail:{idx}",
                env_ref=builder.empty_env_ref,
                context_ref=content_ref("ctx", context),
                context=context,
            )
        )
        tail = builder.push_frame(frame_ref, tail)
    root_ref = builder.put_root(
        tail,
        program_ref="program:cache-tail",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="full",
        execution_context_ref=content_ref("ctx", _context_payload()),
        execution_context=_context_payload(),
        result_schema_ref=None,
    )
    prefix_frame_ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:prefix",
            env_ref=builder.empty_env_ref,
            context_ref=content_ref("ctx", context),
            context=context,
        )
    )
    prefixed_root_ref = builder.put_root(
        builder.push_frame(prefix_frame_ref, tail),
        program_ref="program:cache-tail",
        branch_ref="branch:root",
        branch_scope_ref=None,
        continuation_kind="captured-worker",
        execution_context_ref=content_ref("ctx", _context_payload()),
        execution_context=_context_payload(),
        result_schema_ref="schema:worker",
    )
    validator = _ContinuationEvidenceValidator(builder.store.snapshot())

    validator.validate_root(root_ref)
    validated_after_tail = len(validator._validated_frame_control_nodes)
    validator.validate_root(prefixed_root_ref)

    assert len(validator._validated_frame_control_nodes) == validated_after_tail + 2


def test_validate_trace_evidence_rejects_bad_trace_control_ref() -> None:
    result = _trace_result()
    trace = tuple(
        replace(record, captured_continuation_control_ref="continuation-control:sha256:bad")
        if isinstance(record, HandlerSelection)
        else record
        for record in result.trace
    )

    with pytest.raises(TraceValidationError, match="control"):
        validate_trace_evidence(_bundle(result, trace=trace))


@pytest.mark.parametrize(
    ("field_name", "bad_value", "match"),
    [
        ("program_ref", "program:sha256:wrong", "program_ref mismatch"),
        ("branch_ref", "branch:wrong", "branch_ref mismatch"),
        ("branch_scope_ref", "branch-scope:wrong", "branch_scope_ref mismatch"),
        ("result_schema_ref", "schema:wrong", "result_schema_ref mismatch"),
        ("continuation_kind", "outer", "continuation_kind mismatch"),
    ],
)
def test_validate_trace_evidence_rejects_forged_declaration_root_facts(
    field_name: str,
    bad_value: str,
    match: str,
) -> None:
    result = _trace_result()
    declaration = next(record for record in result.trace if isinstance(record, EffectDeclaration))
    root = result.require_debug_evidence().continuation_objects[
        _continuation_evidence_ref(result, declaration.full_continuation_ref)
    ]
    assert isinstance(root, ContinuationRoot)
    bad_root = replace(root, **{field_name: bad_value})

    with pytest.raises(TraceValidationError, match=match):
        validate_trace_evidence(_bundle_with_rewired_root(result, declaration.full_continuation_ref, bad_root))


def test_validate_trace_evidence_rejects_forged_root_execution_context_fact() -> None:
    result = _trace_result()
    declaration = next(record for record in result.trace if isinstance(record, EffectDeclaration))
    root = result.require_debug_evidence().continuation_objects[
        _continuation_evidence_ref(result, declaration.full_continuation_ref)
    ]
    assert isinstance(root, ContinuationRoot)
    forged_context = dict(root.execution_context)
    forged_context["authority_ref"] = "authority:forged"
    bad_root = replace(
        root,
        execution_context_ref=content_ref("ctx", forged_context),
        execution_context=forged_context,
    )

    with pytest.raises(TraceValidationError, match="execution_context_ref mismatch"):
        validate_trace_evidence(_bundle_with_rewired_root(result, declaration.full_continuation_ref, bad_root))


def test_validate_trace_evidence_rejects_forged_captured_root_kind() -> None:
    result = _trace_result()
    selection = next(record for record in result.trace if isinstance(record, HandlerSelection))
    root = result.require_debug_evidence().continuation_objects[
        _continuation_evidence_ref(result, selection.captured_continuation_ref)
    ]
    assert isinstance(root, ContinuationRoot)
    bad_root = replace(root, continuation_kind="outer")

    with pytest.raises(TraceValidationError, match="continuation_kind mismatch"):
        validate_trace_evidence(_bundle_with_rewired_root(result, selection.captured_continuation_ref, bad_root))


def test_validate_trace_evidence_rejects_forged_handler_continuation_context() -> None:
    result = _trace_result()
    resume = next(record for record in result.trace if isinstance(record, ContinuationResume))
    root = result.require_debug_evidence().continuation_objects[
        _continuation_evidence_ref(result, resume.handler_continuation_ref)
    ]
    assert isinstance(root, ContinuationRoot)
    forged_context = dict(root.execution_context)
    forged_context["region_ref"] = "region:forged"
    bad_root = replace(
        root,
        execution_context_ref=content_ref("ctx", forged_context),
        execution_context=forged_context,
    )

    with pytest.raises(TraceValidationError, match="execution_context_ref mismatch"):
        validate_trace_evidence(_bundle_with_rewired_root(result, resume.handler_continuation_ref, bad_root))


def test_validate_trace_evidence_rejects_root_payload_ref_mismatch() -> None:
    result = _trace_result()
    declaration = next(record for record in result.trace if isinstance(record, EffectDeclaration))
    root = result.require_debug_evidence().continuation_objects[
        _continuation_evidence_ref(result, declaration.full_continuation_ref)
    ]
    assert isinstance(root, ContinuationRoot)
    bad_root = replace(
        root,
        execution_context={**root.execution_context, "authority_ref": "authority:payload-mismatch"},
    )

    with pytest.raises(TraceValidationError, match="execution_context_ref"):
        validate_trace_evidence(_bundle_with_rewired_root(result, declaration.full_continuation_ref, bad_root))


def test_validate_trace_evidence_accepts_deep_stack_without_recursion_failure() -> None:
    result = _trace_result()
    declaration = next(record for record in result.trace if isinstance(record, EffectDeclaration))
    old_root_ref = _continuation_evidence_ref(result, declaration.full_continuation_ref)
    old_root = result.require_debug_evidence().continuation_objects[old_root_ref]
    assert isinstance(old_root, ContinuationRoot)
    builder = ContinuationObjectBuilder()
    stack = builder.empty_stack

    for idx in range(1_100):
        context = _context_payload()
        frame_ref = builder.put_frame(
            BindFramePayload(
                binder_ref=f"binder:{idx}",
                env_ref=builder.empty_env_ref,
                context_ref=content_ref("ctx", context),
                context=context,
            )
        )
        stack = builder.push_frame(frame_ref, stack)

    root_ref = builder.put_root(
        stack,
        program_ref=declaration.program_ref,
        branch_ref=declaration.branch_ref,
        branch_scope_ref=declaration.branch_scope_ref,
        continuation_kind="full",
        execution_context_ref=old_root.execution_context_ref,
        execution_context=old_root.execution_context,
        result_schema_ref=declaration.operation_result_schema_ref,
    )
    objects = dict(result.require_debug_evidence().continuation_objects)
    objects.update(builder.store.snapshot((root_ref,)))
    root_refs = _replace_ref_in_tuple(result.require_debug_evidence().continuation_root_refs, old_root_ref, root_ref)
    continuation_ref_map = {
        **result.require_debug_evidence().continuation_ref_map,
        declaration.full_continuation_ref: root_ref,
    }

    validate_trace_evidence(
        _bundle(
            result,
            continuation_root_refs=root_refs,
            continuation_objects=objects,
            continuation_ref_map=continuation_ref_map,
        )
    )


def _trace_result() -> TraceResult:
    return run_trace(
        elaborate(
            Handle(
                Let("x", Perform("eff.a", Lit({"i": 0})), Return(Var("x"))),
                HandlerEnv(
                    (
                        StaticHandlerInstall(
                            effect_kind="eff.a",
                            handler_id="trace-evidence-test.handler.v1",
                            handled_result_schema=AnySchema(),
                            payload_name="_payload",
                            body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                        ),
                    )
                ),
            )
        ),
        include_debug_evidence=True,
    )


def _bundle(
    result: TraceResult,
    *,
    trace=None,
    continuation_root_refs=None,
    continuation_objects=None,
    continuation_ref_map=None,
    continuation_control_ref_map=None,
    context_ref_map=None,
) -> TraceEvidenceBundle:
    evidence = result.require_debug_evidence()
    return TraceEvidenceBundle(
        bundle_schema_version=TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        trace=result.trace if trace is None else trace,
        continuation_root_refs=evidence.continuation_root_refs
        if continuation_root_refs is None
        else continuation_root_refs,
        continuation_objects=evidence.continuation_objects if continuation_objects is None else continuation_objects,
        validation_profile="runtime-with-continuations",
        continuation_ref_map=evidence.continuation_ref_map if continuation_ref_map is None else continuation_ref_map,
        continuation_control_ref_map=(
            evidence.continuation_control_ref_map
            if continuation_control_ref_map is None
            else continuation_control_ref_map
        ),
        context_ref_map=evidence.context_ref_map if context_ref_map is None else context_ref_map,
    )


def _trace_continuation_refs(trace) -> set[str]:
    refs: set[str] = set()
    for record in trace:
        for field_name in _CONTINUATION_FIELD_NAMES:
            value = getattr(record, field_name, None)
            if isinstance(value, str):
                refs.add(value)
    return refs


def _bundle_with_rewired_root(
    result: TraceResult,
    old_ref: str,
    new_root: ContinuationRoot,
) -> TraceEvidenceBundle:
    new_ref = continuation_object_ref(new_root)
    old_evidence_ref = _continuation_evidence_ref(result, old_ref)
    objects = dict(result.require_debug_evidence().continuation_objects)
    objects[new_ref] = new_root
    continuation_ref_map = {
        **result.require_debug_evidence().continuation_ref_map,
        old_ref: new_ref,
    }
    return _bundle(
        result,
        continuation_root_refs=_replace_ref_in_tuple(
            result.require_debug_evidence().continuation_root_refs, old_evidence_ref, new_ref
        ),
        continuation_objects=objects,
        continuation_ref_map=continuation_ref_map,
    )


def _continuation_evidence_ref(result: TraceResult, trace_ref: str) -> str:
    evidence = result.require_debug_evidence()
    return evidence.continuation_ref_map.get(trace_ref, trace_ref)


def _replace_continuation_ref_in_trace(trace, old_ref: str, new_ref: str):
    return tuple(_replace_continuation_ref_in_record(record, old_ref, new_ref) for record in trace)


def _replace_continuation_ref_in_record(record, old_ref: str, new_ref: str):
    updates = {
        field_name: new_ref for field_name in _CONTINUATION_FIELD_NAMES if getattr(record, field_name, None) == old_ref
    }
    return replace(record, **updates) if updates else record


def _replace_ref_in_tuple(refs: tuple[str, ...], old_ref: str, new_ref: str) -> tuple[str, ...]:
    return tuple(new_ref if ref == old_ref else ref for ref in refs)


def _env_bindings(objects, env_ref: str) -> tuple[tuple[str, object], ...]:
    bindings: list[tuple[str, object]] = []
    ref = env_ref
    while True:
        obj = objects[ref]
        if isinstance(obj, ContinuationEnvEmpty):
            return tuple(reversed(bindings))
        assert isinstance(obj, ContinuationEnvNode)
        bindings.append((obj.name, obj.value))
        ref = obj.parent_env_ref


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
