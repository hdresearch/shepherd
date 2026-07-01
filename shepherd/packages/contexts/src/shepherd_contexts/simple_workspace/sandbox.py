"""Copy-based sandbox for SimpleWorkspace.

This module provides CopySandbox, which creates an isolated copy of a
workspace for agent execution using shutil.copytree.

Performance (validated by SW-04 spike):
- 10 files: ~2ms
- 100 files: ~21ms
- 500 files: ~99ms
- 1000 files: ~211ms

Implements the V2 Sandbox protocol (including git_diff() via difflib).
"""

from __future__ import annotations

import difflib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shepherd_core.context import ExecutionContext

    from shepherd_contexts.simple_workspace.context import SimpleWorkspace
    from shepherd_contexts.simple_workspace.delta import FileDelta


class CopySandbox:
    """Copy-based sandbox for SimpleWorkspace.

    Creates a temporary copy of the workspace for isolated execution.
    Changes are captured by comparing against the base snapshot.

    V2 Architecture: This sandbox is created by ExecutionLifecycle,
    not by SimpleWorkspace. The lifecycle calls _create_sandbox(context)
    and passes the result to context.extract_effects(sandbox, result).

    Implements full Sandbox protocol including git_diff() via difflib.
    """

    def __init__(self, workspace: SimpleWorkspace):
        """Initialize sandbox for workspace.

        Args:
            workspace: SimpleWorkspace to create sandbox for
        """
        self.workspace = workspace
        self._sandbox_path: Path | None = None
        self._base_snapshot: dict[str, bytes] = {}  # For git_diff computation
        self._base_files: set[str] = set()  # Files that existed at setup time

    def setup(self, context: ExecutionContext) -> None:
        """Create temporary copy of workspace and apply pending changesets.

        Args:
            context: ExecutionContext (typically the SimpleWorkspace)

        The setup process:
        1. Create temp directory
        2. Copy workspace files
        3. Apply pending changesets
        4. Capture base snapshot for git_diff computation
        """
        # Import here to avoid circular imports
        from shepherd_contexts.simple_workspace.encoding import get_encoder

        self._sandbox_path = Path(tempfile.mkdtemp(prefix="simple-sandbox-"))

        source = Path(self.workspace.path)
        if source.exists() and source.is_dir():
            # Check if directory has content
            try:
                has_content = any(source.iterdir())
            except PermissionError:
                has_content = False

            if has_content:
                # Use shutil.copytree (validated as fastest by SW-04)
                shutil.copytree(source, self._sandbox_path, dirs_exist_ok=True)

        # Apply pending changesets with content tracking for delta chains
        content_cache: dict[str, bytes] = {}
        encoder = get_encoder()

        for changeset in self.workspace.pending_changesets:
            for delta in changeset.deltas:
                self._apply_delta(delta, content_cache, encoder)

        # Capture base snapshot AFTER applying changesets (for git_diff)
        # This represents the "expected" state before agent execution
        self._capture_base_snapshot()

    def _apply_delta(
        self,
        delta: FileDelta,
        content_cache: dict[str, bytes],
        encoder: any,
    ) -> None:
        """Apply a delta to sandbox, maintaining content cache for chains.

        Args:
            delta: The file delta to apply
            content_cache: Mutable dict tracking file contents for delta chains
            encoder: ContentEncoder for decoding
        """
        if self._sandbox_path is None:
            return

        file_path = self._sandbox_path / delta.path

        if delta.operation == "delete":
            if file_path.exists():
                file_path.unlink()
            content_cache.pop(delta.path, None)

        elif delta.operation in ("create", "modify"):
            file_path.parent.mkdir(parents=True, exist_ok=True)

            if delta.content is not None:
                # Get old content from cache first (for delta chains),
                # then fall back to reading from sandbox
                old_content = None
                if delta.encoding == "delta":
                    old_content = content_cache.get(delta.path)
                    if old_content is None and file_path.exists():
                        old_content = file_path.read_bytes()

                # Decode with hash verification
                content = encoder.decode_and_verify(
                    delta.encoding,
                    delta.content,
                    old_content,
                    expected_hash=delta.new_content_hash,
                    path=delta.path,
                )
                file_path.write_bytes(content)

                # Set mode if specified
                if delta.new_mode is not None:
                    file_path.chmod(delta.new_mode)

                # Update cache for subsequent deltas
                content_cache[delta.path] = content

    def _capture_base_snapshot(self) -> None:
        """Capture content of all files for git_diff computation."""
        if self._sandbox_path is None:
            return

        self._base_snapshot = {}
        self._base_files = set()

        for file_path in self._sandbox_path.rglob("*"):
            if file_path.is_file():
                rel_path = str(file_path.relative_to(self._sandbox_path))
                self._base_files.add(rel_path)
                try:
                    self._base_snapshot[rel_path] = file_path.read_bytes()
                except OSError as e:
                    from shepherd_core import is_strict_mode
                    from shepherd_core.errors import SandboxSnapshotError

                    if is_strict_mode():
                        raise SandboxSnapshotError(rel_path, str(self._sandbox_path), e) from e
                    logger.warning("Skipping unreadable file %s: %s", rel_path, e)

    @property
    def path(self) -> Path:
        """Root path of sandbox filesystem."""
        if self._sandbox_path is None:
            raise RuntimeError("Sandbox not set up - call setup() first")
        return self._sandbox_path

    def changed_files(self) -> Sequence[str]:
        """List files changed in sandbox vs base snapshot."""
        if self._sandbox_path is None:
            return []

        # Get current files
        current_files: set[str] = set()
        for file_path in self._sandbox_path.rglob("*"):
            if file_path.is_file():
                rel_path = str(file_path.relative_to(self._sandbox_path))
                current_files.add(rel_path)

        # Find changes
        added = current_files - self._base_files
        removed = self._base_files - current_files
        modified: set[str] = set()

        for rel_path in self._base_files & current_files:
            file_path = self._sandbox_path / rel_path
            try:
                current_content = file_path.read_bytes()
                if current_content != self._base_snapshot.get(rel_path, b""):
                    modified.add(rel_path)
            except OSError:
                modified.add(rel_path)

        return sorted(added | modified | removed)

    def git_diff(self) -> str:
        """Return unified diff of changes (V2 Sandbox protocol compliance).

        Uses difflib to generate unified diffs, not actual git.
        This provides protocol compatibility while not requiring git.

        Returns:
            Unified diff string in git-diff format
        """
        if self._sandbox_path is None:
            return ""

        diff_parts: list[str] = []
        changed = self.changed_files()

        for rel_path in changed:
            sandbox_file = self._sandbox_path / rel_path

            # Get old content from base snapshot
            old_content = self._base_snapshot.get(rel_path, b"")

            # Get new content from sandbox
            if sandbox_file.exists():
                try:
                    new_content = sandbox_file.read_bytes()
                except OSError:
                    new_content = b""
            else:
                new_content = b""  # File was deleted

            # Try to decode as text for diffing
            try:
                old_lines = old_content.decode("utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                old_lines = [f"Binary file ({len(old_content)} bytes)\n"]

            try:
                new_lines = new_content.decode("utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                new_lines = [f"Binary file ({len(new_content)} bytes)\n"]

            # Handle empty files (ensure we have at least empty content to diff)
            if not old_lines and not new_lines:
                continue  # No actual change

            # Generate unified diff
            diff = difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
            diff_parts.extend(diff)

        return "".join(diff_parts)

    def discard(self) -> None:
        """Remove sandbox and cleanup resources.

        Safe to call multiple times (idempotent).
        """
        if self._sandbox_path and self._sandbox_path.exists():
            try:
                shutil.rmtree(self._sandbox_path)
            except OSError as e:
                logger.debug("Failed to remove sandbox directory %s: %s", self._sandbox_path, e)
            self._sandbox_path = None
            self._base_snapshot = {}
            self._base_files = set()
