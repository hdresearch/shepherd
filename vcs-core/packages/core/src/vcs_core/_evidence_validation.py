"""Private evidence-ref validation helpers for transition-kernel records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._transition_kernel_records import EvidenceRecord, EvidenceRef

EvidenceResolver = Callable[[EvidenceRef], EvidenceRecord | None]


@dataclass(frozen=True)
class EvidenceCitationScope:
    operation_id: str
    binding: str
    store_id: str
    substrate_kind: str


def validate_preparation_evidence_refs(
    evidence_refs: tuple[EvidenceRef, ...],
    *,
    cited_evidence_refs: tuple[EvidenceRef, ...] = (),
    expected_digests: tuple[str, ...],
    scope: EvidenceCitationScope,
    resolver: EvidenceResolver | None,
) -> tuple[EvidenceRecord, ...]:
    """Validate prepared-revision evidence and its local/cited authority split."""
    if len(set(evidence_refs)) != len(evidence_refs):
        raise InvalidRepositoryStateError("revision preparation evidence_refs contain duplicate refs")
    if len(set(cited_evidence_refs)) != len(cited_evidence_refs):
        raise InvalidRepositoryStateError("revision preparation cited_evidence_refs contain duplicate refs")
    evidence_ref_digests = tuple(ref.evidence_digest for ref in evidence_refs)
    if len(set(evidence_ref_digests)) != len(evidence_ref_digests):
        raise InvalidRepositoryStateError("revision preparation evidence_refs contain duplicate evidence digests")
    if len(set(expected_digests)) != len(expected_digests):
        raise InvalidRepositoryStateError("revision preparation evidence_digests contain duplicate values")
    if sorted(evidence_ref_digests) != sorted(expected_digests):
        raise InvalidRepositoryStateError("revision preparation evidence_refs disagree with evidence_digests")
    evidence_ref_set = set(evidence_refs)
    cited_ref_set = set(cited_evidence_refs)
    if not cited_ref_set.issubset(evidence_ref_set):
        raise InvalidRepositoryStateError("revision preparation cited_evidence_refs are not evidence refs")
    cited_count = len(cited_evidence_refs)
    if cited_count and evidence_refs[-cited_count:] != cited_evidence_refs:
        raise InvalidRepositoryStateError("revision preparation cited_evidence_refs must be a suffix of evidence_refs")
    if evidence_refs and resolver is None:
        raise InvalidRepositoryStateError("revision preparation evidence_refs require a resolver")

    records: list[EvidenceRecord] = []
    for evidence_ref in evidence_refs:
        assert resolver is not None
        resolved = resolver(evidence_ref)
        if resolved is None:
            raise InvalidRepositoryStateError("revision preparation evidence ref did not resolve")
        _validate_evidence_ref_integrity(evidence_ref, resolved)
        if evidence_ref not in cited_ref_set and resolved.operation_id != scope.operation_id:
            raise InvalidRepositoryStateError("resolved evidence operation_id disagrees with preparation")
        if resolved.binding != scope.binding:
            raise InvalidRepositoryStateError("resolved evidence binding disagrees with preparation")
        if resolved.store_id != scope.store_id:
            raise InvalidRepositoryStateError("resolved evidence store_id disagrees with preparation")
        if resolved.substrate_kind != scope.substrate_kind:
            raise InvalidRepositoryStateError("resolved evidence substrate kind disagrees with preparation")
        records.append(resolved)
    return tuple(records)


def _validate_evidence_ref_integrity(evidence_ref: EvidenceRef, resolved: EvidenceRecord) -> None:
    if resolved.evidence_digest() != evidence_ref.evidence_digest:
        raise InvalidRepositoryStateError("resolved evidence_digest disagrees with evidence ref")
    if resolved.record_digest() != evidence_ref.record_digest:
        raise InvalidRepositoryStateError("resolved record_digest disagrees with evidence ref")
    if resolved.payload_digest != evidence_ref.payload_digest:
        raise InvalidRepositoryStateError("resolved payload_digest disagrees with evidence ref")
