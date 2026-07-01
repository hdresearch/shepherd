"""LocalSimulatedDevice - Mock Container for Testing.

Validated by Spike X1: Container-Free Integration Testing.

This device simulates container isolation using temporary directories,
providing ~80% fidelity for effect extraction testing without requiring
Podman/container runtime.

Limitations (documented in X1):
- No true isolation (can still access host filesystem via symlinks)
- Git operations affect real .git (in the clone)
- No overlay semantics (just a clone)

Use cases:
- Integration tests without containers
- CI environments without Podman
- Fast development iteration

For full E2E validation, use real containers on main branch.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from typing_extensions import Self

from .effects import (
    GitBranchCreated,
    GitBranchDeleted,
    GitCheckoutPerformed,
    GitCommitCreated,
    GitTagCreated,
)
from .git_state_reader import GitStateReader, GitStateSnapshot

if TYPE_CHECKING:
    from typing import Any


@dataclass
class LocalSimulatedDevice:
    """Device that simulates container isolation using temp directories.

    Provides ~80% fidelity for effect extraction testing.

    Usage:
        with LocalSimulatedDevice(workspace_path) as device:
            device.execute("git checkout -b feature")
            device.execute("echo 'hello' > file.txt")
            device.execute("git add . && git commit -m 'Add file'")
            effects = device.extract_git_effects()

        assert any(e.effect_type == "git_branch_created" for e in effects)
    """

    workspace_path: Path
    sandbox_dir: Path | None = field(default=None, init=False)
    _before_snapshot: GitStateSnapshot | None = field(default=None, init=False)
    _reader: GitStateReader | None = field(default=None, init=False)

    def __enter__(self) -> Self:
        """Set up sandboxed workspace clone."""
        # Create temp directory
        self.sandbox_dir = Path(tempfile.mkdtemp(prefix="shepherd-mock-device-"))

        # Clone workspace (local clone, fast)
        result = subprocess.run(
            [
                "git",
                "clone",
                "--local",
                str(self.workspace_path),
                str(self.sandbox_dir / "workspace"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone workspace: {result.stderr}")

        # Initialize reader
        self._reader = GitStateReader(self.sandbox_dir / "workspace" / ".git")

        # Capture before state for diffing
        self._before_snapshot = self._reader.snapshot()

        return self

    def __exit__(self, *args: object) -> None:
        """Clean up sandbox directory."""
        if self.sandbox_dir and self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir)
        self.sandbox_dir = None
        self._before_snapshot = None
        self._reader = None

    @property
    def workspace(self) -> Path:
        """Path to sandboxed workspace."""
        if self.sandbox_dir is None:
            raise RuntimeError("Device not entered - use 'with' statement")
        return self.sandbox_dir / "workspace"

    def execute(self, command: str) -> subprocess.CompletedProcess[str]:
        """Execute a command in the sandboxed workspace.

        Args:
            command: Shell command to execute.

        Returns:
            CompletedProcess with stdout, stderr, returncode.
        """
        if self.sandbox_dir is None:
            raise RuntimeError("Device not entered - use 'with' statement")

        return subprocess.run(
            command,
            check=False,
            shell=True,
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )

    def run_git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run a git command in the sandboxed workspace.

        Args:
            *args: Git command arguments (without 'git' prefix).

        Returns:
            CompletedProcess with stdout, stderr, returncode.
        """
        if self.sandbox_dir is None:
            raise RuntimeError("Device not entered - use 'with' statement")

        return subprocess.run(
            ["git", *args],
            check=False,
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )

    def extract_git_effects(self) -> list[Any]:
        """Extract git effects by diffing before/after state.

        This is the core value of the mock device - it captures git
        operations as effects without needing container overlay.

        Returns:
            List of git effects (GitBranchCreated, etc.)
        """
        if self._reader is None or self._before_snapshot is None:
            raise RuntimeError("Device not entered - use 'with' statement")

        after = self._reader.snapshot()
        effects: list[Any] = []

        # Detect new branches
        before_branches = set(self._before_snapshot.branches.keys())
        after_branches = set(after.branches.keys())

        for branch_name in after_branches - before_branches:
            effects.append(
                GitBranchCreated(
                    branch_name=branch_name,
                    from_commit=after.branches[branch_name],
                )
            )

        # Detect deleted branches
        for branch_name in before_branches - after_branches:
            effects.append(
                GitBranchDeleted(
                    branch_name=branch_name,
                    was_at_commit=self._before_snapshot.branches[branch_name],
                )
            )

        # Detect HEAD/branch change (checkout)
        if after.head_ref != self._before_snapshot.head_ref:
            effects.append(
                GitCheckoutPerformed(
                    target_ref=after.head_ref or after.head_commit,
                    previous_ref=self._before_snapshot.head_ref,
                )
            )

        # Detect new commits (by checking if current branch moved forward)
        # This is a simplification - real implementation would parse objects
        for branch_name in before_branches & after_branches:
            old_sha = self._before_snapshot.branches[branch_name]
            new_sha = after.branches[branch_name]
            if old_sha != new_sha:
                # Branch moved - likely a commit (could be reset, merge, etc.)
                commit = self._reader.read_commit(new_sha)
                if commit:
                    effects.append(
                        GitCommitCreated(
                            sha=new_sha,
                            message=commit.get("message", ""),
                            author=commit.get("author", ""),
                            parent_shas=tuple(commit.get("parents", [])),
                        )
                    )

        # Detect new tags
        before_tags = set(self._before_snapshot.tags.keys())
        after_tags = set(after.tags.keys())

        for tag_name in after_tags - before_tags:
            effects.append(
                GitTagCreated(
                    tag_name=tag_name,
                    at_commit=after.tags[tag_name],
                )
            )

        return effects

    def get_current_snapshot(self) -> GitStateSnapshot:
        """Get current git state snapshot.

        Returns:
            Current GitStateSnapshot.
        """
        if self._reader is None:
            raise RuntimeError("Device not entered - use 'with' statement")
        return self._reader.snapshot()

    def get_file_content(self, path: str) -> str | None:
        """Read a file from the sandboxed workspace.

        Args:
            path: Relative path within workspace.

        Returns:
            File content as string, or None if not found.
        """
        full_path = self.workspace / path
        if full_path.exists():
            return full_path.read_text()
        return None

    def write_file(self, path: str, content: str) -> None:
        """Write a file to the sandboxed workspace.

        Args:
            path: Relative path within workspace.
            content: File content to write.
        """
        full_path = self.workspace / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)


# =============================================================================
# Materialization Testing Support
# =============================================================================


@dataclass
class MaterializationAttempt:
    """Record of an effect materialization attempt.

    Used by test materialization flow to track success/failure.
    """

    effect_type: str
    effect_id: str
    success: bool
    error: str | None = None
    rollback_action: str | None = None


@dataclass
class PartialMaterializationResult:
    """Result of partial materialization (Design Decision D11).

    D11: STOP_ON_FIRST - Stop materialization at first failure,
    provide manual rollback instructions.
    """

    overall_success: bool
    effects_applied: int
    effects_failed: int
    effects_skipped: int
    attempts: list[MaterializationAttempt] = field(default_factory=list)
    recovery_actions: list[str] = field(default_factory=list)
