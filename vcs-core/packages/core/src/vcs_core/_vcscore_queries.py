from __future__ import annotations

from typing import TYPE_CHECKING, cast

from vcs_core._errors import StaleScopeError
from vcs_core._recovery_inventory import (
    recovery_inventory_snapshot,
    recovery_orphaned_operation_items,
    recovery_orphaned_scope_refs,
)
from vcs_core._workspace_authority_inventory import probe_workspace_authority_pending, workspace_authority_pending_label
from vcs_core.types import (
    CommitInfo,
    DiffSummary,
    OperationHistory,
    OperationSummary,
    OperationVisibility,
    RecoverySnapshot,
    ScopeInfo,
    Status,
)

if TYPE_CHECKING:
    from vcs_core._query_inventory import InventoryItem
    from vcs_core.vcscore import VcsCore


def status(owner: VcsCore) -> Status:
    return owner._store.status()


def diff(owner: VcsCore) -> DiffSummary:
    return owner._store.diff()


def log(owner: VcsCore, *, ref: str | None = None, max_count: int = 50) -> list[CommitInfo]:
    return owner._store.log(ref=ref, max_count=max_count)


def filter_effects(
    owner: VcsCore,
    *,
    effect_type: str | None = None,
    substrate: str | None = None,
    ref: str | None = None,
    max_count: int = 100,
    scope: str | None = None,
) -> list[CommitInfo]:
    return owner._store.filter_effects(
        effect_type=effect_type,
        substrate=substrate,
        ref=ref,
        max_count=max_count,
        scope=scope,
    )


def visible_operations(owner: VcsCore, *, ref: str | None = None, max_count: int = 50) -> list[OperationSummary]:
    return owner._store.visible_operations(ref=ref, max_count=max_count)


def open_operations(
    owner: VcsCore,
    *,
    scope: ScopeInfo | None = None,
    session_id: str | None = None,
) -> list[OperationSummary]:
    scope_ref = scope.ref if scope is not None else None
    return owner._store.open_operations(scope_ref=scope_ref, session_id=session_id)


def archived_operations(
    owner: VcsCore,
    *,
    max_count: int = 50,
    world_id: str | None = None,
    operation_id: str | None = None,
) -> list[OperationSummary]:
    return owner._store.archived_operations(
        max_count=max_count,
        world_id=world_id,
        operation_id=operation_id,
    )


def operation_history(owner: VcsCore, ref: str) -> OperationHistory:
    return owner._store.read_operation_history(ref)


def recovery_snapshot(owner: VcsCore, *, archived_max_count: int = 50) -> RecoverySnapshot:
    inventory = recovery_inventory_snapshot(owner)
    return RecoverySnapshot(
        orphaned_scope_refs=recovery_orphaned_scope_refs(inventory),
        open_operations=tuple(open_operations(owner)),
        archived_recovery_operations=tuple(owner._store.archived_recovery_operations(max_count=archived_max_count)),
        orphaned_operations=_hydrate_orphaned_operation_summaries(
            owner,
            recovery_orphaned_operation_items(inventory),
        ),
        workspace_authority_pending=tuple(
            workspace_authority_pending_label(item) for item in probe_workspace_authority_pending(owner._repo_path)
        ),
    )


def orphaned_operations(owner: VcsCore) -> tuple[OperationSummary, ...]:
    """Return orphaned operations selected by recovery inventory."""
    inventory = recovery_inventory_snapshot(owner)
    return _hydrate_orphaned_operation_summaries(owner, recovery_orphaned_operation_items(inventory))


def _hydrate_orphaned_operation_summaries(
    owner: VcsCore,
    items: tuple[InventoryItem, ...],
) -> tuple[OperationSummary, ...]:
    return tuple(_hydrate_orphaned_operation_summary(owner, item) for item in items)


def _hydrate_orphaned_operation_summary(owner: VcsCore, item: InventoryItem) -> OperationSummary:
    if item.locator is not None and owner._store.ref_exists(item.locator):
        return owner._store.read_operation_history(item.locator).summary
    operation_id = _field_str(item, "operation_id") or _field_str(item, "operation_label") or item.id
    scope_ref = _field_str(item, "scope_ref") or "refs/vcscore/ground"
    return OperationSummary(
        operation_id=operation_id,
        label=_field_str(item, "operation_label"),
        kind=_field_str(item, "operation_kind") or "unknown",
        status=_field_str(item, "status") or "open",
        visibility=cast("OperationVisibility", _field_str(item, "visibility") or "staged"),
        world_id=_field_str(item, "world_id") or _orphaned_operation_world_id(owner, scope_ref),
        world_name=_field_str(item, "world_name") or owner._scope_name_for_ref(scope_ref),
        world_ref=scope_ref,
        carrier_ref=item.locator or item.id,
        parent_operation_id=_field_str(item, "parent_operation_id"),
    )


def _orphaned_operation_world_id(owner: VcsCore, scope_ref: str) -> str:
    if owner._ground is not None and scope_ref == owner._ground.ref:
        return owner._scope_world_id(owner._ground)
    for scope in owner._active_scopes.values():
        if scope.ref == scope_ref:
            return owner._scope_world_id(scope)
    return "unknown"


def _field_str(item: InventoryItem, key: str) -> str | None:
    value = item.fields.get(key)
    return value if isinstance(value, str) and value else None


def resolve_operation_history(
    owner: VcsCore,
    selector: str,
    *,
    scope: ScopeInfo | None = None,
    max_count: int = 200,
) -> OperationHistory:
    if selector.startswith(("refs/vcscore/ops/", "refs/vcscore/archive/ops/")):
        return operation_history(owner, selector)

    direct_matches = operation_direct_matches(owner, selector, scope=scope)
    identity_matches = operation_id_matches(owner, selector, scope=scope, max_count=max_count)

    if len(direct_matches) == 1:
        return read_operation_summary_history(owner, direct_matches[0])
    if len(direct_matches) > 1:
        labels = ", ".join(sorted(describe_operation_selector_match(item) for item in direct_matches)[:5])
        msg = f"Ambiguous operation selector {selector!r}. Matches: {labels}"
        raise ValueError(msg)

    if len(identity_matches) == 1:
        return read_operation_summary_history(owner, next(iter(identity_matches.values())))
    if len(identity_matches) > 1:
        labels = ", ".join(sorted(describe_operation_selector_match(item) for item in identity_matches.values())[:5])
        msg = f"Ambiguous operation selector {selector!r}. Matches: {labels}"
        raise ValueError(msg)
    msg = f"No operation matches {selector!r}."
    raise ValueError(msg)


def operation_direct_matches(
    owner: VcsCore,
    selector: str,
    *,
    scope: ScopeInfo | None,
) -> list[OperationSummary]:
    try:
        summaries = owner._store.committed_carrier_operations(selector, max_count=1_000_000)
    except (StaleScopeError, ValueError):
        return []
    if scope is None:
        return summaries
    world_id = owner._scope_world_id(scope)
    return [summary for summary in summaries if summary.world_id == world_id]


def operation_id_matches(
    owner: VcsCore,
    selector: str,
    *,
    scope: ScopeInfo | None,
    max_count: int = 200,
) -> dict[str, OperationSummary]:
    matches: dict[str, OperationSummary] = {}
    visible_refs = [scope.ref] if scope is not None else ["refs/vcscore/ground", *owner._store.list_scope_refs()]
    for ref in visible_refs:
        try:
            summary = owner._store.read_visible_operation_history(ref, operation_id=selector).summary
        except StaleScopeError:
            continue
        matches[summary.carrier_ref] = summary

    operations = open_operations(owner, scope=scope) if scope is not None else open_operations(owner)
    for summary in operations:
        if summary.operation_id == selector:
            matches[summary.carrier_ref] = summary

    world_id = owner._scope_world_id(scope) if scope is not None else None
    for summary in owner._store.archived_operations(
        max_count=max(max_count, 1_000_000),
        world_id=world_id,
        operation_id=selector,
    ):
        matches[summary.carrier_ref] = summary
    return matches


def read_operation_summary_history(owner: VcsCore, summary: OperationSummary) -> OperationHistory:
    if summary.visibility == "visible":
        return owner._store.read_visible_operation_history(
            summary.carrier_ref,
            operation_id=summary.operation_id,
        )
    if summary.archived_via == "discarded_world_ref":
        return owner._store.read_discarded_world_operation_history(
            summary.carrier_ref,
            operation_id=summary.operation_id,
        )
    return operation_history(owner, summary.carrier_ref)


def describe_operation_selector_match(summary: OperationSummary) -> str:
    label = summary.label or summary.operation_id
    carrier_ref = summary.carrier_ref
    return (
        f"{summary.operation_id} ({label}) [{summary.visibility}/{summary.status}] "
        f"world:{summary.world_name} carrier:{carrier_ref.rsplit('/', 1)[-1]}"
    )
