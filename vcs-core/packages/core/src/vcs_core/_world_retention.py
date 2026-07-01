"""Private coordinator-owned retention vocabulary for v2 worlds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._errors import InvalidRepositoryStateError

if TYPE_CHECKING:
    from vcs_core._transition_kernel_records import RetainedRef, RetentionPolicyRequirement

SELECTED_HEAD_PIN = "selected-head-pin"
CANDIDATE_REF = "candidate-ref"
ARCHIVE_REF = "archive-ref"
EVIDENCE_REF = "evidence-ref"
CHILD_WORLD_RETENTION = "child-world-retention"
MATERIALIZATION_RECEIPT = "materialization-receipt"

RETENTION_POLICY_KINDS = frozenset(
    {
        SELECTED_HEAD_PIN,
        CANDIDATE_REF,
        ARCHIVE_REF,
        EVIDENCE_REF,
        CHILD_WORLD_RETENTION,
        MATERIALIZATION_RECEIPT,
    }
)

RETAINED_REF_KINDS = RETENTION_POLICY_KINDS


def validate_retention_policy_kind(requirement: RetentionPolicyRequirement) -> None:
    if requirement.kind not in RETENTION_POLICY_KINDS:
        raise InvalidRepositoryStateError(f"unsupported retention policy kind: {requirement.kind!r}")


def validate_retained_ref(ref: RetainedRef) -> None:
    if ref.kind not in RETAINED_REF_KINDS:
        raise InvalidRepositoryStateError(f"unsupported retained ref kind: {ref.kind!r}")
    if not ref.ref:
        raise InvalidRepositoryStateError("retained ref must name a ref")
