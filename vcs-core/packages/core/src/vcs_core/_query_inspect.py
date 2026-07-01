"""Experimental inspect JSON surface over private inventory probes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from vcs_core._operation_journal_inventory import probe_operation_journals
from vcs_core._query_inventory import (
    QUERY_DOMAIN_UNREADABLE,
    InventoryIssue,
    InventoryItem,
    InventorySnapshot,
    issue_id,
)
from vcs_core._query_selectors import select_inventory_items
from vcs_core._recovery_inventory import recovery_inventory_snapshot_for_store
from vcs_core._scope_world_inventory import (
    probe_authority_ref,
    probe_scope,
    probe_selected_world,
    scope_ref_for_selector,
)
from vcs_core._workspace_authority_inventory import probe_workspace_authority_pending
from vcs_core._world_storage_installation import default_world_storage_exists, open_existing_default_world_storage

if TYPE_CHECKING:
    from vcs_core._world_storage_manager import WorldStorageManager

INSPECT_RESULT_SCHEMA = "vcscore/inspect-result/experimental-v1"
InspectDomain = Literal["operation_journal", "workspace_authority", "scope", "authority_ref", "world", "recovery"]


def inspect_repository(
    repo_path: str | Path,
    *,
    domains: tuple[str, ...] = (),
    selector: str | None = None,
    scope: str = "ground",
) -> dict[str, object]:
    """Return an experimental JSON-safe inventory snapshot for selected domains."""
    path = Path(repo_path)
    selected = _selected_domains(domains)
    items: list[InventoryItem] = []
    domain_issues: list[InventoryIssue] = []
    authority_ref = scope_ref_for_selector(scope)
    if "scope" in selected:
        try:
            items.append(probe_scope(path, scope))
        except Exception as exc:  # noqa: BLE001
            domain_issues.append(_domain_issue("scope", exc))
    if "authority_ref" in selected:
        try:
            items.append(probe_authority_ref(path, authority_ref))
        except Exception as exc:  # noqa: BLE001
            domain_issues.append(_domain_issue("authority_ref", exc))
    if "world" in selected:
        try:
            items.extend(probe_selected_world(path, authority_ref))
        except Exception as exc:  # noqa: BLE001
            domain_issues.append(_domain_issue("world", exc))
    if "operation_journal" in selected:
        try:
            manager = _open_existing_world_storage(path)
            if manager is not None:
                items.extend(probe_operation_journals(manager.world_store.repo))
        except Exception as exc:  # noqa: BLE001
            domain_issues.append(_domain_issue("operation_journal", exc))
    if "workspace_authority" in selected:
        try:
            items.extend(probe_workspace_authority_pending(path))
        except Exception as exc:  # noqa: BLE001
            domain_issues.append(_domain_issue("workspace_authority", exc))
    if "recovery" in selected:
        try:
            from vcs_core.store import Store

            store = Store.open_existing(str(path))
            items.extend(recovery_inventory_snapshot_for_store(path, store).items)
        except Exception as exc:  # noqa: BLE001
            domain_issues.append(_domain_issue("recovery", exc))
    snapshot = InventorySnapshot.create(items=tuple(items), issues=tuple(domain_issues))
    selected_items = select_inventory_items(snapshot, selector) if selector is not None else snapshot.items
    issues = (*domain_issues, *(issue for item in selected_items for issue in item.issues))
    snapshot = InventorySnapshot.create(items=selected_items, issues=issues)
    return {
        "schema": INSPECT_RESULT_SCHEMA,
        "repository": {
            "path": str(path),
        },
        "snapshot": snapshot.to_json(),
        "query": {
            "domains": list(selected),
            "selector": selector,
            "scope": scope,
        },
        "items": [item.to_json() for item in snapshot.items],
        "edges": [edge.to_json() for edge in snapshot.edges],
        "issues": [issue.to_json() for issue in snapshot.issues],
    }


def _selected_domains(domains: tuple[str, ...]) -> tuple[InspectDomain, ...]:
    if not domains or "all" in domains:
        return ("scope", "authority_ref", "world", "operation_journal", "workspace_authority", "recovery")
    selected: list[InspectDomain] = []
    for domain in domains:
        if domain == "operation_journal":
            selected.append("operation_journal")
        elif domain == "workspace_authority":
            selected.append("workspace_authority")
        elif domain == "scope":
            selected.append("scope")
        elif domain == "authority_ref":
            selected.append("authority_ref")
        elif domain == "world":
            selected.append("world")
        elif domain == "recovery":
            selected.append("recovery")
        else:
            raise ValueError(f"unknown inspect domain: {domain}")
    return tuple(selected)


def _open_existing_world_storage(repo_path: Path) -> WorldStorageManager | None:
    if not default_world_storage_exists(repo_path):
        return None
    return open_existing_default_world_storage(repo_path)


def _domain_issue(domain: InspectDomain, exc: Exception) -> InventoryIssue:
    subject_id = f"inspect:{domain}"
    return InventoryIssue(
        id=issue_id(subject_id, QUERY_DOMAIN_UNREADABLE),
        code=QUERY_DOMAIN_UNREADABLE,
        message=f"inspect domain {domain!r} could not be read: {exc}",
        subject_id=subject_id,
        recovery_hint="Inspect narrower domains and repair the unreadable control-plane state before recovery.",
    )
