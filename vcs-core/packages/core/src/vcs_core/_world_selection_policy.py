"""Selection policy helpers for private world-vector coordination."""

from __future__ import annotations

from typing import Literal

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._transition_kernel_records import (
    CandidateBackedSelectionKind,
    EvidenceRef,
    RetentionPolicyRequirement,
)
from vcs_core._world_retention import CHILD_WORLD_RETENTION, SELECTED_HEAD_PIN
from vcs_core._world_types import WORLD_REF_SUBSTRATE_KIND, SubstrateHead, WorldRefPayload, canonical_digest

ExistingHeadSelectionKind = Literal["bootstrap", "checkpoint", "import", "revert"]
# CandidateBackedSelectionKind is imported from _transition_kernel_records (its single
# home); re-exported here so existing `from _world_selection_policy import …` keeps working.


def allowed_existing_head_semantic_ops(selection_kind: ExistingHeadSelectionKind) -> set[str]:
    return {
        "bootstrap": {"bootstrap"},
        "checkpoint": {"checkpoint"},
        "import": {
            "import",
            "bootstrap",
            "workspace-adoption",
            "workspace-capture-reduction",
            "workspace-overlay-merge",
            "workspace-scan",
        },
        "revert": {"revert"},
    }[selection_kind]


def validate_root_selection_policy(*, input_world_oid: str | None, selection_kind: str) -> None:
    if input_world_oid is not None:
        return
    if selection_kind == "unchanged":
        raise InvalidRepositoryStateError(
            "root prepared operation requires explicit bootstrap/import/checkpoint/revert selections"
        )


def validate_unchanged_selection_policy(
    *,
    input_world_oid: str | None,
    evidence_refs: tuple[EvidenceRef, ...],
) -> None:
    if input_world_oid is None:
        raise InvalidRepositoryStateError(
            "root prepared operation requires explicit bootstrap/import/checkpoint/revert selections"
        )
    if evidence_refs:
        raise InvalidRepositoryStateError("unchanged selection must not carry evidence refs")


def validate_unchanged_head_identity(*, input_head: SubstrateHead, selected_head: SubstrateHead) -> None:
    if selected_head != input_head:
        raise InvalidRepositoryStateError("unchanged selection must match input world head identity")


def stable_selection_policy_digest(*, binding: str, head: str) -> str:
    return canonical_digest({"selection": binding, "head": head})


def resolve_candidate_selection_kind(
    *,
    operation_id: str,
    producer_operation_id: str,
    producer_world_oid: str | None,
    requested_kind: CandidateBackedSelectionKind | None,
) -> CandidateBackedSelectionKind:
    selection_kind = requested_kind or (
        "child-produced" if producer_operation_id != operation_id or producer_world_oid is not None else "new-candidate"
    )
    if selection_kind == "new-candidate" and producer_operation_id != operation_id:
        raise InvalidRepositoryStateError("new-candidate selection requires current operation producer")
    if selection_kind == "new-candidate" and producer_world_oid is not None:
        raise InvalidRepositoryStateError("new-candidate selection must not carry producer_world_oid")
    if selection_kind == "child-produced" and producer_world_oid is None:
        raise InvalidRepositoryStateError("child-produced selection requires producer_world_oid")
    return selection_kind


def selection_retention_policy_requirements(
    head: SubstrateHead,
    *,
    explicit_requirements: tuple[RetentionPolicyRequirement, ...] = (),
    world_ref_payload: WorldRefPayload | None = None,
) -> tuple[RetentionPolicyRequirement, ...]:
    mandatory: list[RetentionPolicyRequirement] = [
        RetentionPolicyRequirement(kind=SELECTED_HEAD_PIN, target=head.head),
    ]
    if head.kind == WORLD_REF_SUBSTRATE_KIND:
        if world_ref_payload is None:
            raise InvalidRepositoryStateError("world-ref selection retention requires child world payload")
        mandatory.append(
            RetentionPolicyRequirement(
                kind=CHILD_WORLD_RETENTION,
                target=f"world:{world_ref_payload.world_oid}",
                digest=world_ref_payload.snapshot_digest,
            )
        )
    requirements_by_key = {(requirement.kind, requirement.target): requirement for requirement in mandatory}
    for requirement in explicit_requirements:
        key = (requirement.kind, requirement.target)
        existing = requirements_by_key.get(key)
        if existing is not None and existing.digest != requirement.digest:
            raise InvalidRepositoryStateError("selection retention policy conflicts with mandatory requirement")
        requirements_by_key[key] = requirement
    return tuple(sorted(requirements_by_key.values(), key=lambda requirement: canonical_digest(requirement.to_json())))
