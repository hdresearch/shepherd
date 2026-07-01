"""Shared app blocker DTOs and stable ordering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AppBlockerKind = Literal[
    "live_scope",
    "orphaned_scope",
    "scope_registry_mismatch",
    "orphaned_operation",
    "operation_journal",
    "interrupted_lifecycle",
    "dirty_push",
    "sibling_group",
    "workspace_authority",
    "materialization_recovery",
    "materialization_preflight",
    "physical_workspace",
    "unsupported_feature",
    "session_running",
    "isolated_scope_requires_session",
    "invalid_input",
]

_BLOCKER_ORDER: dict[AppBlockerKind, int] = {
    "dirty_push": 0,
    "interrupted_lifecycle": 1,
    "sibling_group": 2,
    "workspace_authority": 3,
    "physical_workspace": 4,
    "materialization_preflight": 5,
    "scope_registry_mismatch": 6,
    "orphaned_operation": 7,
    "operation_journal": 8,
    "materialization_recovery": 9,
    "orphaned_scope": 10,
    "live_scope": 11,
    "isolated_scope_requires_session": 12,
    "unsupported_feature": 13,
    "session_running": 14,
    "invalid_input": 15,
}


@dataclass(frozen=True)
class AppBlocker:
    """Structured reason an app command is blocked or degraded."""

    kind: AppBlockerKind
    subject: str
    detail: str
    hint: str | None = None
    source_item_id: str | None = None
    source_issue_id: str | None = None
    source_issue_code: str | None = None


def sort_app_blockers(blockers: tuple[AppBlocker, ...] | list[AppBlocker]) -> tuple[AppBlocker, ...]:
    return tuple(sorted(blockers, key=lambda blocker: (_BLOCKER_ORDER[blocker.kind], blocker.subject, blocker.detail)))


def dedupe_app_blockers(blockers: tuple[AppBlocker, ...]) -> tuple[AppBlocker, ...]:
    seen: set[tuple[AppBlockerKind, str, str]] = set()
    deduped: list[AppBlocker] = []
    for blocker in blockers:
        key = (blocker.kind, blocker.subject, blocker.detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    return tuple(deduped)


__all__ = ["AppBlocker", "AppBlockerKind", "dedupe_app_blockers", "sort_app_blockers"]
