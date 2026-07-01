import hashlib
import json
from dataclasses import dataclass, replace

import pytest

from shepherd_kernel_v3_reference.kernel import (
    KernelProgram,
    elaborate,
)
from shepherd_kernel_v3_reference.kernel.context import ExecutionContext
from shepherd_kernel_v3_reference.kernel.continuation_objects import (
    CONTINUATION_OBJECT_SCHEMA_VERSION,
    ContinuationEnvEmpty,
    ContinuationRoot,
    continuation_object_from_json,
    continuation_object_ref,
    continuation_object_to_json,
)
from shepherd_kernel_v3_reference.kernel.continuations import (
    CONTINUATION_CONTROL_SCHEMA_VERSION,
    ContinuationImage,
    continuation_control_payload,
    continuation_control_ref,
    continuation_image_from_json,
    continuation_image_to_json,
)
from shepherd_kernel_v3_reference.kernel.frame_state import HandlerFrame, HandlerReturnFrame
from shepherd_kernel_v3_reference.kernel.ir import HandlerEnvDef, HandlerInstallDef, KPure, SchemaDef
from shepherd_kernel_v3_reference.kernel.recursive_machine import RecursiveKernelEvaluator
from shepherd_kernel_v3_reference.kernel.refs import content_ref
from shepherd_kernel_v3_reference.kernel.step_machine import StepKernelEvaluator
from shepherd_kernel_v3_reference.schemas import AnySchema, TypeSchema, ValidationError
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed, Suspended
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.source.values import Env
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.records import EffectDeclaration


def test_content_ref_rejects_non_deterministic_fallback_values() -> None:
    class Opaque:
        pass

    with pytest.raises(TypeError, match="not content-addressable"):
        content_ref("value", Opaque())


def test_content_ref_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(TypeError, match="string keys"):
        content_ref("value", {1: "one"})


def test_content_ref_rejects_dataclasses() -> None:
    @dataclass(frozen=True)
    class TaggedValue:
        value: str

    with pytest.raises(TypeError, match="JSON-compatible"):
        content_ref("value", TaggedValue("x"))


def test_content_ref_uses_unescaped_utf8_canonical_json() -> None:
    payload = {"text": "cafe\u0301"}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    ).encode("utf-8")

    assert content_ref("value", payload) == f"value:sha256:{hashlib.sha256(encoded).hexdigest()}"


def test_continuation_ref_excludes_source_consumption_state() -> None:
    install = HandlerInstallDef(
        install_ref="install:0",
        effect_kind="eff.a",
        handler_id="h.v1",
        handled_result_schema_ref="schema:handled",
        payload_name="_payload",
        body=KPure(Lit(None)),
    )
    evaluator = RecursiveKernelEvaluator(
        KernelProgram(
            KPure(Lit(None)),
            {},
            {"handler-env:0": HandlerEnvDef("handler-env:0", (install,))},
            {"schema:handled": SchemaDef("schema:handled", AnySchema())},
        )
    )
    frame = HandlerReturnFrame(
        install=install,
        captured_kont=(),
        selected_handler_frame=HandlerFrame(
            handler_env_ref="handler-env:0",
            env=Env(),
            region_ref="region:root",
        ),
        outer_kont=(),
        handler_env=Env(),
        declaration_ref="declaration:0",
        selection_ref="selection:0",
        resumption_handle_ref="resumption:0",
        selection_path_ref="path:selection:0/resumption:0/branch:root",
        captured_continuation_ref="kont:captured",
        outer_continuation_ref="kont:outer",
        captured_continuation_control_ref="continuation-control:captured",
        outer_continuation_control_ref="continuation-control:outer",
        operation_result_schema_ref="schema:operation-result",
        handled_result_schema_ref="schema:handled",
    )
    builder = evaluator._evidence_builder()
    frame = replace(
        frame,
        captured_stack_ref=builder.empty_stack.stack_ref,
        selected_handler_frame_ref=evaluator._continuation_frame_ref(frame.selected_handler_frame),
        outer_stack_ref=builder.empty_stack.stack_ref,
    )

    kont = evaluator._kont_state_from_frames((frame,))
    ref_before_consumption = evaluator._kont_ref(
        kont,
        continuation_kind="captured-worker",
        context=ExecutionContext(),
    )
    assert evaluator._state.consume_source_path("path:selection:0/resumption:0/branch:root")

    assert (
        evaluator._kont_ref(
            kont,
            continuation_kind="captured-worker",
            context=ExecutionContext(),
        )
        == ref_before_consumption
    )


def test_continuation_ref_has_durable_json_root_object() -> None:
    evaluator = RecursiveKernelEvaluator(KernelProgram(KPure(Lit("done")), {}, {}, {}))
    ref = evaluator._kont_ref(
        evaluator._empty_kont_state(),
        continuation_kind="full",
        context=ExecutionContext(),
    )
    root = evaluator.get_continuation_object(ref)

    assert isinstance(root, ContinuationRoot)
    assert continuation_object_ref(root) == ref
    assert root.object_schema_version == CONTINUATION_OBJECT_SCHEMA_VERSION
    assert ref.startswith("continuation-object:sha256:")
    assert root.program_ref.startswith("program:sha256:")
    assert root.branch_ref == "branch:root"
    assert root.branch_scope_ref is None
    assert root.position == "value"
    assert root.continuation_kind == "full"
    assert root.execution_context_ref.startswith("ctx:sha256:")
    assert root.execution_context == {
        "binding_env_ref": continuation_object_ref(ContinuationEnvEmpty()),
        "region_ref": "region:root",
        "authority_ref": "authority:root",
    }
    assert root.stack_ref in evaluator.continuation_objects
    encoded = json.loads(json.dumps(continuation_object_to_json(root)))
    decoded = evaluator.get_continuation_object(ref).__class__(**encoded)
    assert continuation_object_ref(decoded) == ref


def test_continuation_image_ref_commits_to_full_semantic_payload() -> None:
    outer = ContinuationImage(
        **_image_kwargs(continuation_kind="outer"),
    )
    terminal = ContinuationImage(
        **_image_kwargs(continuation_kind="empty-terminal"),
    )
    other_program = ContinuationImage(
        **_image_kwargs(program_ref="program:other"),
    )
    other_context = ContinuationImage(
        **_image_kwargs(
            execution_context_ref="ctx:other",
            execution_context={
                "binding_env_ref": "env:other",
                "region_ref": "region:root",
                "authority_ref": "authority:root",
            },
        ),
    )

    assert len({outer.ref, terminal.ref, other_program.ref, other_context.ref}) == 4


def test_continuation_control_ref_ignores_image_role_and_top_level_context() -> None:
    base = continuation_control_payload(
        program_ref="program:test",
        branch_ref="branch:root",
        branch_scope_ref=None,
        position="value",
        frames=({"frame": "bind", "binder_ref": "binder:0"},),
    )
    same_control_different_image_role = ContinuationImage(
        **_image_kwargs(
            continuation_kind="outer",
            frames=({"frame": "bind", "binder_ref": "binder:0"},),
        )
    )
    same_control_different_context = ContinuationImage(
        **_image_kwargs(
            execution_context_ref="ctx:other",
            execution_context={
                "binding_env_ref": "env:other",
                "region_ref": "region:root",
                "authority_ref": "authority:root",
            },
            frames=({"frame": "bind", "binder_ref": "binder:0"},),
        )
    )
    other_branch = continuation_control_payload(
        program_ref="program:test",
        branch_ref="branch:other",
        branch_scope_ref=None,
        position="value",
        frames=({"frame": "bind", "binder_ref": "binder:0"},),
    )

    assert base["control_schema_version"] == CONTINUATION_CONTROL_SCHEMA_VERSION
    assert (
        continuation_control_ref(base)
        == continuation_control_ref(
            continuation_control_payload(
                program_ref=same_control_different_image_role.program_ref,
                branch_ref=same_control_different_image_role.branch_ref,
                branch_scope_ref=same_control_different_image_role.branch_scope_ref,
                position=same_control_different_image_role.position,
                frames=same_control_different_image_role.frames,
            )
        )
        == continuation_control_ref(
            continuation_control_payload(
                program_ref=same_control_different_context.program_ref,
                branch_ref=same_control_different_context.branch_ref,
                branch_scope_ref=same_control_different_context.branch_scope_ref,
                position=same_control_different_context.position,
                frames=same_control_different_context.frames,
            )
        )
    )
    assert continuation_control_ref(base) != continuation_control_ref(other_branch)
    assert same_control_different_image_role.ref != same_control_different_context.ref


def test_continuation_control_ref_ignores_nested_role_image_refs() -> None:
    install = HandlerInstallDef(
        install_ref="install:0",
        effect_kind="eff.a",
        handler_id="h.v1",
        handled_result_schema_ref="schema:handled",
        payload_name="_payload",
        body=KPure(Lit(None)),
    )
    evaluator = RecursiveKernelEvaluator(
        KernelProgram(
            KPure(Lit(None)),
            {},
            {"handler-env:0": HandlerEnvDef("handler-env:0", (install,))},
            {"schema:handled": SchemaDef("schema:handled", AnySchema())},
        )
    )
    frame = HandlerReturnFrame(
        install=install,
        captured_kont=(),
        selected_handler_frame=HandlerFrame(
            handler_env_ref="handler-env:0",
            env=Env(),
            region_ref="region:root",
        ),
        outer_kont=(),
        handler_env=Env(),
        declaration_ref="declaration:0",
        selection_ref="selection:0",
        resumption_handle_ref="resumption:0",
        selection_path_ref="path:selection:0/resumption:0/branch:root",
        captured_continuation_ref="continuation-image:captured-a",
        outer_continuation_ref="continuation-image:outer-a",
        captured_continuation_control_ref="continuation-control:captured",
        outer_continuation_control_ref="continuation-control:outer",
        operation_result_schema_ref="schema:operation-result",
        handled_result_schema_ref="schema:handled",
    )
    builder = evaluator._evidence_builder()
    frame = replace(
        frame,
        captured_stack_ref=builder.empty_stack.stack_ref,
        selected_handler_frame_ref=evaluator._continuation_frame_ref(frame.selected_handler_frame),
        outer_stack_ref=builder.empty_stack.stack_ref,
    )

    same_control = replace(
        frame,
        captured_continuation_ref="continuation-image:captured-b",
        outer_continuation_ref="continuation-image:outer-b",
    )

    assert evaluator._kont_control_ref(evaluator._kont_state_from_frames((frame,))) == evaluator._kont_control_ref(
        evaluator._kont_state_from_frames((same_control,))
    )


def test_continuation_image_rejects_ref_that_disagrees_with_payload() -> None:
    image = ContinuationImage(**_image_kwargs())
    encoded = continuation_image_to_json(image)
    encoded["ref"] = "continuation-image:sha256:bad"

    with pytest.raises(ValueError, match="ref does not match"):
        continuation_image_from_json(encoded)


def test_continuation_image_rejects_opaque_frames_at_creation() -> None:
    class Opaque:
        pass

    with pytest.raises(TypeError, match="ContinuationImage.*non-JSON-compatible"):
        ContinuationImage(
            program_ref="program:test",
            branch_ref="branch:root",
            branch_scope_ref=None,
            position="value",
            continuation_kind="full",
            execution_context_ref="ctx:test",
            execution_context={
                "binding_env_ref": "env:root",
                "region_ref": "region:root",
                "authority_ref": "authority:root",
            },
            frames=({"frame": Opaque()},),
        )


def test_continuation_image_freezes_nested_payloads() -> None:
    execution_context = {
        "binding_env_ref": "env:root",
        "region_ref": "region:root",
        "authority_ref": "authority:root",
    }
    frames = [
        {
            "frame": "bind",
            "binder_ref": "binder:0",
            "env": [["x", {"value": 1}]],
        }
    ]

    image = ContinuationImage(
        **_image_kwargs(
            execution_context=execution_context,
            frames=frames,
        )
    )
    ref = image.ref

    execution_context["binding_env_ref"] = "env:changed"
    frames[0]["env"][0][1]["value"] = 2

    assert image.ref == ref
    assert image.execution_context["binding_env_ref"] == "env:root"
    assert image.frames[0]["env"][0][1]["value"] == 1
    with pytest.raises(TypeError, match="immutable"):
        image.execution_context["binding_env_ref"] = "env:changed"
    with pytest.raises(TypeError, match="immutable"):
        image.frames[0]["env"][0][1]["value"] = 3
    assert continuation_image_from_json(continuation_image_to_json(image)).ref == ref


def test_continuation_image_catalog_rejects_payload_disagreement_under_ref() -> None:
    evaluator = RecursiveKernelEvaluator(KernelProgram(KPure(Lit("done")), {}, {}, {}))
    first = ContinuationImage(**_image_kwargs(continuation_kind="outer"))
    second = ContinuationImage(**_image_kwargs(continuation_kind="empty-terminal"))
    object.__setattr__(second, "ref", first.ref)

    evaluator._register_continuation_image(first)
    with pytest.raises(RuntimeError, match="catalog collision"):
        evaluator._register_continuation_image(second)


def test_internal_resume_from_continuation_objects_restarts_nontrivial_bind_continuation() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, _declaration = _suspended_root(program)

    resumed = StepKernelEvaluator(program, evidence_mode="none")._resume_value_from_continuation_objects(
        root_ref,
        objects,
        "resumed",
        source_label="resume('eff.a')",
    )

    assert resumed == Completed("resumed")


def test_internal_resume_from_continuation_objects_restarts_through_handler_continuation() -> None:
    program = elaborate(
        Handle(
            Let("x", Perform("eff.worker", Lit({"input": "draft"})), Return(Var("x"))),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.worker",
                        handler_id="continuation-object-replay.handler.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let(
                            "approved",
                            Perform("provider.llm.generate", Lit({"prompt": "approve"})),
                            Resume(Var("approved")),
                        ),
                    ),
                )
            ),
        )
    )
    root_ref, objects, _declaration = _suspended_root(program, effect_kind="provider.llm.generate")

    resumed = StepKernelEvaluator(program, evidence_mode="none")._resume_value_from_continuation_objects(
        root_ref,
        _json_round_trip_continuation_objects(objects),
        "accepted",
        source_label="resume('provider.llm.generate')",
    )

    assert resumed == Completed("accepted")


def test_internal_resume_from_continuation_objects_checks_source_result_schema() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))), registry=registry)
    root_ref, objects, _declaration = _suspended_root(program, registry=registry)
    evaluator = StepKernelEvaluator(program, registry=registry, evidence_mode="none")

    with pytest.raises(ValidationError, match="expected int"):
        evaluator._resume_value_from_continuation_objects(
            root_ref,
            objects,
            "bad",
            source_label="resume('eff.a')",
        )

    assert evaluator._resume_value_from_continuation_objects(
        root_ref,
        objects,
        7,
        source_label="resume('eff.a')",
    ) == Completed(7)


def test_internal_resume_from_continuation_objects_rejects_different_program() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, _declaration = _suspended_root(program)
    consumer = StepKernelEvaluator(elaborate(Return(Lit("different"))), evidence_mode="none")

    with pytest.raises(RuntimeError, match="program_ref"):
        consumer._resume_value_from_continuation_objects(
            root_ref,
            objects,
            "resumed",
            source_label="resume('eff.a')",
        )


def test_internal_resume_from_continuation_objects_rejects_ref_payload_mismatch() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, _declaration = _suspended_root(program)
    bad_objects = dict(objects)
    bad_ref, bad_object = next(iter(bad_objects.items()))
    del bad_objects[bad_ref]
    bad_objects["continuation-object:sha256:bad"] = bad_object

    with pytest.raises(RuntimeError, match="ref mismatch"):
        StepKernelEvaluator(program, evidence_mode="none")._resume_value_from_continuation_objects(
            root_ref,
            bad_objects,
            "resumed",
            source_label="resume('eff.a')",
        )


def _image_kwargs(**overrides):
    kwargs = {
        "program_ref": "program:test",
        "branch_ref": "branch:root",
        "branch_scope_ref": None,
        "position": "value",
        "continuation_kind": "full",
        "execution_context_ref": "ctx:test",
        "execution_context": {
            "binding_env_ref": "env:root",
            "region_ref": "region:root",
            "authority_ref": "authority:root",
        },
        "frames": (),
    }
    kwargs.update(overrides)
    return kwargs


def _suspended_root(program, *, registry: EffectRegistry | None = None, effect_kind: str = "eff.a"):
    result = run_trace(program, registry=registry, include_debug_evidence=True)

    assert isinstance(result.outcome, Suspended)
    declaration = next(
        record for record in result.trace if isinstance(record, EffectDeclaration) and record.effect_kind == effect_kind
    )
    evidence = result.require_debug_evidence()
    return (
        evidence.continuation_ref_map[declaration.full_continuation_ref],
        evidence.continuation_objects,
        declaration,
    )


def _json_round_trip_continuation_objects(objects):
    return {
        ref: continuation_object_from_json(json.loads(json.dumps(continuation_object_to_json(obj))))
        for ref, obj in objects.items()
    }
