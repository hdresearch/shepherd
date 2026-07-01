"""Pure projections from query inventory facts to app blockers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard, assert_never, get_args

from vcs_core._app_blockers import AppBlocker
from vcs_core._query_inventory import RecoveryKind

if TYPE_CHECKING:
    from vcs_core._query_inventory import InventoryIssue, InventoryItem
    from vcs_core._query_readiness import ReadinessResult

# Single source for the recovery-domain item kinds; _recovery_blocker matches it
# exhaustively so a new kind cannot silently become a non-blocker.
_RECOVERY_KINDS: frozenset[str] = frozenset(get_args(RecoveryKind))


def _is_recovery_kind(kind: str) -> TypeGuard[RecoveryKind]:
    return kind in _RECOVERY_KINDS


def app_blocker_from_inventory_item(
    item: InventoryItem,
    issue: InventoryIssue | None = None,
) -> AppBlocker | None:
    """Map one durable inventory fact into an app blocker, when it is app-visible."""
    current_issue = issue or _primary_issue(item)
    if item.domain == "workspace_authority":
        return _workspace_authority_blocker(item, current_issue)
    if item.domain == "operation_journal":
        return _operation_journal_blocker(item, current_issue)
    if item.domain == "recovery":
        return _recovery_blocker(item, current_issue)
    return None


def app_blockers_from_inventory_items(items: tuple[InventoryItem, ...]) -> tuple[AppBlocker, ...]:
    """Project app-visible blockers from inventory items without app context."""
    blockers: list[AppBlocker] = []
    for item in items:
        blocker = app_blocker_from_inventory_item(item)
        if blocker is not None:
            blockers.append(blocker)
    return tuple(blockers)


def app_blockers_from_readiness_result(result: ReadinessResult) -> tuple[AppBlocker, ...]:
    """Project app-visible blockers from readiness blockers with policy provenance."""
    items_by_id = {item.id: item for item in result.snapshot.items}
    issues_by_id = {issue.id: issue for issue in result.snapshot.issues}
    blockers: list[AppBlocker] = []
    for readiness_blocker in result.blockers:
        item = items_by_id.get(readiness_blocker.item_id)
        if item is None:
            continue
        issue = issues_by_id.get(readiness_blocker.issue_id) if readiness_blocker.issue_id is not None else None
        blocker = app_blocker_from_inventory_item(item, issue)
        if blocker is not None:
            blockers.append(blocker)
    return tuple(blockers)


def _workspace_authority_blocker(item: InventoryItem, issue: InventoryIssue | None) -> AppBlocker | None:
    if item.health.presence != "present":
        return None
    subject = _workspace_authority_subject(item)
    return _blocker(
        item,
        issue,
        kind="workspace_authority",
        subject=subject,
        detail=f"Workspace authority operation {subject!r} requires recovery before mutation.",
        hint="Run `vcs-core recover-workspace-authority` before mutating or materializing.",
    )


def _operation_journal_blocker(item: InventoryItem, issue: InventoryIssue | None) -> AppBlocker | None:
    journal_status = item.fields.get("status")
    if (
        item.health.validity != "invalid"
        and item.health.lifecycle != "active"
        and journal_status not in {"failed", "recovery_required"}
    ):
        return None
    subject = _field_str(item, "operation_id") or _field_str(item, "payload_operation_id") or item.locator or item.id
    return _blocker(
        item,
        issue,
        kind="operation_journal",
        subject=subject,
        detail=f"Operation journal {subject!r} requires recovery before mutation.",
        hint="Run vcs-core recovery for the pending world-vector operation before mutating.",
    )


def _recovery_blocker(item: InventoryItem, issue: InventoryIssue | None) -> AppBlocker | None:
    if item.health.validity != "invalid" and item.health.lifecycle != "recoverable":
        return None
    kind = item.kind
    if not _is_recovery_kind(kind):
        # Unknown recovery kinds are not app-visible blockers (defensive; preserves
        # prior fall-through). Known kinds below are exhaustive under assert_never.
        return None
    match kind:
        case "orphaned_scope_ref":
            ref = _field_str(item, "scope_ref") or item.locator or item.id
            subject = _field_str(item, "scope_name") or ref.rsplit("/", 1)[-1]
            return _blocker(
                item,
                issue,
                kind="orphaned_scope",
                subject=subject,
                detail=f"Orphaned scope ref {ref!r} remains from a prior incomplete session.",
                hint="Run `vcs-core archive-orphaned-scopes` after confirming it is not a live scope.",
            )
        case "orphaned_operation_ref":
            subject = _field_str(item, "operation_id") or _field_str(item, "operation_label") or item.locator or item.id
            return _blocker(
                item,
                issue,
                kind="orphaned_operation",
                subject=subject,
                detail=f"Orphaned operation {subject!r} remains open from a prior session.",
                hint="Run `vcs-core archive-orphaned-operations` before mutating or materializing.",
            )
        case "scope_registry_mismatch":
            subject = _field_str(item, "scope_name") or _field_str(item, "ref") or item.locator or item.id
            detail = _field_str(item, "detail") or (issue.message if issue is not None else None) or item.id
            return _blocker(item, issue, kind="scope_registry_mismatch", subject=subject, detail=detail)
        case "sibling_group_blocker":
            subject = _field_str(item, "label") or _field_str(item, "group_id") or item.id
            return _blocker(
                item,
                issue,
                kind="sibling_group",
                subject=subject,
                detail=f"Sibling group {subject} requires recovery before mutation.",
                hint="Resume, cancel, archive, or complete the sibling group first.",
            )
        case "dirty_push":
            subject = _field_str(item, "session_id") or item.locator or item.id
            return _blocker(
                item,
                issue,
                kind="dirty_push",
                subject=subject,
                detail=f"Dirty push state from session {subject!r} requires recovery before mutation.",
                hint="Run `vcs-core recover-materialization --mode repair` before mutating or materializing.",
            )
        case "materialization_run":
            subject = _field_str(item, "run_id") or item.locator or item.id
            return _blocker(
                item,
                issue,
                kind="materialization_recovery",
                subject=subject,
                detail=f"Materialization run {subject!r} requires verification or cleanup before mutation.",
                hint="Run `vcs-core recover-materialization --mode verify`, `repair`, or `force`.",
            )
    assert_never(kind)


def _blocker(
    item: InventoryItem,
    issue: InventoryIssue | None,
    *,
    kind: str,
    subject: str,
    detail: str,
    hint: str | None = None,
) -> AppBlocker:
    return AppBlocker(
        kind=kind,  # type: ignore[arg-type]
        subject=subject,
        detail=detail,
        hint=hint,
        source_item_id=item.id,
        source_issue_id=None if issue is None else issue.id,
        source_issue_code=None if issue is None else issue.code,
    )


def _primary_issue(item: InventoryItem) -> InventoryIssue | None:
    return item.issues[0] if item.issues else None


def _workspace_authority_subject(item: InventoryItem) -> str:
    operation_id = _field_str(item, "operation_id")
    if operation_id is not None:
        return operation_id
    if item.locator is None:
        return item.id
    filename = Path(item.locator).name
    if item.health.status and item.health.status != "unknown":
        return f"{filename} ({item.health.status})"
    return filename


def _field_str(item: InventoryItem, key: str) -> str | None:
    value = item.fields.get(key)
    return value if isinstance(value, str) and value else None
