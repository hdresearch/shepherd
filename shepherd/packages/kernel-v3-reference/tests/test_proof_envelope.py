from __future__ import annotations

import pytest

from shepherd_kernel_v3_reference.proof_envelope import (
    EXTENSION_PROOF_SURFACE_THEOREM_IDS,
    PROOF_SURFACE_THEOREM_IDS,
    ProofEnvelope,
    ProofEnvelopeError,
    ProofProfile,
    ProofStrength,
    classify_trace_envelope,
    proof_envelope_from_json,
    reference_core_a_envelope,
    runtime_only_envelope,
)
from shepherd_kernel_v3_reference.trace.records import EffectDeclaration

PROGRAM_REF = "program:sha256:" + "0" * 64
TRACE_REF = "trace:sha256:" + "1" * 64
PROOF_EVIDENCE_REF = "proof-evidence:sha256:" + "a" * 64


def test_runtime_only_envelope_is_not_reference_or_lean_backed() -> None:
    envelope = runtime_only_envelope(reason="ordinary-python-runtime")

    assert envelope.profile is ProofProfile.RUNTIME_ONLY
    assert envelope.strength is ProofStrength.RUNTIME_ONLY
    assert envelope.theorem_ids == ()
    assert not envelope.reference_validated
    assert not envelope.lean_backed
    assert not envelope.proof_backed
    assert envelope.to_json()["metadata"] == {"reason": "ordinary-python-runtime"}


def test_reference_core_a_envelope_validates_trace_and_is_stably_addressed() -> None:
    envelope = reference_core_a_envelope((), program_ref=PROGRAM_REF)

    assert envelope.profile is ProofProfile.REFERENCE_CORE_A
    assert envelope.strength is ProofStrength.REFERENCE_VALIDATED
    assert envelope.reference_validated
    assert not envelope.lean_backed
    assert not envelope.proof_backed
    assert envelope.program_ref == PROGRAM_REF
    assert envelope.trace_ref is not None
    assert envelope.evidence_id is not None
    assert envelope.evidence_id.startswith("proof-evidence:sha256:")
    assert envelope.envelope_ref().startswith("proof-envelope:sha256:")


def test_envelope_json_round_trips() -> None:
    envelope = reference_core_a_envelope((), program_ref=PROGRAM_REF)

    decoded = proof_envelope_from_json(envelope.to_json())

    assert decoded == envelope
    assert decoded.envelope_ref() == envelope.envelope_ref()


def test_classifier_does_not_promote_invalid_trace() -> None:
    invalid_trace = (
        EffectDeclaration(
            ref="declaration:sha256:1",
            program_ref=None,
            effect_kind="effect",
            payload={},
            full_continuation_ref="continuation:sha256:full",
            branch_ref="branch:root",
            payload_schema_ref=None,
            operation_result_schema_ref=None,
        ),
    )

    envelope = classify_trace_envelope(invalid_trace)

    assert envelope.profile is ProofProfile.RUNTIME_ONLY
    assert envelope.strength is ProofStrength.RUNTIME_ONLY


def test_lean_backed_profiles_require_evidence() -> None:
    with pytest.raises(ProofEnvelopeError, match="require evidence_id"):
        ProofEnvelope(
            profile=ProofProfile.CORE_A,
            strength=ProofStrength.SEMANTIC_ADEQUACY,
            program_ref=PROGRAM_REF,
            trace_ref=TRACE_REF,
        )


def test_non_runtime_profiles_require_program_and_trace_refs() -> None:
    with pytest.raises(ProofEnvelopeError, match="require program_ref"):
        ProofEnvelope(
            profile=ProofProfile.REFERENCE_CORE_A,
            strength=ProofStrength.REFERENCE_VALIDATED,
            evidence_id=PROOF_EVIDENCE_REF,
            trace_ref=TRACE_REF,
        )

    with pytest.raises(ProofEnvelopeError, match="require trace_ref"):
        ProofEnvelope(
            profile=ProofProfile.REFERENCE_CORE_A,
            strength=ProofStrength.REFERENCE_VALIDATED,
            evidence_id=PROOF_EVIDENCE_REF,
            program_ref=PROGRAM_REF,
        )


def test_non_runtime_profiles_require_proof_evidence_ref() -> None:
    with pytest.raises(ProofEnvelopeError, match="proof-evidence"):
        ProofEnvelope(
            profile=ProofProfile.REFERENCE_CORE_A,
            strength=ProofStrength.REFERENCE_VALIDATED,
            evidence_id="proof-envelope-evidence:sha256:" + "a" * 64,
            program_ref=PROGRAM_REF,
            trace_ref=TRACE_REF,
        )


def test_non_runtime_profiles_require_content_addressed_program_and_trace_refs() -> None:
    with pytest.raises(ProofEnvelopeError, match="program_ref"):
        ProofEnvelope(
            profile=ProofProfile.REFERENCE_CORE_A,
            strength=ProofStrength.REFERENCE_VALIDATED,
            evidence_id=PROOF_EVIDENCE_REF,
            program_ref="program:sha256:not-a-digest",
            trace_ref=TRACE_REF,
        )

    with pytest.raises(ProofEnvelopeError, match="trace_ref"):
        ProofEnvelope(
            profile=ProofProfile.REFERENCE_CORE_A,
            strength=ProofStrength.REFERENCE_VALIDATED,
            evidence_id=PROOF_EVIDENCE_REF,
            program_ref=PROGRAM_REF,
            trace_ref="trace:sha256:not-a-digest",
        )


def test_lean_backed_profile_gets_exact_theorem_surface() -> None:
    envelope = ProofEnvelope(
        profile=ProofProfile.CORE_A,
        strength=ProofStrength.SEMANTIC_ADEQUACY,
        evidence_id=PROOF_EVIDENCE_REF,
        program_ref=PROGRAM_REF,
        trace_ref=TRACE_REF,
    )

    assert envelope.lean_backed
    assert envelope.proof_backed
    assert envelope.theorem_ids == (
        "source_eval_to_machine",
        "coreA_machine_eval_to_source",
        "trace_monotonic",
    )
    assert set(envelope.theorem_ids).issubset(PROOF_SURFACE_THEOREM_IDS)


def test_profile_strength_mismatch_is_rejected() -> None:
    with pytest.raises(ProofEnvelopeError, match="cannot carry strength"):
        ProofEnvelope(
            profile=ProofProfile.REFERENCE_CORE_A,
            strength=ProofStrength.SEMANTIC_ADEQUACY,
            evidence_id=PROOF_EVIDENCE_REF,
            program_ref=PROGRAM_REF,
            trace_ref=TRACE_REF,
        )


def test_semantic_extension_requires_whitelisted_theorem_ids() -> None:
    envelope = ProofEnvelope(
        profile=ProofProfile.EXTENSION,
        strength=ProofStrength.SEMANTIC_ADEQUACY,
        evidence_id=PROOF_EVIDENCE_REF,
        program_ref=PROGRAM_REF,
        trace_ref=TRACE_REF,
        theorem_ids=EXTENSION_PROOF_SURFACE_THEOREM_IDS,
    )

    assert envelope.lean_backed
    assert not envelope.reference_validated
    assert envelope.theorem_ids == EXTENSION_PROOF_SURFACE_THEOREM_IDS

    with pytest.raises(ProofEnvelopeError, match="unsupported extension theorem ids"):
        ProofEnvelope(
            profile=ProofProfile.EXTENSION,
            strength=ProofStrength.SEMANTIC_ADEQUACY,
            evidence_id=PROOF_EVIDENCE_REF,
            program_ref=PROGRAM_REF,
            trace_ref=TRACE_REF,
            theorem_ids=("not_a_checked_theorem",),
        )
