"""Shared CLI wording for the managed workspace boundary."""

from __future__ import annotations

from pathlib import Path

ENVIRONMENT_BOUNDARY_LINE = "Environment: host state outside workspace is untracked"
INIT_ENVIRONMENT_BOUNDARY_LINE = "Host environment outside this workspace is untracked."


def managed_workspace_path(workspace: str | Path) -> str:
    """Return the canonical display path for a managed workspace."""
    return str(Path(workspace).expanduser().resolve())


def managed_workspace_line(workspace: str | Path, *, indent: str = "") -> str:
    return f"{indent}Managed workspace: {managed_workspace_path(workspace)}"


def environment_boundary_line(*, indent: str = "") -> str:
    return f"{indent}{ENVIRONMENT_BOUNDARY_LINE}"
