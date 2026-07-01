"""Private typed inventory DTOs for vcs-core control-plane inspection."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, assert_never

HealthPresence = Literal["absent", "present", "unknown"]
HealthValidity = Literal["valid", "invalid", "unknown"]
HealthIssue = Literal[
    "none",
    "missing",
    "unreadable",
    "corrupt",
    "schema_mismatch",
    "identity_mismatch",
    "unsupported_schema",
    "dangling_dependency",
    "collision",
    "unknown",
]
HealthLifecycle = Literal[
    "active",
    "terminal",
    "recoverable",
    "stale",
    "projection_stale",
    "projection_missing",
    "unknown",
]
HealthAuthorityRole = Literal["authoritative", "advisory", "projection", "none", "unknown"]
IssueSeverity = Literal["info", "warning", "error"]
# Tier 2 of the control-plane taxonomy (260622-control-plane-taxonomy.md): what to DO about one
# fact, orthogonal to its surface tier. `blocking` gates the relevant mutation now; `recoverable`
# is actionable + targetable through normal recovery selection; `diagnostic` is a visible error
# that is neither a blocker nor auto-recoverable. Declared on the facts this tranche owns; the rest
# stay driven by the legacy `_derive_blockers` rules until a follow-on migrates them.
Disposition = Literal["blocking", "recoverable", "diagnostic"]
SnapshotConsistency = Literal["best_effort", "locked"]

OPERATION_JOURNAL_MISSING_REF = "operation_journal_missing_ref"
OPERATION_JOURNAL_REF_UNREADABLE = "operation_journal_ref_unreadable"
OPERATION_JOURNAL_PAYLOAD_CORRUPT = "operation_journal_payload_corrupt"
OPERATION_JOURNAL_SCHEMA_MISMATCH = "operation_journal_schema_mismatch"
OPERATION_JOURNAL_IDENTITY_MISMATCH = "operation_journal_identity_mismatch"
OPERATION_JOURNAL_CHAIN_INVALID = "operation_journal_chain_invalid"
OPERATION_JOURNAL_UNSUPPORTED_FAMILY = "operation_journal_unsupported_family"

QUERY_DOMAIN_UNREADABLE = "query_domain_unreadable"

WORKSPACE_AUTHORITY_MISSING_FILE = "workspace_authority_missing_file"
WORKSPACE_AUTHORITY_FILE_UNREADABLE = "workspace_authority_file_unreadable"
WORKSPACE_AUTHORITY_PAYLOAD_CORRUPT = "workspace_authority_payload_corrupt"
WORKSPACE_AUTHORITY_SCHEMA_MISMATCH = "workspace_authority_schema_mismatch"
WORKSPACE_AUTHORITY_IDENTITY_MISMATCH = "workspace_authority_identity_mismatch"
WORKSPACE_AUTHORITY_LOCATOR_COLLISION = "workspace_authority_locator_collision"
WORKSPACE_AUTHORITY_LEGACY_LOCATOR = "workspace_authority_legacy_locator"

SCOPE_MISSING_REF = "scope_missing_ref"
SCOPE_REF_UNREADABLE = "scope_ref_unreadable"

AUTHORITY_REF_MISSING = "authority_ref_missing"
AUTHORITY_REF_UNREADABLE = "authority_ref_unreadable"
AUTHORITY_REF_TARGET_MISSING_WORLD = "authority_ref_target_missing_world"
AUTHORITY_REF_IDENTITY_MISMATCH = "authority_ref_identity_mismatch"

WORLD_MISSING = "world_missing"
WORLD_UNREADABLE = "world_unreadable"
WORLD_SCHEMA_MISMATCH = "world_schema_mismatch"
WORLD_BINDING_MISSING = "world_binding_missing"
WORLD_BINDING_INVALID = "world_binding_invalid"
WORLD_SELECTED_HEAD_DANGLING = "world_selected_head_dangling"

RECOVERY_ORPHANED_SCOPE_REF = "recovery_orphaned_scope_ref"
RECOVERY_ORPHANED_OPERATION_REF = "recovery_orphaned_operation_ref"
RECOVERY_SCOPE_REGISTRY_MISMATCH = "recovery_scope_registry_mismatch"
RECOVERY_SIBLING_GROUP_BLOCKER = "recovery_sibling_group_blocker"
RECOVERY_MATERIALIZATION_RUN = "recovery_materialization_run"
RECOVERY_MATERIALIZATION_RUN_CORRUPT = "recovery_materialization_run_corrupt"
RECOVERY_DIRTY_PUSH = "recovery_dirty_push"
RECOVERY_DIRTY_PUSH_CORRUPT = "recovery_dirty_push_corrupt"
# Active-lease accelerator health: corrupt is surfaced cheaply (index-only) on the recovery
# inventory; the stale-vs-authority verdict is a deep-fsck issue code (literal, see fsck_world).
ACTIVE_LEASE_INDEX_CORRUPT = "active_lease_index_corrupt"
# Open-operation-journal accelerator health: unlike the lease index (superset, GC-protection), this
# index is EXACT and gates mutation, so a corrupt index is a fail-closed *blocking* admission fact
# (recoverable -> in _RECOVERABLE_ISSUES, so targeted `vcscore.recover` can rebuild it).
OPEN_OPERATION_JOURNAL_INDEX_CORRUPT = "open_operation_journal_index_corrupt"

# Canonical recovery-domain item kinds — the inventory vocabulary that the AppBlocker
# projection and the readiness/exception paths classify on. Single source of truth:
# _ALL_RECOVERY_KINDS derives from it (get_args), and _recovery_blocker matches it
# exhaustively (assert_never), so a new recovery kind cannot silently fall through.
RecoveryKind = Literal[
    "orphaned_scope_ref",
    "orphaned_operation_ref",
    "scope_registry_mismatch",
    "sibling_group_blocker",
    "dirty_push",
    "materialization_run",
]


@dataclass(frozen=True)
class PresentValid:
    """A present, valid durable item. presence/validity/primary_issue are fixed."""

    lifecycle: HealthLifecycle = "active"
    authority_role: HealthAuthorityRole = "none"

    @property
    def presence(self) -> HealthPresence:
        return "present"

    @property
    def validity(self) -> HealthValidity:
        return "valid"

    @property
    def primary_issue(self) -> HealthIssue:
        return "none"

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return ()

    @property
    def status(self) -> str:
        return "present_terminal" if self.lifecycle == "terminal" else "present_valid"


@dataclass(frozen=True)
class PresentInvalid:
    """A present item with a structural/data problem (validity=invalid)."""

    primary_issue: HealthIssue
    issue_codes: tuple[str, ...]
    lifecycle: HealthLifecycle = "unknown"
    authority_role: HealthAuthorityRole = "none"
    status_override: str | None = None

    @property
    def presence(self) -> HealthPresence:
        return "present"

    @property
    def validity(self) -> HealthValidity:
        return "invalid"

    @property
    def status(self) -> str:
        return self.status_override or _invalid_status(self.primary_issue)


@dataclass(frozen=True)
class Expected:
    """A benign absence.

    Not present because it is not (yet) expected to be — ground/authority
    pre-first-publish, or a queried thing that does not exist.
    ``primary_issue`` is ``none``; this is not a fault (severity ``info``).
    """

    issue_codes: tuple[str, ...] = ()
    lifecycle: HealthLifecycle = "unknown"
    authority_role: HealthAuthorityRole = "none"

    @property
    def presence(self) -> HealthPresence:
        return "absent"

    @property
    def validity(self) -> HealthValidity:
        return "unknown"

    @property
    def primary_issue(self) -> HealthIssue:
        return "none"

    @property
    def status(self) -> str:
        return "absent"


@dataclass(frozen=True)
class Missing:
    """A *problematic* absence: a thing that should exist but does not.

    ``primary_issue`` is ``missing``. ``lifecycle="recoverable"`` distinguishes a
    recoverable-pending absence (severity ``warning``) from a hard fault
    (severity ``error``) — see ``severity_for``.
    """

    issue_codes: tuple[str, ...] = ()
    lifecycle: HealthLifecycle = "unknown"
    authority_role: HealthAuthorityRole = "none"

    @property
    def presence(self) -> HealthPresence:
        return "absent"

    @property
    def validity(self) -> HealthValidity:
        return "unknown"

    @property
    def primary_issue(self) -> HealthIssue:
        return "missing"

    @property
    def status(self) -> str:
        return "missing"


# Health is a discriminated union (a sum type): every reachable state is a named
# variant, so invalid field combinations are unrepresentable and consumers can
# match exhaustively (assert_never). The seven flat fields are exposed as derived
# properties on each variant, so existing readers, the selector DSL, and
# serialization keep working unchanged (derived => cannot drift).
Health = PresentValid | PresentInvalid | Expected | Missing


def present_valid(
    *,
    lifecycle: HealthLifecycle = "active",
    authority_role: HealthAuthorityRole = "none",
) -> Health:
    return PresentValid(lifecycle=lifecycle, authority_role=authority_role)


def present_invalid(
    *,
    primary_issue: HealthIssue,
    issue_codes: tuple[str, ...],
    lifecycle: HealthLifecycle = "unknown",
    authority_role: HealthAuthorityRole = "none",
    status: str | None = None,
) -> Health:
    return PresentInvalid(
        primary_issue=primary_issue,
        issue_codes=issue_codes,
        lifecycle=lifecycle,
        authority_role=authority_role,
        status_override=status,
    )


def expected(
    *,
    issue_codes: tuple[str, ...] = (),
    lifecycle: HealthLifecycle = "unknown",
    authority_role: HealthAuthorityRole = "none",
) -> Health:
    """Benign absence (not a fault)."""
    return Expected(issue_codes=issue_codes, lifecycle=lifecycle, authority_role=authority_role)


def missing(
    *,
    issue_codes: tuple[str, ...] = (),
    lifecycle: HealthLifecycle = "unknown",
    authority_role: HealthAuthorityRole = "none",
) -> Health:
    """Problematic absence: a thing that should exist but does not."""
    return Missing(issue_codes=issue_codes, lifecycle=lifecycle, authority_role=authority_role)


def health_to_json(health: Health) -> dict[str, object]:
    return {
        "presence": health.presence,
        "validity": health.validity,
        "primary_issue": health.primary_issue,
        "issue_codes": list(health.issue_codes),
        "lifecycle": health.lifecycle,
        "authority_role": health.authority_role,
        "status": health.status,
    }


def severity_for(health: Health) -> IssueSeverity:
    """Single producer of issue severity, derived from the (honest) Health verdict.

    Ratified Decision 2 in DESIGN-severity-health-derivation.md. Keyed on the
    variant + lifecycle (so a recoverable Missing is a warning, not an error).
    Severity is no longer hand-set on InventoryIssue; deriving it here at the item
    boundary makes a severity inconsistent with Health unrepresentable.
    """
    match health:
        case PresentValid():
            return "info"
        case Expected():
            return "info"
        case Missing(lifecycle="recoverable"):
            return "warning"
        case Missing():
            return "error"
        case PresentInvalid():
            return _severity_for_primary_issue(health.primary_issue)
    assert_never(health)


def _severity_for_primary_issue(issue: HealthIssue) -> IssueSeverity:
    match issue:
        case "none":
            return "info"
        case "unknown":
            return "warning"
        case (
            "missing"
            | "unreadable"
            | "corrupt"
            | "schema_mismatch"
            | "identity_mismatch"
            | "unsupported_schema"
            | "dangling_dependency"
            | "collision"
        ):
            return "error"
    assert_never(issue)


@dataclass(frozen=True)
class InventoryIssue:
    """Factual diagnostic attached to an inventory item."""

    id: str
    code: str
    message: str
    subject_id: str
    locator: str | None = None
    recovery_hint: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "code": self.code,
            "message": self.message,
            "subject_id": self.subject_id,
        }
        if self.locator is not None:
            payload["locator"] = self.locator
        if self.recovery_hint is not None:
            payload["recovery_hint"] = self.recovery_hint
        if self.evidence:
            payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(frozen=True)
class InventoryItem:
    """One observed durable or derived vcs-core control-plane fact."""

    id: str
    domain: str
    kind: str
    locator: str | None
    source_kind: str
    source_store: str | None
    health: Health
    role: tuple[str, ...] = ()
    fields: dict[str, object] = field(default_factory=dict)
    source_identity: dict[str, object] = field(default_factory=dict)
    issues: tuple[InventoryIssue, ...] = ()
    disposition: Disposition | None = None

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "domain": self.domain,
            "kind": self.kind,
            "locator": self.locator,
            "source_kind": self.source_kind,
            "source_store": self.source_store,
            "health": health_to_json(self.health),
            "role": list(self.role),
            "fields": dict(self.fields),
            "source_identity": dict(self.source_identity),
            "issues": [{**issue.to_json(), "severity": severity_for(self.health)} for issue in self.issues],
        }
        # Emit the declared disposition only when present, so un-migrated facts keep their current
        # serialization (minimal public-surface ripple); migrated facts gain the typed field.
        if self.disposition is not None:
            payload["disposition"] = self.disposition
        return payload


@dataclass(frozen=True)
class InventoryEdge:
    """Typed relationship between two inventory items."""

    source_id: str
    target_id: str
    kind: str
    fields: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "fields": dict(self.fields),
        }


@dataclass(frozen=True)
class InventorySnapshot:
    """Immutable observation of selected vcs-core control-plane domains."""

    id: str
    consistency: SnapshotConsistency
    items: tuple[InventoryItem, ...]
    edges: tuple[InventoryEdge, ...] = ()
    issues: tuple[InventoryIssue, ...] = ()
    source_identity: dict[str, object] = field(default_factory=dict)
    created_at_unix_ns: int = 0

    @classmethod
    def create(
        cls,
        *,
        items: tuple[InventoryItem, ...],
        edges: tuple[InventoryEdge, ...] = (),
        issues: tuple[InventoryIssue, ...] = (),
        consistency: SnapshotConsistency = "best_effort",
        source_identity: dict[str, object] | None = None,
    ) -> InventorySnapshot:
        created_at = time.time_ns()
        return cls(
            id=f"snapshot:{created_at}",
            consistency=consistency,
            items=items,
            edges=edges,
            issues=issues,
            source_identity=dict(source_identity or {}),
            created_at_unix_ns=created_at,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "created_at_unix_ns": self.created_at_unix_ns,
            "consistency": self.consistency,
            "source_identity": dict(self.source_identity),
            "items": [item.to_json() for item in self.items],
            "edges": [edge.to_json() for edge in self.edges],
            # Snapshot-level issues are domain-read failures (no item Health to
            # derive from); a domain that cannot be read is intrinsically an error.
            "issues": [{**issue.to_json(), "severity": "error"} for issue in self.issues],
        }


def issue_id(subject_id: str, code: str) -> str:
    return f"issue:{subject_id}:{code}"


def _invalid_status(primary_issue: HealthIssue) -> str:
    if primary_issue == "schema_mismatch":
        return "schema_mismatch"
    if primary_issue == "identity_mismatch":
        return "identity_mismatch"
    if primary_issue == "unsupported_schema":
        return "unsupported_schema"
    if primary_issue == "dangling_dependency":
        return "dangling_dependency"
    if primary_issue == "collision":
        return "locator_collision"
    return f"present_{primary_issue}"
