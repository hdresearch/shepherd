import json
from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate, prepare_kernel_program
from shepherd_kernel_v3_reference.kernel.continuation_objects import (
    ContinuationEnvNode,
    ContinuationRoot,
    continuation_object_child_refs,
    continuation_object_ref,
)
from shepherd_kernel_v3_reference.kernel.replay import (
    ContinuationReplayError,
    ContinuationReplayLedger,
    ContinuationReplaySerializationError,
    continuation_replay_artifact_from_json,
    continuation_replay_artifact_from_objects,
    continuation_replay_artifact_to_json,
    resume_continuation,
)
from shepherd_kernel_v3_reference.kernel.step_machine import StepKernelEvaluator
from shepherd_kernel_v3_reference.schemas import AnySchema, TypeSchema, ValidationError
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed, ResumptionUsed, Suspended
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.records import EffectDeclaration, ResumptionHandle


def test_replay_artifact_json_round_trips_and_trims_to_reachable_objects() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, declaration, program_ref = _suspended_root(program)
    extra = ContinuationEnvNode(
        parent_env_ref=next(ref for ref, obj in objects.items() if obj.object_type == "env-empty"),
        name="unused",
        value=0,
        depth=1,
    )
    extended_objects = {**objects, continuation_object_ref(extra): extra}

    artifact = continuation_replay_artifact_from_objects(
        root_ref,
        extended_objects,
        program_ref=program_ref,
        source_ref=declaration.ref,
        source_record_type="EffectDeclaration",
        effect_kind=declaration.effect_kind,
        operation_result_schema_ref=declaration.operation_result_schema_ref,
    )
    decoded = continuation_replay_artifact_from_json(
        json.loads(json.dumps(continuation_replay_artifact_to_json(artifact)))
    )

    assert decoded == artifact
    assert continuation_object_ref(extra) not in decoded.continuation_objects
    assert decoded.source_key is not None
    assert decoded.source_key.startswith("continuation-source:sha256:")
    assert resume_continuation(program, decoded, "resumed") == Completed("resumed")


def test_replay_artifact_rejects_ref_payload_mismatch() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, _declaration, _program_ref = _suspended_root(program)
    bad_objects = dict(objects)
    bad_ref, bad_object = next(iter(bad_objects.items()))
    del bad_objects[bad_ref]
    bad_objects["continuation-object:sha256:bad"] = bad_object

    with pytest.raises(ContinuationReplayError, match="ref mismatch"):
        continuation_replay_artifact_from_objects(root_ref, bad_objects)


def test_replay_artifact_rejects_missing_root_and_missing_children() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, _declaration, _program_ref = _suspended_root(program)

    with pytest.raises(ContinuationReplayError, match="missing object"):
        continuation_replay_artifact_from_objects("continuation-object:sha256:missing", objects)

    missing_child_objects = dict(objects)
    missing_child_ref = continuation_object_child_refs(missing_child_objects[root_ref])[0]
    del missing_child_objects[missing_child_ref]
    with pytest.raises(ContinuationReplayError, match="missing object"):
        continuation_replay_artifact_from_objects(root_ref, missing_child_objects)


def test_replay_artifact_rejects_schema_metadata_disagreement() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))), registry=registry)
    root_ref, objects, _declaration, _program_ref = _suspended_root(program, registry=registry)

    with pytest.raises(ContinuationReplayError, match="operation_result_schema_ref"):
        continuation_replay_artifact_from_objects(
            root_ref,
            objects,
            operation_result_schema_ref="schema:wrong",
        )


def test_replay_artifact_rejects_mismatched_canonical_source_key() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, declaration, program_ref = _suspended_root(program)

    with pytest.raises(ContinuationReplayError, match="source_key"):
        continuation_replay_artifact_from_objects(
            root_ref,
            objects,
            program_ref=program_ref,
            source_key="continuation-source:sha256:wrong",
            source_ref=declaration.ref,
            source_record_type="EffectDeclaration",
            effect_kind=declaration.effect_kind,
            operation_result_schema_ref=declaration.operation_result_schema_ref,
        )


def test_replay_artifact_rejects_terminal_roots() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, _declaration, _program_ref = _suspended_root(program)
    root = objects[root_ref]
    assert isinstance(root, ContinuationRoot)
    terminal_root = replace(root, continuation_kind="empty-terminal")
    terminal_root_ref = continuation_object_ref(terminal_root)
    terminal_objects = {**objects, terminal_root_ref: terminal_root}

    with pytest.raises(ContinuationReplayError, match="empty-terminal"):
        continuation_replay_artifact_from_objects(terminal_root_ref, terminal_objects)

    with pytest.raises(RuntimeError, match="empty-terminal"):
        StepKernelEvaluator(program, evidence_mode="none")._resume_value_from_continuation_root(
            terminal_root,
            "resumed",
            source_label="resume('eff.a')",
        )


def test_resume_continuation_rejects_different_program() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    artifact = _artifact_for(program)

    with pytest.raises(ContinuationReplayError, match="program_ref"):
        resume_continuation(elaborate(Return(Lit("different"))), artifact, "resumed")


def test_resume_continuation_accepts_prepared_programs() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    artifact = _artifact_for(program)

    assert resume_continuation(prepare_kernel_program(program), artifact, "resumed") == Completed("resumed")


def test_resume_continuation_validates_resume_value_against_root_schema() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))), registry=registry)
    artifact = _artifact_for(program, registry=registry)

    with pytest.raises(ValidationError, match="expected int"):
        resume_continuation(program, artifact, "bad", registry=registry)

    assert resume_continuation(program, artifact, 7, registry=registry) == Completed(7)


def test_resume_continuation_ledger_rejects_second_replay() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    artifact = _artifact_for(program)
    ledger = ContinuationReplayLedger()

    assert resume_continuation(program, artifact, "resumed", ledger=ledger) == Completed("resumed")
    with pytest.raises(ResumptionUsed, match="already consumed"):
        resume_continuation(program, artifact, "again", ledger=ledger)


def test_resume_continuation_ledger_requires_source_key() -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    root_ref, objects, _declaration, _program_ref = _suspended_root(program)
    artifact = continuation_replay_artifact_from_objects(root_ref, objects)

    with pytest.raises(ContinuationReplayError, match="source_key"):
        resume_continuation(program, artifact, "resumed", ledger=ContinuationReplayLedger())


def test_resume_continuation_bad_value_consumes_ledger_source_key() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))), registry=registry)
    artifact = _artifact_for(program, registry=registry)
    ledger = ContinuationReplayLedger()

    with pytest.raises(ValidationError, match="expected int"):
        resume_continuation(program, artifact, "bad", registry=registry, ledger=ledger)

    with pytest.raises(ResumptionUsed, match="already consumed"):
        resume_continuation(program, artifact, 7, registry=registry, ledger=ledger)


def test_resume_continuation_reconstructs_root_stack_once(monkeypatch) -> None:
    program = elaborate(Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))))
    artifact = _artifact_for(program)
    root = artifact.continuation_objects[artifact.root_ref]
    assert isinstance(root, ContinuationRoot)
    root_stack_walks = 0
    original = StepKernelEvaluator._kont_state_from_continuation_object_stack_ref

    def counted_stack_reconstruction(self, stack_ref):
        nonlocal root_stack_walks
        if stack_ref == root.stack_ref:
            root_stack_walks += 1
        return original(self, stack_ref)

    monkeypatch.setattr(
        StepKernelEvaluator,
        "_kont_state_from_continuation_object_stack_ref",
        counted_stack_reconstruction,
    )

    assert resume_continuation(program, artifact, "resumed") == Completed("resumed")
    assert root_stack_walks == 1


def test_resume_continuation_replays_serialized_handler_body_continuation() -> None:
    program = elaborate(
        Handle(
            Let("x", Perform("eff.worker", Lit({"input": "draft"})), Return(Var("x"))),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.worker",
                        handler_id="continuation-replay-test.handler.v1",
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
    artifact = _artifact_for(program, effect_kind="provider.llm.generate")
    decoded = continuation_replay_artifact_from_json(
        json.loads(json.dumps(continuation_replay_artifact_to_json(artifact)))
    )

    assert resume_continuation(program, decoded, "accepted") == Completed("accepted")


def test_resume_continuation_replays_resumption_handle_continuation() -> None:
    program = elaborate(
        Handle(
            Let("x", Perform("eff.worker", Lit({"input": "draft"})), Return(Var("x"))),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.worker",
                        handler_id="continuation-replay-resumption-test.handler.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("worker-value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )
    result = run_trace(program, include_debug_evidence=True)
    evidence = result.require_debug_evidence()
    handle = next(record for record in result.trace if isinstance(record, ResumptionHandle))
    root_ref = evidence.continuation_ref_map[handle.continuation_ref]
    artifact = continuation_replay_artifact_from_objects(
        root_ref,
        evidence.continuation_objects,
        program_ref=evidence.program_ref,
        source_ref=handle.ref,
        source_record_type="ResumptionHandle",
        operation_result_schema_ref=handle.operation_result_schema_ref,
    )
    decoded = continuation_replay_artifact_from_json(
        json.loads(json.dumps(continuation_replay_artifact_to_json(artifact)))
    )

    assert decoded.source_key is not None
    assert decoded.source_key.startswith("continuation-source:sha256:")
    assert resume_continuation(program, decoded, "direct-worker-resume") == Completed("direct-worker-resume")


def test_replay_artifact_from_json_rejects_malformed_shape() -> None:
    with pytest.raises(ContinuationReplaySerializationError, match="keys disagree"):
        continuation_replay_artifact_from_json({"root_ref": "continuation-object:sha256:missing"})


def _artifact_for(program, *, registry: EffectRegistry | None = None, effect_kind: str = "eff.a"):
    root_ref, objects, declaration, program_ref = _suspended_root(program, registry=registry, effect_kind=effect_kind)
    return continuation_replay_artifact_from_objects(
        root_ref,
        objects,
        program_ref=program_ref,
        source_ref=declaration.ref,
        source_record_type="EffectDeclaration",
        effect_kind=declaration.effect_kind,
        operation_result_schema_ref=declaration.operation_result_schema_ref,
    )


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
        evidence.program_ref,
    )
