from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.conformance import (
    CONFORMANCE_ARTIFACT_SCHEMA_VERSION,
    ConformanceArtifact,
    ConformanceArtifactSerializationError,
    ConformanceArtifactValidationError,
    ConformanceContinuationObject,
    artifact_from_trace_result,
    conformance_artifact_from_json,
    conformance_artifact_to_json,
    dumps_conformance_artifact,
    loads_conformance_artifact,
    validate_conformance_artifact,
)
from shepherd_kernel_v3_reference.conformance.artifact import artifact_from_trace_evidence_bundle
from shepherd_kernel_v3_reference.kernel import elaborate, elaborate_publication_experimental
from shepherd_kernel_v3_reference.kernel.continuation_objects import (
    CONTINUATION_OBJECT_SCHEMA_VERSION,
    ContinuationRoot,
    continuation_object_ref,
    continuation_object_to_json,
)
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.experimental import TerminalFork
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace import EffectDeclaration
from shepherd_kernel_v3_reference.trace.machine import TraceResult, run_trace
from shepherd_kernel_v3_reference.trace.serde import trace_to_json
from shepherd_kernel_v3_reference.trace.validate import (
    TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    TraceEvidenceBundle,
    TraceValidationError,
)


def test_artifact_from_trace_result_validates_offline() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    validate_conformance_artifact(loads_conformance_artifact(dumps_conformance_artifact(artifact)))


def test_publication_experimental_trace_result_is_not_a_conformance_artifact() -> None:
    result = _publication_trace_result()

    with pytest.raises(ConformanceArtifactValidationError, match="publication-experimental continuation evidence"):
        artifact_from_trace_result(result)


def test_publication_experimental_evidence_bundle_is_not_a_conformance_artifact() -> None:
    result = _publication_trace_result()
    evidence = result.require_debug_evidence()
    bundle = TraceEvidenceBundle(
        bundle_schema_version=TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        trace=result.trace,
        continuation_root_refs=evidence.continuation_root_refs,
        continuation_objects=evidence.continuation_objects,
        validation_profile="runtime-with-continuations",
        continuation_ref_map=evidence.continuation_ref_map,
        continuation_control_ref_map=evidence.continuation_control_ref_map,
        context_ref_map=evidence.context_ref_map,
    )

    with pytest.raises(ConformanceArtifactValidationError, match="publication-experimental continuation evidence"):
        artifact_from_trace_evidence_bundle(bundle, program_ref=evidence.program_ref)


def test_artifact_json_round_trip_is_equal_and_byte_stable() -> None:
    result = _trace_result()
    artifact = artifact_from_trace_result(result)
    text = dumps_conformance_artifact(artifact)

    assert loads_conformance_artifact(text) == artifact
    assert dumps_conformance_artifact(loads_conformance_artifact(text)) == text

    reversed_bundle = TraceEvidenceBundle(
        bundle_schema_version=TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        trace=result.trace,
        continuation_root_refs=tuple(reversed(result.require_debug_evidence().continuation_root_refs)),
        continuation_objects=dict(reversed(tuple(result.require_debug_evidence().continuation_objects.items()))),
        validation_profile="runtime-with-continuations",
        continuation_ref_map=result.require_debug_evidence().continuation_ref_map,
        continuation_control_ref_map=result.require_debug_evidence().continuation_control_ref_map,
        context_ref_map=result.require_debug_evidence().context_ref_map,
    )
    equivalent = artifact_from_trace_evidence_bundle(
        reversed_bundle, program_ref=result.require_debug_evidence().program_ref
    )
    assert dumps_conformance_artifact(equivalent) == text


def test_artifact_freezes_json_mappings() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(TypeError, match="immutable"):
        artifact.trace_json[0]["ref"] = "trace:forged"  # type: ignore[index]

    frozen = artifact.trace_json[0]
    with pytest.raises(TypeError, match="immutable"):
        frozen | {"ref": "trace:forged"}
    with pytest.raises(TypeError, match="immutable"):
        frozen |= {"ref": "trace:forged"}


def test_validate_conformance_artifact_rejects_missing_continuation_object() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(ConformanceArtifactValidationError, match="reachable snapshot|missing"):
        validate_conformance_artifact(replace(artifact, continuation_objects=artifact.continuation_objects[:-1]))


def test_validate_conformance_artifact_rejects_stale_object_entry_ref() -> None:
    artifact = artifact_from_trace_result(_trace_result())
    stale_entry = replace(artifact.continuation_objects[0], ref="continuation-object:sha256:stale")
    forged = replace(
        artifact,
        continuation_objects=(stale_entry, *artifact.continuation_objects[1:]),
    )

    with pytest.raises(ConformanceArtifactValidationError, match="does not match content ref"):
        validate_conformance_artifact(forged)


def test_runtime_artifact_rejects_record_only_evidence_when_trace_has_continuation_refs() -> None:
    result = _trace_result()
    artifact = artifact_from_trace_result(result)
    record_only = replace(artifact, continuation_root_refs=(), continuation_objects=())

    with pytest.raises(ConformanceArtifactValidationError, match="must carry root refs"):
        validate_conformance_artifact(record_only)


def test_lifecycle_only_artifact_rejects_continuation_evidence() -> None:
    artifact = artifact_from_trace_result(_trace_result())
    lifecycle = replace(artifact, validation_profile="lifecycle-only")

    with pytest.raises(ConformanceArtifactValidationError, match="lifecycle-only"):
        validate_conformance_artifact(lifecycle)


def test_lifecycle_only_artifact_accepts_record_only_trace() -> None:
    result = _trace_result()
    artifact = replace(
        artifact_from_trace_result(result),
        validation_profile="lifecycle-only",
        continuation_root_refs=(),
        continuation_ref_map={},
        continuation_control_ref_map={},
        context_ref_map={},
        continuation_objects=(),
        program_ref=None,
    )

    validate_conformance_artifact(artifact)


def test_mutated_root_fact_is_rejected_by_trace_evidence_validation() -> None:
    result = _trace_result()
    artifact = artifact_from_trace_result(result)
    declaration = next(record for record in result.trace if isinstance(record, EffectDeclaration))
    root_ref = _continuation_evidence_ref(result, declaration.full_continuation_ref)
    root = result.require_debug_evidence().continuation_objects[root_ref]
    assert isinstance(root, ContinuationRoot)
    bad_root = replace(root, program_ref="program:sha256:wrong")
    bad_root_ref = continuation_object_ref(bad_root)
    bad_objects = dict(result.require_debug_evidence().continuation_objects)
    bad_objects.pop(root_ref)
    bad_objects[bad_root_ref] = bad_root
    bad_root_refs = tuple(
        bad_root_ref if ref == root_ref else ref for ref in result.require_debug_evidence().continuation_root_refs
    )
    continuation_ref_map = {
        **result.require_debug_evidence().continuation_ref_map,
        declaration.full_continuation_ref: bad_root_ref,
    }
    forged = replace(
        artifact,
        trace_json=tuple(trace_to_json(result.trace)),
        continuation_root_refs=bad_root_refs,
        continuation_ref_map=continuation_ref_map,
        continuation_objects=tuple(
            ConformanceContinuationObject(ref=ref, object_json=continuation_object_to_json(bad_objects[ref]))
            for ref in sorted(bad_objects)
        ),
        program_ref=None,
    )
    with pytest.raises(TraceValidationError, match="program_ref mismatch"):
        validate_conformance_artifact(forged)


def test_artifact_builder_from_trace_evidence_bundle_does_not_need_evaluator_state() -> None:
    result = _trace_result()
    bundle = TraceEvidenceBundle(
        bundle_schema_version=TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        trace=result.trace,
        continuation_root_refs=result.require_debug_evidence().continuation_root_refs,
        continuation_objects=result.require_debug_evidence().continuation_objects,
        validation_profile="runtime-with-continuations",
        continuation_ref_map=result.require_debug_evidence().continuation_ref_map,
        continuation_control_ref_map=result.require_debug_evidence().continuation_control_ref_map,
        context_ref_map=result.require_debug_evidence().context_ref_map,
    )

    artifact = artifact_from_trace_evidence_bundle(bundle, program_ref=result.require_debug_evidence().program_ref)

    validate_conformance_artifact(artifact)
    assert artifact.program_ref == result.require_debug_evidence().program_ref


def test_duplicate_continuation_root_refs_are_rejected_at_artifact_boundary() -> None:
    artifact_json = conformance_artifact_to_json(artifact_from_trace_result(_trace_result()))
    artifact_json["continuation_root_refs"] = [
        *artifact_json["continuation_root_refs"],
        artifact_json["continuation_root_refs"][0],
    ]

    with pytest.raises(ConformanceArtifactValidationError, match="duplicate"):
        conformance_artifact_from_json(artifact_json)


def test_duplicate_continuation_object_refs_are_rejected_at_artifact_boundary() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(ConformanceArtifactValidationError, match="duplicate"):
        replace(
            artifact,
            continuation_objects=(
                artifact.continuation_objects[0],
                artifact.continuation_objects[0],
                *artifact.continuation_objects[1:],
            ),
        )


def test_artifact_json_rejects_unknown_top_level_keys() -> None:
    artifact_json = conformance_artifact_to_json(artifact_from_trace_result(_trace_result()))
    artifact_json["carrier_ref"] = "carrier:vcs-core:deferred"

    with pytest.raises(ConformanceArtifactSerializationError, match="unknown keys.*carrier_ref"):
        conformance_artifact_from_json(artifact_json)


def test_artifact_json_rejects_unknown_continuation_entry_keys() -> None:
    artifact_json = conformance_artifact_to_json(artifact_from_trace_result(_trace_result()))
    artifact_json["continuation_objects"][0]["carrier_ref"] = "carrier:vcs-core:deferred"

    with pytest.raises(ConformanceArtifactSerializationError, match="unknown keys.*carrier_ref"):
        conformance_artifact_from_json(artifact_json)


def test_forged_artifact_program_ref_is_rejected() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(ConformanceArtifactValidationError, match="program_ref"):
        validate_conformance_artifact(replace(artifact, program_ref="program:sha256:wrong"))


def test_unsupported_validation_profile_is_rejected() -> None:
    artifact_json = conformance_artifact_to_json(artifact_from_trace_result(_trace_result()))
    artifact_json["validation_profile"] = "publication-with-continuations"

    with pytest.raises(ConformanceArtifactSerializationError, match="unknown validation_profile"):
        conformance_artifact_from_json(artifact_json)


def test_artifact_schema_version_is_pinned() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    assert artifact.artifact_schema_version == CONFORMANCE_ARTIFACT_SCHEMA_VERSION
    assert artifact.schema_versions["conformance_artifact"] == CONFORMANCE_ARTIFACT_SCHEMA_VERSION
    assert artifact.schema_versions["trace_evidence_bundle"] == TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION
    assert artifact.schema_versions["continuation_object"] == CONTINUATION_OBJECT_SCHEMA_VERSION


def test_program_profile_round_trips_with_closed_semantic_profile_shape() -> None:
    profile = {"name": "core_a", "version": "v0", "validated": True}
    artifact = replace(artifact_from_trace_result(_trace_result()), program_profile=profile)

    loaded = loads_conformance_artifact(dumps_conformance_artifact(artifact))

    assert loaded.program_profile == profile


def test_program_profile_rejects_missing_required_key() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(TypeError, match="program_profile.*missing"):
        replace(artifact, program_profile={"name": "core_a", "version": "v0"})


def test_program_profile_rejects_wrong_validated_type() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(TypeError, match="validated must be a bool"):
        replace(artifact, program_profile={"name": "core_a", "version": "v0", "validated": "true"})


def test_program_profile_rejects_extra_keys() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(TypeError, match="program_profile.*extra"):
        replace(
            artifact,
            program_profile={
                "name": "core_a",
                "version": "v0",
                "validated": True,
                "implementation": "reference",
            },
        )


def test_schema_versions_reject_missing_required_key() -> None:
    artifact = artifact_from_trace_result(_trace_result())
    schema_versions = dict(artifact.schema_versions)
    schema_versions.pop("continuation_object")

    with pytest.raises(TypeError, match="missing required key 'continuation_object'"):
        replace(artifact, schema_versions=schema_versions)


def test_schema_versions_reject_stale_required_value() -> None:
    artifact = artifact_from_trace_result(_trace_result())
    schema_versions = dict(artifact.schema_versions)
    schema_versions["continuation_object"] = "shepherd_kernel_v3_reference.continuation-object.v1"

    with pytest.raises(TypeError, match="schema_versions.continuation_object"):
        replace(artifact, schema_versions=schema_versions)


def test_schema_versions_preserve_extra_string_keys() -> None:
    artifact = artifact_from_trace_result(_trace_result())
    schema_versions = {**artifact.schema_versions, "fixture_schema": "example.v1"}

    loaded = loads_conformance_artifact(dumps_conformance_artifact(replace(artifact, schema_versions=schema_versions)))

    assert loaded.schema_versions["fixture_schema"] == "example.v1"


def test_manual_artifact_rejects_non_json_source_outcome() -> None:
    artifact = artifact_from_trace_result(_trace_result())

    with pytest.raises(TypeError, match="non-JSON-compatible"):
        ConformanceArtifact(
            artifact_schema_version=artifact.artifact_schema_version,
            artifact_kind=artifact.artifact_kind,
            validation_profile=artifact.validation_profile,
            trace_json=artifact.trace_json,
            continuation_root_refs=artifact.continuation_root_refs,
            continuation_objects=artifact.continuation_objects,
            program_ref=artifact.program_ref,
            program_profile=artifact.program_profile,
            source_outcome_json={"value": object()},
            schema_versions=artifact.schema_versions,
            tool_versions=artifact.tool_versions,
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
                            handler_id="conformance-artifact-test.handler.v1",
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


def _publication_trace_result() -> TraceResult:
    return run_trace(
        elaborate_publication_experimental(
            Handle(
                Perform("eff.fork", Lit(None)),
                HandlerEnv(
                    (
                        StaticHandlerInstall(
                            effect_kind="eff.fork",
                            handler_id="conformance-artifact-publication.handler.v1",
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


def _continuation_evidence_ref(result: TraceResult, trace_ref: str) -> str:
    evidence = result.require_debug_evidence()
    return evidence.continuation_ref_map.get(trace_ref, trace_ref)
