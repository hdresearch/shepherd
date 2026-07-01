"""Private transition-kernel lowering helpers for substrate candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from vcs_core._transition_kernel_records import (
    EvidenceRecord,
    EvidenceRef,
    LogicalTransition,
    PayloadDescriptorClaim,
    PreparedRevisionPlan,
    RelationshipRequirement,
)
from vcs_core._world_types import canonical_digest

if TYPE_CHECKING:
    import pygit2

    from vcs_core._substrate_driver import RevisionContentDraft
    from vcs_core._substrate_store import SubstrateStore


@dataclass(frozen=True)
class PreparedCandidateDraft:
    transition: LogicalTransition
    plan: PreparedRevisionPlan
    evidence_records: tuple[EvidenceRecord, ...]
    payload_descriptor_claim: PayloadDescriptorClaim
    payload: dict[str, Any]
    parents: tuple[str | pygit2.Oid, ...]
    cited_evidence_refs: tuple[EvidenceRef, ...] = ()
    content: RevisionContentDraft | None = None


class TransitionKernelDriver(Protocol):
    @property
    def driver_id(self) -> str: ...

    @property
    def driver_version(self) -> str: ...

    def prepare_candidate(
        self,
        *,
        store: SubstrateStore,
        operation_id: str,
        binding: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...],
        ingress_kind: str,
        semantic_op: str,
        relationship_requirements: tuple[RelationshipRequirement, ...],
    ) -> PreparedCandidateDraft: ...


@dataclass(frozen=True)
class JsonPayloadTransitionDriver:
    """Canonical lowering for JSON-backed substrate candidate revisions."""

    driver_id: str
    driver_version: str = "v1"
    materialization_class: str = "external"

    def prepare_candidate(
        self,
        *,
        store: SubstrateStore,
        operation_id: str,
        binding: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...],
        ingress_kind: str,
        semantic_op: str,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
    ) -> PreparedCandidateDraft:
        parent_heads = tuple(str(parent) for parent in parents)
        payload_digest = canonical_digest(payload)
        payload_descriptor_claim = PayloadDescriptorClaim.for_json_payload(payload)
        evidence = EvidenceRecord(
            operation_id=operation_id,
            binding=binding,
            store_id=store.identity.store_id,
            substrate_kind=store.identity.kind,
            ingress_kind=ingress_kind,
            evidence_kind=f"{ingress_kind}:{semantic_op}",
            payload_digest=payload_digest,
            stable_observation={
                "binding": binding,
                "store_id": store.identity.store_id,
                "resource_id": store.identity.resource_id,
                "substrate_kind": store.identity.kind,
                "semantic_op": semantic_op,
                "parent_heads": list(parent_heads),
                "payload_digest": payload_digest,
            },
            mechanism=self.driver_id,
        )
        transition = LogicalTransition(
            binding=binding,
            store_id=store.identity.store_id,
            resource_id=store.identity.resource_id,
            substrate_kind=store.identity.kind,
            driver=self.driver_id,
            driver_version=self.driver_version,
            base_heads=parent_heads,
            ingress_kind=ingress_kind,
            semantic_op=semantic_op,
            payload_digest=payload_digest,
            evidence_digests=(evidence.evidence_digest(),),
            requirements=relationship_requirements,
        )
        plan = PreparedRevisionPlan(
            binding=binding,
            store_id=store.identity.store_id,
            transition_digest=transition.transition_digest(),
            base_heads=transition.base_heads,
            expected_parent_heads=parent_heads,
            content_digest=payload_digest,
            materialization_class=self.materialization_class,
            entries=({"path": "revision.json", "payload_digest": payload_digest},),
        )
        return PreparedCandidateDraft(
            transition=transition,
            plan=plan,
            evidence_records=(evidence,),
            payload_descriptor_claim=payload_descriptor_claim,
            payload=payload,
            parents=parents,
        )
