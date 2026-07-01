"""Workspace context for git-backed workspaces.

This module provides WorkspaceRef, a git-backed workspace with capability-based
access control.

Example:
    from shepherd_contexts.workspace import WorkspaceRef

    workspace = WorkspaceRef.from_path("/path/to/repo")
    workspace = workspace.with_bash()  # Enable bash capability
"""

from shepherd_core.effects import DiffPatch
from shepherd_runtime.context.sandbox import GitWorktreeSandbox
from shepherd_runtime.lifecycle import register_sandbox_factory
from shepherd_runtime.materialization import register_materializer

from shepherd_contexts.workspace.effects import BashCommand, WorkspacePatchCaptured
from shepherd_contexts.workspace.materializer import (
    WorkspaceMaterializationIntent,
    WorkspaceMaterializer,
)
from shepherd_contexts.workspace.ref import WorkspaceRef

# Register sandbox factory for WorkspaceRef
register_sandbox_factory(
    "WorkspaceRef",
    lambda ctx: GitWorktreeSandbox(
        source_repo=ctx.path,
        base_commit=ctx.base_commit,
        pending_patches=ctx.pending_patches,
    ),
)

# Register materializer for WorkspaceRef
register_materializer("WorkspaceRef", WorkspaceMaterializer())

__all__ = [
    "BashCommand",
    "DiffPatch",
    "WorkspaceMaterializationIntent",
    "WorkspaceMaterializer",
    "WorkspacePatchCaptured",
    "WorkspaceRef",
]
