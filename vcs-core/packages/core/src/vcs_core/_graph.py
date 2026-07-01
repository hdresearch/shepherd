"""ASCII graph rendering for vcs-core log.

Renders the commit DAG as a git-log-style ASCII graph with scope-based
columns. Pure formatting function over CommitInfo data — no VcsCore
session state required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcs_core.types import CommitInfo


def render_graph(entries: list[CommitInfo]) -> list[str]:
    """Render a list of CommitInfo entries as ASCII graph lines.

    Returns a list of formatted strings ready for display.

    Entries arrive newest-first (topological). Scopes are assigned to
    columns when first seen. ScopeMerge and DiscardSnapshot structural
    effects trigger column release and merge connector rendering.
    """
    if not entries:
        return []

    # Pre-scan: find the last index (oldest commit) for each scope,
    # and which scopes have structural effects (ScopeMerge/DiscardSnapshot).
    scope_last: dict[str, int] = {}
    scope_merge_target: dict[str, str] = {}  # scope -> merged_into target
    scope_has_discard: set[str] = set()
    for i, entry in enumerate(entries):
        scope = _metadata_str(entry, "scope", default="ground")
        scope_last[scope] = i
        effect_type = _metadata_str(entry, "type")
        if effect_type == "ScopeMerge":
            scope_merge_target[scope] = _metadata_str(entry, "merged_into", default="ground")
        elif effect_type == "DiscardSnapshot":
            scope_has_discard.add(scope)

    # Column assignment state.
    columns: dict[str, int] = {"ground": 0}
    free_columns: list[int] = []
    next_column = 1
    active_columns: set[int] = {0}

    def _assign(scope: str) -> int:
        nonlocal next_column
        if scope in columns:
            return columns[scope]
        if free_columns:
            col = free_columns.pop(0)
        else:
            col = next_column
            next_column += 1
        columns[scope] = col
        active_columns.add(col)
        return col

    def _release(scope: str) -> None:
        if scope in columns and scope != "ground":
            col = columns.pop(scope)
            active_columns.discard(col)
            free_columns.append(col)
            free_columns.sort()

    # Render pass.
    lines: list[str] = []
    max_col = 0

    for i, entry in enumerate(entries):
        scope = _metadata_str(entry, "scope", default="ground")
        col = _assign(scope)
        max_col = max(max_col, col)

        effect_type = _metadata_str(entry, "type", default="?")
        oid_short = entry.oid[:8]

        # Build graph prefix.
        prefix_parts: list[str] = []
        for c in range(max_col + 1):
            if c == col:
                prefix_parts.append("*")
            elif c in active_columns:
                prefix_parts.append("|")
            else:
                prefix_parts.append(" ")
        prefix = " ".join(prefix_parts)

        detail = _format_detail(entry)
        scope_label = f"scope:{scope}"
        lines.append(f"{prefix}  {oid_short}  {effect_type:<20s} {detail}{scope_label}")

        # Release column after the scope's last (oldest) entry.
        if scope_last.get(scope) == i and scope != "ground":
            released_col = columns.get(scope, col)
            _release(scope)
            # Draw merge connector if this scope was merged.
            if scope in scope_merge_target:
                target = scope_merge_target[scope]
                target_col = columns.get(target, 0)
                if released_col != target_col:
                    connector = _render_merge_connector(released_col, target_col, max_col, active_columns)
                    lines.append(connector)

    return lines


def _format_detail(entry: CommitInfo) -> str:
    """Extract a short detail string from entry metadata."""
    effect_type = _metadata_str(entry, "type")

    if effect_type == "ScopeMerge":
        scope = _metadata_str(entry, "scope", default="?")
        target = _metadata_str(entry, "merged_into", default="?")
        return f"({scope} -> {target})  "

    if effect_type == "DiscardSnapshot":
        scope = _metadata_str(entry, "discarded_scope", default="?")
        return f"(discarded {scope})  "

    if effect_type in ("FileCreate", "FilePatch", "FileDelete", "FileRead"):
        path = _metadata_str(entry, "path")
        return f"{path}  " if path else ""

    if effect_type == "Marker":
        label = _metadata_str(entry, "label")
        return f"{label}  " if label else ""

    return ""


def _render_merge_connector(from_col: int, to_col: int, max_col: int, active_columns: set[int]) -> str:
    """Render a merge connector line between two columns.

    from_col is the child (already released). to_col is the parent (still active).
    Draws '/' at the child's former position to show the merge direction.
    """
    parts: list[str] = []
    for c in range(max_col + 1):
        if c == from_col:
            parts.append("/")
        elif c == to_col or c in active_columns:
            parts.append("|")
        else:
            parts.append(" ")

    return " ".join(parts)


def _metadata_str(entry: CommitInfo, key: str, *, default: str = "") -> str:
    value = entry.metadata.get(key, default)
    return value if isinstance(value, str) else default
