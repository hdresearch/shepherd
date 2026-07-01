"""Capability spikes for the proposed transition-kernel standardization.

These tests intentionally use inert test-only DTOs. They make the proposed
digest and vocabulary rules executable without turning them into production API.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields

import pytest

from .world_vectors_v2_helpers import canonical_digest


def _digest(value: object) -> str:
    return canonical_digest(value)


def _sha(label: str) -> str:
    return _digest({"label": label})


def _canonical_sequence(items: Iterable[object]) -> list[object]:
    """Sort semantically unordered digest inputs by canonical identity."""
    return sorted(items, key=_digest)


@dataclass(frozen=True)
class EvidenceRecord:
    operation_id: str
    evidence_kind: str
    payload_digest: str
    stable_observation: dict[str, object]
    binding: str | None = None
    observed_at_unix_ns: int | None = None
    ref: str | None = None

    def evidence_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/evidence-stable/v1",
                "binding": self.binding,
                "evidence_kind": self.evidence_kind,
                "payload_digest": self.payload_digest,
                "stable_observation": self.stable_observation,
            }
        )

    def record_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/evidence-record/v1",
                "operation_id": self.operation_id,
                "binding": self.binding,
                "evidence_kind": self.evidence_kind,
                "payload_digest": self.payload_digest,
                "stable_observation": self.stable_observation,
                "observed_at_unix_ns": self.observed_at_unix_ns,
                "ref": self.ref,
                "evidence_digest": self.evidence_digest(),
            }
        )


@dataclass(frozen=True)
class EvidenceRef:
    ref: str
    evidence_digest: str
    record_digest: str
    payload_digest: str

    def to_json(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "evidence_digest": self.evidence_digest,
            "record_digest": self.record_digest,
            "payload_digest": self.payload_digest,
        }


@dataclass(frozen=True)
class RelationshipRequirement:
    binding: str
    relation: str
    target_binding: str
    target_head: str

    def to_json(self) -> dict[str, object]:
        return {
            "binding": self.binding,
            "relation": self.relation,
            "target_binding": self.target_binding,
            "target_head": self.target_head,
        }


@dataclass(frozen=True)
class RetentionPolicyRequirement:
    kind: str
    target: str
    digest: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "kind": self.kind,
            "target": self.target,
        }
        if self.digest is not None:
            value["digest"] = self.digest
        return value


@dataclass(frozen=True)
class RetainedRef:
    kind: str
    ref: str
    digest: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "kind": self.kind,
            "ref": self.ref,
        }
        if self.digest is not None:
            value["digest"] = self.digest
        return value


@dataclass(frozen=True)
class LogicalTransition:
    binding: str
    store_id: str
    resource_id: str
    substrate_kind: str
    driver: str
    driver_version: str
    base_heads: tuple[str, ...]
    ingress_kind: str
    semantic_op: str
    payload_digest: str
    evidence_digests: tuple[str, ...] = ()
    requirements: tuple[RelationshipRequirement, ...] = ()
    idempotency_key: str | None = None

    def transition_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/logical-transition/v1",
                "binding": self.binding,
                "store_id": self.store_id,
                "resource_id": self.resource_id,
                "substrate_kind": self.substrate_kind,
                "driver": self.driver,
                "driver_version": self.driver_version,
                "base_heads": list(self.base_heads),
                "ingress_kind": self.ingress_kind,
                "semantic_op": self.semantic_op,
                "payload_digest": self.payload_digest,
                "evidence_digests": _canonical_sequence(self.evidence_digests),
                "requirements": _canonical_sequence(requirement.to_json() for requirement in self.requirements),
                "idempotency_key": self.idempotency_key,
            }
        )


@dataclass(frozen=True)
class PreparedRevisionPlan:
    binding: str
    store_id: str
    transition_digest: str
    base_heads: tuple[str, ...]
    expected_parent_heads: tuple[str, ...]
    content_digest: str
    materialization_class: str
    entries: tuple[dict[str, object], ...]
    entry_ordering: str = "set"

    def plan_digest(self) -> str:
        if self.entry_ordering == "set":
            entries: list[object] = _canonical_sequence(self.entries)
        elif self.entry_ordering == "sequence":
            entries = list(self.entries)
        else:
            raise ValueError(f"unsupported revision plan entry ordering: {self.entry_ordering!r}")
        return _digest(
            {
                "schema": "vcscore/spike/revision-plan/v1",
                "binding": self.binding,
                "store_id": self.store_id,
                "transition_digest": self.transition_digest,
                "base_heads": list(self.base_heads),
                "expected_parent_heads": list(self.expected_parent_heads),
                "content_digest": self.content_digest,
                "materialization_class": self.materialization_class,
                "entry_ordering": self.entry_ordering,
                "entries": entries,
            }
        )


@dataclass(frozen=True)
class RevisionPreparationRecord:
    operation_id: str
    binding: str
    store_id: str
    resource_id: str
    transition_digest: str
    revision_plan_digest: str
    content_digest: str
    evidence_digests: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    relationship_requirements: tuple[RelationshipRequirement, ...] = ()

    def revision_preparation_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/revision-preparation/v1",
                "operation_id": self.operation_id,
                "binding": self.binding,
                "store_id": self.store_id,
                "resource_id": self.resource_id,
                "transition_digest": self.transition_digest,
                "revision_plan_digest": self.revision_plan_digest,
                "content_digest": self.content_digest,
                "evidence_digests": _canonical_sequence(self.evidence_digests),
                "evidence_refs": _canonical_sequence(ref.to_json() for ref in self.evidence_refs),
                "relationship_requirements": _canonical_sequence(
                    requirement.to_json() for requirement in self.relationship_requirements
                ),
            }
        )


@dataclass(frozen=True)
class CandidateCommitRecord:
    operation_id: str
    binding: str
    store_id: str
    resource_id: str
    candidate_head: str
    candidate_ref: str
    revision_preparation_digest: str

    def candidate_commit_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/candidate-commit/v1",
                "operation_id": self.operation_id,
                "binding": self.binding,
                "store_id": self.store_id,
                "resource_id": self.resource_id,
                "candidate_head": self.candidate_head,
                "candidate_ref": self.candidate_ref,
                "revision_preparation_digest": self.revision_preparation_digest,
            }
        )


@dataclass(frozen=True)
class HeadSelectionRecord:
    binding: str
    store_id: str
    resource_id: str
    selected_head: str
    selection_kind: str
    selected_from: str | None = None
    producer_world_oid: str | None = None
    relationship_requirements: tuple[RelationshipRequirement, ...] = ()
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = ()
    selection_policy_digest: str | None = None

    def selection_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/head-selection/v1",
                "binding": self.binding,
                "store_id": self.store_id,
                "resource_id": self.resource_id,
                "selected_head": self.selected_head,
                "selection_kind": self.selection_kind,
                "selected_from": self.selected_from,
                "producer_world_oid": self.producer_world_oid,
                "relationship_requirements": _canonical_sequence(
                    requirement.to_json() for requirement in self.relationship_requirements
                ),
                "retention_policy_requirements": _canonical_sequence(
                    requirement.to_json() for requirement in self.retention_policy_requirements
                ),
                "selection_policy_digest": self.selection_policy_digest,
            }
        )


@dataclass(frozen=True)
class HeadSelectionEvidence:
    operation_id: str
    binding: str
    store_id: str
    resource_id: str
    selected_head: str
    selection_digest: str
    revision_preparation_digest: str | None = None
    candidate_commit_digest: str | None = None
    candidate_ref: str | None = None
    producer_operation_id: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = ()

    def selection_evidence_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/head-selection-evidence/v1",
                "operation_id": self.operation_id,
                "binding": self.binding,
                "store_id": self.store_id,
                "resource_id": self.resource_id,
                "selected_head": self.selected_head,
                "selection_digest": self.selection_digest,
                "revision_preparation_digest": self.revision_preparation_digest,
                "candidate_commit_digest": self.candidate_commit_digest,
                "candidate_ref": self.candidate_ref,
                "producer_operation_id": self.producer_operation_id,
                "evidence_refs": _canonical_sequence(ref.to_json() for ref in self.evidence_refs),
                "retention_policy_requirements": _canonical_sequence(
                    requirement.to_json() for requirement in self.retention_policy_requirements
                ),
            }
        )


@dataclass(frozen=True)
class SelectionRetentionReceipt:
    operation_id: str
    world_oid: str
    binding: str
    store_id: str
    resource_id: str
    selected_head: str
    selection_digest: str
    retained_refs: tuple[RetainedRef, ...]
    authority_ref: str | None = None

    def selection_retention_receipt_digest(self) -> str:
        return _digest(
            {
                "schema": "vcscore/spike/selection-retention-receipt/v1",
                "operation_id": self.operation_id,
                "world_oid": self.world_oid,
                "binding": self.binding,
                "store_id": self.store_id,
                "resource_id": self.resource_id,
                "selected_head": self.selected_head,
                "selection_digest": self.selection_digest,
                "retained_refs": _canonical_sequence(ref.to_json() for ref in self.retained_refs),
                "authority_ref": self.authority_ref,
            }
        )


@dataclass(frozen=True)
class JournalOnlyOperation:
    operation_id: str
    input_world_oid: str
    evidence_refs: tuple[EvidenceRef, ...]
    task_trace_citations: tuple[dict[str, object], ...] = ()
    world_oid: str | None = None
    selected_head_pins: tuple[str, ...] = ()
    head_selections: tuple[HeadSelectionRecord, ...] = ()


_CANDIDATE_BACKED_SELECTION_KINDS = frozenset({"new-candidate", "child-produced"})


def _validate_candidate_backed_selection(selection: HeadSelectionRecord, evidence: HeadSelectionEvidence) -> None:
    if evidence.selection_digest != selection.selection_digest():
        raise ValueError("selection evidence disagrees with head selection")
    if evidence.selected_head != selection.selected_head:
        raise ValueError("selection evidence disagrees with selected head")
    candidate_fields = (
        evidence.revision_preparation_digest,
        evidence.candidate_commit_digest,
        evidence.candidate_ref,
    )
    carries_candidate_evidence = any(field is not None for field in candidate_fields)
    is_candidate_backed_kind = selection.selection_kind in _CANDIDATE_BACKED_SELECTION_KINDS
    if is_candidate_backed_kind and any(field is None for field in candidate_fields):
        raise ValueError("candidate-backed selection requires revision preparation, commit, and ref evidence")
    if not is_candidate_backed_kind and carries_candidate_evidence:
        raise ValueError("non-candidate selection must not carry candidate evidence")


def _evidence_ref(*, operation_id: str, ref: str, path: str = "artifact.txt") -> EvidenceRef:
    payload_digest = _sha(f"payload:{path}")
    record = EvidenceRecord(
        operation_id=operation_id,
        binding="workspace",
        evidence_kind="diff_scan",
        payload_digest=payload_digest,
        stable_observation={"path": path, "sha256": payload_digest},
        observed_at_unix_ns=123,
        ref=ref,
    )
    return EvidenceRef(
        ref=ref,
        evidence_digest=record.evidence_digest(),
        record_digest=record.record_digest(),
        payload_digest=payload_digest,
    )


def _workspace_transition(
    *, ingress_kind: str, evidence_ref: EvidenceRef, semantic_op: str = "FilePatch"
) -> LogicalTransition:
    return LogicalTransition(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        substrate_kind="filesystem",
        driver="builtin.filesystem",
        driver_version="spike",
        base_heads=("w42",),
        ingress_kind=ingress_kind,
        semantic_op=semantic_op,
        payload_digest=_sha("workspace W43"),
        evidence_digests=(evidence_ref.evidence_digest,),
    )


def _workspace_plan(transition: LogicalTransition) -> PreparedRevisionPlan:
    return PreparedRevisionPlan(
        binding=transition.binding,
        store_id=transition.store_id,
        transition_digest=transition.transition_digest(),
        base_heads=transition.base_heads,
        expected_parent_heads=transition.base_heads,
        content_digest=_sha("workspace tree W43"),
        materialization_class="external",
        entries=({"path": "artifact.txt", "payload_digest": transition.payload_digest},),
    )


def _candidate_preparation(
    *,
    operation_id: str,
    transition: LogicalTransition,
    plan: PreparedRevisionPlan,
    evidence_ref: EvidenceRef,
) -> RevisionPreparationRecord:
    return RevisionPreparationRecord(
        operation_id=operation_id,
        binding=transition.binding,
        store_id=transition.store_id,
        resource_id=transition.resource_id,
        transition_digest=transition.transition_digest(),
        revision_plan_digest=plan.plan_digest(),
        content_digest=plan.content_digest,
        evidence_digests=transition.evidence_digests,
        evidence_refs=(evidence_ref,),
    )


@pytest.mark.spike
def test_digest_taxonomy_separates_retry_stable_transition_from_operation_preparation() -> None:
    ref_a = _evidence_ref(operation_id="op-a", ref="refs/vcscore/evidence/op-a/1")
    ref_b = _evidence_ref(operation_id="op-b", ref="refs/vcscore/evidence/op-b/1")
    transition_a = _workspace_transition(ingress_kind="scan", evidence_ref=ref_a)
    transition_b = _workspace_transition(ingress_kind="scan", evidence_ref=ref_b)
    plan_a = _workspace_plan(transition_a)
    plan_b = _workspace_plan(transition_b)
    preparation_a = _candidate_preparation(
        operation_id="op-a",
        transition=transition_a,
        plan=plan_a,
        evidence_ref=ref_a,
    )
    preparation_b = _candidate_preparation(
        operation_id="op-b",
        transition=transition_b,
        plan=plan_b,
        evidence_ref=ref_b,
    )

    assert ref_a.evidence_digest == ref_b.evidence_digest
    assert ref_a.record_digest != ref_b.record_digest
    assert transition_a.transition_digest() == transition_b.transition_digest()
    assert plan_a.plan_digest() == plan_b.plan_digest()
    assert preparation_a.revision_preparation_digest() != preparation_b.revision_preparation_digest()


@pytest.mark.spike
def test_command_and_scan_can_share_content_digest_but_not_transition_digest() -> None:
    command_ref = _evidence_ref(operation_id="op-command", ref="refs/vcscore/evidence/op-command/1")
    scan_ref = _evidence_ref(operation_id="op-scan", ref="refs/vcscore/evidence/op-scan/1")
    command_transition = _workspace_transition(ingress_kind="command", evidence_ref=command_ref)
    scan_transition = _workspace_transition(ingress_kind="scan", evidence_ref=scan_ref)
    command_plan = _workspace_plan(command_transition)
    scan_plan = _workspace_plan(scan_transition)

    assert command_plan.content_digest == scan_plan.content_digest
    assert command_transition.transition_digest() != scan_transition.transition_digest()
    assert command_plan.plan_digest() != scan_plan.plan_digest()


@pytest.mark.spike
def test_transition_identity_excludes_output_and_operation_context_fields() -> None:
    evidence_ref = _evidence_ref(operation_id="op-a", ref="refs/vcscore/evidence/op-a/1")
    transition = _workspace_transition(ingress_kind="command", evidence_ref=evidence_ref)
    plan = _workspace_plan(transition)
    preparation = _candidate_preparation(
        operation_id="op-a",
        transition=transition,
        plan=plan,
        evidence_ref=evidence_ref,
    )
    commit_a = CandidateCommitRecord(
        operation_id="op-a",
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        candidate_head="a" * 40,
        candidate_ref="refs/vcscore/candidates/op-a/workspace",
        revision_preparation_digest=preparation.revision_preparation_digest(),
    )
    commit_b = CandidateCommitRecord(
        operation_id="op-a",
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        candidate_head="b" * 40,
        candidate_ref="refs/vcscore/candidates/op-a/workspace-retry",
        revision_preparation_digest=preparation.revision_preparation_digest(),
    )

    assert "input_world_oid" not in {field.name for field in fields(LogicalTransition)}
    assert (
        transition.transition_digest()
        == _workspace_transition(
            ingress_kind="command",
            evidence_ref=evidence_ref,
        ).transition_digest()
    )
    assert commit_a.candidate_commit_digest() != commit_b.candidate_commit_digest()


@pytest.mark.spike
def test_unordered_digest_collections_are_canonicalized() -> None:
    ref_a = _evidence_ref(operation_id="op-order", ref="refs/vcscore/evidence/op-order/a", path="a.txt")
    ref_b = _evidence_ref(operation_id="op-order", ref="refs/vcscore/evidence/op-order/b", path="b.txt")
    requirement_a = RelationshipRequirement(
        binding="workspace",
        relation="visible-from",
        target_binding="session",
        target_head="7" * 40,
    )
    requirement_b = RelationshipRequirement(
        binding="workspace",
        relation="descends-from",
        target_binding="workspace",
        target_head="2" * 40,
    )
    transition_ab = LogicalTransition(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        substrate_kind="filesystem",
        driver="builtin.filesystem",
        driver_version="spike",
        base_heads=("w42",),
        ingress_kind="scan",
        semantic_op="FilePatch",
        payload_digest=_sha("workspace W43"),
        evidence_digests=(ref_a.evidence_digest, ref_b.evidence_digest),
        requirements=(requirement_a, requirement_b),
    )
    transition_ba = LogicalTransition(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        substrate_kind="filesystem",
        driver="builtin.filesystem",
        driver_version="spike",
        base_heads=("w42",),
        ingress_kind="scan",
        semantic_op="FilePatch",
        payload_digest=_sha("workspace W43"),
        evidence_digests=(ref_b.evidence_digest, ref_a.evidence_digest),
        requirements=(requirement_b, requirement_a),
    )
    plan_ab = PreparedRevisionPlan(
        binding="workspace",
        store_id="store_workspace",
        transition_digest=transition_ab.transition_digest(),
        base_heads=transition_ab.base_heads,
        expected_parent_heads=transition_ab.base_heads,
        content_digest=_sha("workspace tree W43"),
        materialization_class="external",
        entries=(
            {"path": "a.txt", "payload_digest": ref_a.payload_digest},
            {"path": "b.txt", "payload_digest": ref_b.payload_digest},
        ),
    )
    plan_ba = PreparedRevisionPlan(
        binding="workspace",
        store_id="store_workspace",
        transition_digest=transition_ba.transition_digest(),
        base_heads=transition_ba.base_heads,
        expected_parent_heads=transition_ba.base_heads,
        content_digest=_sha("workspace tree W43"),
        materialization_class="external",
        entries=(
            {"path": "b.txt", "payload_digest": ref_b.payload_digest},
            {"path": "a.txt", "payload_digest": ref_a.payload_digest},
        ),
    )
    preparation_ab = RevisionPreparationRecord(
        operation_id="op-order",
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        transition_digest=transition_ab.transition_digest(),
        revision_plan_digest=plan_ab.plan_digest(),
        content_digest=plan_ab.content_digest,
        evidence_digests=transition_ab.evidence_digests,
        evidence_refs=(ref_a, ref_b),
        relationship_requirements=(requirement_a, requirement_b),
    )
    preparation_ba = RevisionPreparationRecord(
        operation_id="op-order",
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        transition_digest=transition_ba.transition_digest(),
        revision_plan_digest=plan_ba.plan_digest(),
        content_digest=plan_ba.content_digest,
        evidence_digests=transition_ba.evidence_digests,
        evidence_refs=(ref_b, ref_a),
        relationship_requirements=(requirement_b, requirement_a),
    )
    selection_ab = HeadSelectionRecord(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        selected_head="4" * 40,
        selection_kind="new-candidate",
        relationship_requirements=(requirement_a, requirement_b),
        retention_policy_requirements=(
            RetentionPolicyRequirement(kind="selected-head-pin", target="4" * 40),
            RetentionPolicyRequirement(kind="materialization-receipt", target="target:checkout-main"),
        ),
        selection_policy_digest=_sha("select candidate"),
    )
    selection_ba = HeadSelectionRecord(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        selected_head="4" * 40,
        selection_kind="new-candidate",
        relationship_requirements=(requirement_b, requirement_a),
        retention_policy_requirements=(
            RetentionPolicyRequirement(kind="materialization-receipt", target="target:checkout-main"),
            RetentionPolicyRequirement(kind="selected-head-pin", target="4" * 40),
        ),
        selection_policy_digest=_sha("select candidate"),
    )

    assert transition_ab.transition_digest() == transition_ba.transition_digest()
    assert plan_ab.plan_digest() == plan_ba.plan_digest()
    assert preparation_ab.revision_preparation_digest() == preparation_ba.revision_preparation_digest()
    assert selection_ab.selection_digest() == selection_ba.selection_digest()


@pytest.mark.spike
def test_revision_plan_entry_ordering_is_explicit() -> None:
    transition_digest = _sha("ordered trace transition")
    unordered_ab = PreparedRevisionPlan(
        binding="trace",
        store_id="store_trace",
        transition_digest=transition_digest,
        base_heads=("t1",),
        expected_parent_heads=("t1",),
        content_digest=_sha("trace content"),
        materialization_class="noop",
        entry_ordering="set",
        entries=(
            {"event": "tool-call", "sequence": 1},
            {"event": "tool-result", "sequence": 2},
        ),
    )
    unordered_ba = PreparedRevisionPlan(
        binding="trace",
        store_id="store_trace",
        transition_digest=transition_digest,
        base_heads=("t1",),
        expected_parent_heads=("t1",),
        content_digest=_sha("trace content"),
        materialization_class="noop",
        entry_ordering="set",
        entries=(
            {"event": "tool-result", "sequence": 2},
            {"event": "tool-call", "sequence": 1},
        ),
    )
    ordered_ab = PreparedRevisionPlan(
        binding="trace",
        store_id="store_trace",
        transition_digest=transition_digest,
        base_heads=("t1",),
        expected_parent_heads=("t1",),
        content_digest=_sha("trace content"),
        materialization_class="noop",
        entry_ordering="sequence",
        entries=unordered_ab.entries,
    )
    ordered_ba = PreparedRevisionPlan(
        binding="trace",
        store_id="store_trace",
        transition_digest=transition_digest,
        base_heads=("t1",),
        expected_parent_heads=("t1",),
        content_digest=_sha("trace content"),
        materialization_class="noop",
        entry_ordering="sequence",
        entries=unordered_ba.entries,
    )

    assert unordered_ab.plan_digest() == unordered_ba.plan_digest()
    assert ordered_ab.plan_digest() != ordered_ba.plan_digest()
    with pytest.raises(ValueError, match="unsupported revision plan entry ordering"):
        PreparedRevisionPlan(
            binding="trace",
            store_id="store_trace",
            transition_digest=transition_digest,
            base_heads=("t1",),
            expected_parent_heads=("t1",),
            content_digest=_sha("trace content"),
            materialization_class="noop",
            entry_ordering="priority",
            entries=(),
        ).plan_digest()


@pytest.mark.spike
def test_motivating_loop_fits_candidate_preparation_and_head_selection_records() -> None:
    evidence_ref = _evidence_ref(operation_id="op-child", ref="refs/vcscore/evidence/op-child/workspace")
    workspace_transition = _workspace_transition(ingress_kind="command", evidence_ref=evidence_ref)
    workspace_plan = _workspace_plan(workspace_transition)
    workspace_preparation = _candidate_preparation(
        operation_id="op-child",
        transition=workspace_transition,
        plan=workspace_plan,
        evidence_ref=evidence_ref,
    )
    workspace_commit = CandidateCommitRecord(
        operation_id="op-child",
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        candidate_head="4" * 40,
        candidate_ref="refs/vcscore/candidates/op-child/workspace",
        revision_preparation_digest=workspace_preparation.revision_preparation_digest(),
    )

    workspace_selection = HeadSelectionRecord(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        selected_head=workspace_commit.candidate_head,
        selection_kind="child-produced",
        selected_from="2" * 40,
        producer_world_oid="c" * 40,
        retention_policy_requirements=(
            RetentionPolicyRequirement(kind="selected-head-pin", target=workspace_commit.candidate_head),
        ),
        selection_policy_digest=_sha("take child workspace by reference"),
    )
    workspace_selection_evidence = HeadSelectionEvidence(
        operation_id="op-parent-merge",
        binding=workspace_selection.binding,
        store_id=workspace_selection.store_id,
        resource_id=workspace_selection.resource_id,
        selected_head=workspace_selection.selected_head,
        selection_digest=workspace_selection.selection_digest(),
        revision_preparation_digest=workspace_preparation.revision_preparation_digest(),
        candidate_commit_digest=workspace_commit.candidate_commit_digest(),
        candidate_ref=workspace_commit.candidate_ref,
        producer_operation_id="op-child",
        evidence_refs=(evidence_ref,),
        retention_policy_requirements=workspace_selection.retention_policy_requirements,
    )
    workspace_retention_receipt = SelectionRetentionReceipt(
        operation_id="op-parent-merge",
        world_oid="p" * 40,
        binding=workspace_selection.binding,
        store_id=workspace_selection.store_id,
        resource_id=workspace_selection.resource_id,
        selected_head=workspace_selection.selected_head,
        selection_digest=workspace_selection.selection_digest(),
        retained_refs=(
            RetainedRef(
                kind="selected-head-pin",
                ref="refs/vcscore/pins/world/store_world/op-parent-merge/workspace",
            ),
            RetainedRef(kind="candidate-ref", ref=workspace_commit.candidate_ref),
            RetainedRef(kind="evidence-ref", ref=evidence_ref.ref, digest=evidence_ref.record_digest),
        ),
        authority_ref="refs/vcscore/ground",
    )
    session_selection = HeadSelectionRecord(
        binding="session",
        store_id="store_session",
        resource_id="session:child-task",
        selected_head="7" * 40,
        selection_kind="checkpoint",
        selected_from="9" * 40,
        retention_policy_requirements=(RetentionPolicyRequirement(kind="selected-head-pin", target="7" * 40),),
        selection_policy_digest=_sha("session by value from S7"),
    )

    assert workspace_selection.selection_digest() != session_selection.selection_digest()
    assert workspace_selection.selection_kind == "child-produced"
    assert session_selection.selection_kind == "checkpoint"
    assert (
        workspace_selection_evidence.revision_preparation_digest == workspace_preparation.revision_preparation_digest()
    )
    assert "world_oid" not in {field.name for field in fields(HeadSelectionEvidence)}
    assert "selected_head_pin_ref" not in {field.name for field in fields(HeadSelectionEvidence)}
    _validate_candidate_backed_selection(workspace_selection, workspace_selection_evidence)

    retry_workspace_selection = HeadSelectionRecord(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        selected_head=workspace_commit.candidate_head,
        selection_kind="child-produced",
        selected_from="2" * 40,
        producer_world_oid="c" * 40,
        retention_policy_requirements=workspace_selection.retention_policy_requirements,
        selection_policy_digest=workspace_selection.selection_policy_digest,
    )
    retry_workspace_selection_evidence = HeadSelectionEvidence(
        operation_id="op-parent-merge-retry",
        binding=retry_workspace_selection.binding,
        store_id=retry_workspace_selection.store_id,
        resource_id=retry_workspace_selection.resource_id,
        selected_head=retry_workspace_selection.selected_head,
        selection_digest=retry_workspace_selection.selection_digest(),
        revision_preparation_digest=_sha("equivalent re-preparation"),
        candidate_commit_digest=_sha("retry candidate commit"),
        candidate_ref="refs/vcscore/candidates/op-parent-merge-retry/workspace",
        producer_operation_id="op-child",
        evidence_refs=(evidence_ref,),
        retention_policy_requirements=workspace_selection_evidence.retention_policy_requirements,
    )
    retry_retention_receipt = SelectionRetentionReceipt(
        operation_id="op-parent-merge-retry",
        world_oid="q" * 40,
        binding=retry_workspace_selection.binding,
        store_id=retry_workspace_selection.store_id,
        resource_id=retry_workspace_selection.resource_id,
        selected_head=retry_workspace_selection.selected_head,
        selection_digest=retry_workspace_selection.selection_digest(),
        retained_refs=(
            RetainedRef(kind="selected-head-pin", ref="refs/vcscore/pins/world/store_world/retry/workspace"),
            RetainedRef(kind="candidate-ref", ref="refs/vcscore/candidates/op-parent-merge-retry/workspace"),
            RetainedRef(kind="evidence-ref", ref=evidence_ref.ref, digest=evidence_ref.record_digest),
        ),
        authority_ref="refs/vcscore/ground",
    )
    assert retry_workspace_selection.selection_digest() == workspace_selection.selection_digest()
    assert (
        retry_workspace_selection_evidence.selection_evidence_digest()
        != workspace_selection_evidence.selection_evidence_digest()
    )
    assert (
        retry_retention_receipt.selection_retention_receipt_digest()
        != workspace_retention_receipt.selection_retention_receipt_digest()
    )


@pytest.mark.spike
def test_candidate_backed_selection_requires_commit_evidence_without_digesting_candidate_ref() -> None:
    valid_selection = HeadSelectionRecord(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        selected_head="4" * 40,
        selection_kind="new-candidate",
        retention_policy_requirements=(RetentionPolicyRequirement(kind="selected-head-pin", target="4" * 40),),
        selection_policy_digest=_sha("select new workspace candidate"),
    )
    valid_evidence = HeadSelectionEvidence(
        operation_id="op-candidate",
        binding=valid_selection.binding,
        store_id=valid_selection.store_id,
        resource_id=valid_selection.resource_id,
        selected_head=valid_selection.selected_head,
        selection_digest=valid_selection.selection_digest(),
        revision_preparation_digest=_sha("revision preparation A"),
        candidate_commit_digest=_sha("candidate commit A"),
        candidate_ref="refs/vcscore/candidates/op-candidate/workspace",
    )
    retry_selection = HeadSelectionRecord(
        binding="workspace",
        store_id="store_workspace",
        resource_id="fs:repo-main",
        selected_head="4" * 40,
        selection_kind="new-candidate",
        retention_policy_requirements=valid_selection.retention_policy_requirements,
        selection_policy_digest=valid_selection.selection_policy_digest,
    )
    retry_evidence = HeadSelectionEvidence(
        operation_id="op-candidate-retry",
        binding=retry_selection.binding,
        store_id=retry_selection.store_id,
        resource_id=retry_selection.resource_id,
        selected_head=retry_selection.selected_head,
        selection_digest=retry_selection.selection_digest(),
        revision_preparation_digest=_sha("revision preparation B"),
        candidate_commit_digest=_sha("candidate commit B"),
        candidate_ref="refs/vcscore/candidates/op-candidate-retry/workspace",
    )
    invalid_evidence = HeadSelectionEvidence(
        operation_id="op-candidate",
        binding=valid_selection.binding,
        store_id=valid_selection.store_id,
        resource_id=valid_selection.resource_id,
        selected_head=valid_selection.selected_head,
        selection_digest=valid_selection.selection_digest(),
        revision_preparation_digest=_sha("revision preparation A"),
    )
    invalid_checkpoint_evidence = HeadSelectionEvidence(
        operation_id="op-checkpoint",
        binding="session",
        store_id="store_session",
        resource_id="session:child-task",
        selected_head="7" * 40,
        selection_digest=HeadSelectionRecord(
            binding="session",
            store_id="store_session",
            resource_id="session:child-task",
            selected_head="7" * 40,
            selection_kind="checkpoint",
            retention_policy_requirements=(RetentionPolicyRequirement(kind="selected-head-pin", target="7" * 40),),
            selection_policy_digest=_sha("session checkpoint"),
        ).selection_digest(),
        revision_preparation_digest=_sha("should not be here"),
        candidate_commit_digest=_sha("should not be here either"),
        candidate_ref="refs/vcscore/candidates/op-checkpoint/session",
    )
    checkpoint_selection = HeadSelectionRecord(
        binding="session",
        store_id="store_session",
        resource_id="session:child-task",
        selected_head="7" * 40,
        selection_kind="checkpoint",
        retention_policy_requirements=(RetentionPolicyRequirement(kind="selected-head-pin", target="7" * 40),),
        selection_policy_digest=_sha("session checkpoint"),
    )

    _validate_candidate_backed_selection(valid_selection, valid_evidence)
    assert valid_selection.selection_digest() == retry_selection.selection_digest()
    assert valid_evidence.selection_evidence_digest() != retry_evidence.selection_evidence_digest()
    with pytest.raises(ValueError, match="candidate-backed selection requires revision preparation"):
        _validate_candidate_backed_selection(valid_selection, invalid_evidence)
    with pytest.raises(ValueError, match="non-candidate selection must not carry candidate evidence"):
        _validate_candidate_backed_selection(checkpoint_selection, invalid_checkpoint_evidence)


@pytest.mark.spike
def test_journal_only_operation_records_evidence_without_world_selection_surfaces() -> None:
    evidence_ref = _evidence_ref(operation_id="op-tool-call", ref="refs/vcscore/evidence/op-tool-call/1")
    operation = JournalOnlyOperation(
        operation_id="op-tool-call",
        input_world_oid="1" * 40,
        evidence_refs=(evidence_ref,),
        task_trace_citations=({"trace_event": "tool-call", "evidence_digest": evidence_ref.evidence_digest},),
    )

    assert operation.input_world_oid == "1" * 40
    assert operation.evidence_refs == (evidence_ref,)
    assert operation.world_oid is None
    assert operation.selected_head_pins == ()
    assert operation.head_selections == ()

    later_transition = LogicalTransition(
        binding="trace",
        store_id="store_trace",
        resource_id="trace:parent",
        substrate_kind="shepherd.trace",
        driver="builtin.task_trace",
        driver_version="spike",
        base_heads=("t10",),
        ingress_kind="replay",
        semantic_op="TaskTraceAppend",
        payload_digest=_sha("append cited tool call"),
        evidence_digests=(operation.evidence_refs[0].evidence_digest,),
    )
    assert operation.evidence_refs[0].evidence_digest in later_transition.evidence_digests


@pytest.mark.spike
def test_retention_vocabulary_covers_generic_requirements_before_world_ref() -> None:
    generic_policies = (
        RetentionPolicyRequirement(kind="selected-head-pin", target="4" * 40),
        RetentionPolicyRequirement(kind="candidate-ref", target="candidate:workspace"),
        RetentionPolicyRequirement(kind="archive-ref", target="candidate:workspace"),
        RetentionPolicyRequirement(kind="evidence-ref", target="evidence-digest", digest=_sha("evidence")),
        RetentionPolicyRequirement(kind="materialization-receipt", target="target:checkout-main"),
    )
    generic_retained_refs = (
        RetainedRef(kind="selected-head-pin", ref="refs/vcscore/pins/world/store_world/published/workspace"),
        RetainedRef(kind="candidate-ref", ref="refs/vcscore/candidates/op/workspace"),
        RetainedRef(kind="archive-ref", ref="refs/vcscore/archives/operations/op/workspace"),
        RetainedRef(kind="evidence-ref", ref="refs/vcscore/evidence/op/1", digest=_sha("evidence")),
        RetainedRef(kind="materialization-receipt", ref="refs/vcscore/materialization/receipts/r1"),
    )
    world_ref_policies = (
        *generic_policies,
        RetentionPolicyRequirement(
            kind="child-world-retention",
            target="world:" + "c" * 40,
            digest=_sha("child snapshot"),
        ),
    )

    generic_policy_kinds = {requirement.kind for requirement in generic_policies}
    generic_ref_kinds = {retained_ref.kind for retained_ref in generic_retained_refs}
    assert {
        "selected-head-pin",
        "candidate-ref",
        "archive-ref",
        "evidence-ref",
        "materialization-receipt",
    } <= generic_policy_kinds
    assert generic_policy_kinds == generic_ref_kinds
    assert "child-world-retention" not in generic_policy_kinds
    assert world_ref_policies[-1].kind == "child-world-retention"


@pytest.mark.spike
def test_world_ref_selection_uses_generic_retention_vocabulary() -> None:
    child_world_oid = "c" * 40
    child_snapshot_digest = _sha("child world snapshot")
    selection = HeadSelectionRecord(
        binding="child_call",
        store_id="store_child_ref",
        resource_id="world-ref:child-task",
        selected_head="f" * 40,
        selection_kind="new-candidate",
        retention_policy_requirements=(
            RetentionPolicyRequirement(kind="selected-head-pin", target="f" * 40),
            RetentionPolicyRequirement(
                kind="child-world-retention", target=f"world:{child_world_oid}", digest=child_snapshot_digest
            ),
        ),
        selection_policy_digest=_sha("select child world by value"),
    )
    evidence = HeadSelectionEvidence(
        operation_id="op-parent",
        binding=selection.binding,
        store_id=selection.store_id,
        resource_id=selection.resource_id,
        selected_head=selection.selected_head,
        selection_digest=selection.selection_digest(),
        revision_preparation_digest=_sha("world-ref revision preparation"),
        candidate_commit_digest=_sha("world-ref candidate commit"),
        candidate_ref="refs/vcscore/candidates/op-parent/child_call",
        retention_policy_requirements=selection.retention_policy_requirements,
    )
    retention_receipt = SelectionRetentionReceipt(
        operation_id="op-parent",
        world_oid="p" * 40,
        binding=selection.binding,
        store_id=selection.store_id,
        resource_id=selection.resource_id,
        selected_head=selection.selected_head,
        selection_digest=selection.selection_digest(),
        retained_refs=(
            RetainedRef(kind="selected-head-pin", ref="refs/vcscore/pins/world/store_world/p/child_call"),
            RetainedRef(kind="evidence-ref", ref="refs/vcscore/evidence/op-parent/child-call"),
            RetainedRef(
                kind="child-world-retention",
                ref="refs/vcscore/retention/child-worlds/p/child_call",
                digest=child_snapshot_digest,
            ),
        ),
        authority_ref="refs/vcscore/ground",
    )

    retention_kinds = {requirement.kind for requirement in selection.retention_policy_requirements}
    assert retention_kinds == {"selected-head-pin", "child-world-retention"}
    retained_kinds = {retained_ref.kind for retained_ref in retention_receipt.retained_refs}
    assert retained_kinds == {"selected-head-pin", "evidence-ref", "child-world-retention"}
    retry_selection = HeadSelectionRecord(
        binding="child_call",
        store_id="store_child_ref",
        resource_id="world-ref:child-task",
        selected_head="f" * 40,
        selection_kind="new-candidate",
        retention_policy_requirements=selection.retention_policy_requirements,
        selection_policy_digest=selection.selection_policy_digest,
    )
    retry_evidence = HeadSelectionEvidence(
        operation_id="op-parent-retry",
        binding=retry_selection.binding,
        store_id=retry_selection.store_id,
        resource_id=retry_selection.resource_id,
        selected_head=retry_selection.selected_head,
        selection_digest=retry_selection.selection_digest(),
        revision_preparation_digest=_sha("world-ref retry revision preparation"),
        candidate_commit_digest=_sha("world-ref retry candidate commit"),
        candidate_ref="refs/vcscore/candidates/op-parent-retry/child_call",
        retention_policy_requirements=evidence.retention_policy_requirements,
    )
    retry_retention_receipt = SelectionRetentionReceipt(
        operation_id="op-parent-retry",
        world_oid="q" * 40,
        binding=retry_selection.binding,
        store_id=retry_selection.store_id,
        resource_id=retry_selection.resource_id,
        selected_head=retry_selection.selected_head,
        selection_digest=retry_selection.selection_digest(),
        retained_refs=(
            RetainedRef(kind="selected-head-pin", ref="refs/vcscore/pins/world/store_world/q/child_call"),
            RetainedRef(kind="evidence-ref", ref="refs/vcscore/evidence/op-parent/child-call"),
            RetainedRef(
                kind="child-world-retention",
                ref="refs/vcscore/retention/child-worlds/q/child_call",
                digest=child_snapshot_digest,
            ),
        ),
        authority_ref="refs/vcscore/ground",
    )

    assert selection.selection_digest() == retry_selection.selection_digest()
    assert evidence.selection_evidence_digest() != retry_evidence.selection_evidence_digest()
    assert (
        retention_receipt.selection_retention_receipt_digest()
        != retry_retention_receipt.selection_retention_receipt_digest()
    )
