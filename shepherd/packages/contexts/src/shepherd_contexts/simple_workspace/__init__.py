"""SimpleWorkspace: Non-git file workspace context for Shepherd framework.

This module provides SimpleWorkspace, a file workspace that doesn't require
git backing. It uses file manifests and changesets for state tracking.

Usage:
    from shepherd_contexts.simple_workspace import SimpleWorkspace

    # Create from existing directory
    workspace = SimpleWorkspace.from_path("/path/to/dir")

    # Create empty workspace
    workspace = SimpleWorkspace.empty("/tmp/scratch")

    # Create read-only workspace
    workspace = SimpleWorkspace.readonly("/path/to/dir")

Key Classes:
    SimpleWorkspace: Main context class implementing ExecutionContext protocol
    FileManifest: Snapshot of file state (paths, sizes, mtimes)
    FileChangeset: Collection of file deltas from execution
    CopySandbox: Copy-based sandbox for isolated execution
"""

# Register CopySandbox factory with lifecycle
# This enables automatic sandbox creation for SimpleWorkspace contexts
from shepherd_runtime.lifecycle import register_sandbox_factory

from shepherd_contexts.simple_workspace.context import SimpleWorkspace
from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
from shepherd_contexts.simple_workspace.effects import (
    SimpleWorkspaceChangesetCaptured,
    SimpleWorkspaceInitialized,
    SimpleWorkspaceMaterialized,
)
from shepherd_contexts.simple_workspace.manifest import FileEntry, FileManifest
from shepherd_contexts.simple_workspace.materializer import (
    SimpleWorkspaceMaterializationIntent,
    SimpleWorkspaceMaterializer,
)
from shepherd_contexts.simple_workspace.sandbox import CopySandbox

register_sandbox_factory("SimpleWorkspace", CopySandbox)

# Register materializer for SimpleWorkspace
# This enables scope.commit() to apply changesets to filesystem
from shepherd_runtime.materialization import register_materializer

register_materializer("SimpleWorkspace", SimpleWorkspaceMaterializer())

__all__ = [
    # Sandbox
    "CopySandbox",
    "FileChangeset",
    # Delta types
    "FileDelta",
    # Manifest types
    "FileEntry",
    "FileManifest",
    # Main context
    "SimpleWorkspace",
    # Effects
    "SimpleWorkspaceChangesetCaptured",
    "SimpleWorkspaceInitialized",
    "SimpleWorkspaceMaterializationIntent",
    "SimpleWorkspaceMaterialized",
    # Materializer
    "SimpleWorkspaceMaterializer",
]
