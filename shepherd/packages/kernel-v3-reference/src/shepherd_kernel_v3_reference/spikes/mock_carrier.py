"""Tiny mock-carrier pressure check for semantic transition batches.

This module is intentionally not an adapter interface. It only proves that the
semantic batch/admission/evidence shapes can describe world-result provenance
without importing a real carrier or storage layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from shepherd_kernel_v3_reference.kernel.refs import content_ref
from shepherd_kernel_v3_reference.semantic import (
    AdmissionBasis,
    CanonicalRefMap,
    ContinuationSource,
    ExternalEvidenceLink,
    ObservedFrontier,
    SemanticTransitionBatch,
)


@dataclass(frozen=True)
class MockCarrierEvidence:
    """Stable mock evidence for a world-affecting operation result."""

    external_ref: str
    effect_kind: str
    payload: Any
    result: Any
    status: str = "completed"
    schema_ref: str = "mock-carrier.evidence.v0"

    @property
    def digest(self) -> str:
        return content_ref("mock-evidence", asdict(self))


def build_mock_resume_batch(
    *,
    program_ref: str,
    parent_transition_ref: str,
    declaration_record_ref: str,
    source: ContinuationSource,
    evidence: MockCarrierEvidence,
    transition_id: str = "transition:mock-resume:0",
    resume_record_ref: str = "record:mock-resume:0",
) -> SemanticTransitionBatch:
    """Build one evidence-linked unhandled-resume semantic batch."""

    if source.source_kind != "UnhandledSuspension":
        raise ValueError("mock pressure check expects an UnhandledSuspension source")

    evidence_digest = evidence.digest
    admission = AdmissionBasis(
        source_ref=source.source_ref,
        source_kind=source.source_kind,
        source_generation=source.source_generation,
        observed_frontier=ObservedFrontier((declaration_record_ref,)),
        source_path_ref=source.source_path_ref,
        input_value_or_digest=evidence_digest,
        idempotency_key=f"idempotency:{source.source_ref}:{evidence_digest}",
        one_shot_key=source.one_shot_key,
        profile=source.profile,
        program_ref=program_ref,
        external_evidence_refs_or_digests=(evidence_digest,),
    )
    declaration_link = ExternalEvidenceLink(
        semantic_record_ref=declaration_record_ref,
        relation="fulfilled_by",
        external_system_kind="mock",
        external_ref=evidence.external_ref,
        external_schema_ref=evidence.schema_ref,
        evidence_digest=evidence_digest,
        external_status=evidence.status,
    )
    resume_input_link = ExternalEvidenceLink(
        semantic_record_ref=resume_record_ref,
        relation="produced_resume_input",
        external_system_kind="mock",
        external_ref=evidence.external_ref,
        external_schema_ref=evidence.schema_ref,
        evidence_digest=evidence_digest,
        external_status=evidence.status,
    )

    return SemanticTransitionBatch(
        transition_id=transition_id,
        idempotency_key=admission.idempotency_key,
        transition_kind="unhandled_top_level_resume",
        admission_basis=admission,
        profile=source.profile,
        program_ref=program_ref,
        parent_transition_refs=(parent_transition_ref,),
        records=(
            {
                "ref": resume_record_ref,
                "record_type": "ContinuationResume",
                "source_ref": source.source_ref,
                "source_record_type": source.source_kind,
                "continuation_ref": source.continuation_ref,
                "branch_ref": source.branch_ref,
                "value_digest": evidence_digest,
            },
        ),
        ref_map=CanonicalRefMap(),
        external_evidence_links=(declaration_link, resume_input_link),
    )
