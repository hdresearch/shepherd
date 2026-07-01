"""Materialization support for WorkspaceRef.

Provides:
- WorkspaceMaterializationIntent: Describes patches to apply
- WorkspaceMaterializer: Applies patches via git
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from shepherd_runtime.materialization import MaterializationIntent, MaterializationResult

if TYPE_CHECKING:
    from pathlib import Path

    from shepherd_core.effects import DiffPatch

logger = logging.getLogger(__name__)

# =============================================================================
# Intent
# =============================================================================


@dataclass(frozen=True)
class WorkspaceMaterializationIntent(MaterializationIntent):
    """Intent for WorkspaceRef materialization.

    Contains the patches to apply and optional commit message.

    Attributes:
        patches: The git patches to apply
        commit_message: Optional commit message (if committing after apply)
        expected_base_commit: Expected HEAD commit for drift detection.
            If set and current HEAD differs, materialization fails.
    """

    context_type: str = field(default="WorkspaceRef", init=False)
    patches: tuple[DiffPatch, ...] = ()
    commit_message: str | None = None
    expected_base_commit: str | None = None

    def with_commit_message(self, message: str) -> WorkspaceMaterializationIntent:
        """Return intent with commit message set."""
        return replace(self, commit_message=message)


# =============================================================================
# Materializer
# =============================================================================


class WorkspaceMaterializer:
    """Materializer for WorkspaceRef contexts.

    Uses git apply to apply patches to the repository.
    Optionally creates a git commit.

    This class is stateless - all state is passed via intent/result.

    Features:
    - Drift detection: Verifies HEAD matches expected base commit before applying
    - Rollback support: Can undo via git reset
    """

    def _get_head_commit(self, repo_path: Path) -> str | None:
        """Get the current HEAD commit SHA.

        Args:
            repo_path: Path to the git repository

        Returns:
            The full SHA of HEAD, or None if not a git repo or error
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except OSError as e:
            logger.debug("Could not run git rev-parse HEAD at %s: %s", repo_path, e)
            return None

    def _check_drift(self, repo_path: Path, expected_commit: str) -> str | None:
        """Check if repository HEAD matches expected commit.

        Args:
            repo_path: Path to the git repository
            expected_commit: Expected HEAD commit SHA

        Returns:
            Error message if drift detected, None if HEAD matches
        """
        current_head = self._get_head_commit(repo_path)

        if current_head is None:
            return "Drift detection failed: unable to read HEAD commit"

        # Compare commits (handle both full and short SHAs)
        if not (current_head.startswith(expected_commit) or expected_commit.startswith(current_head)):
            return (
                f"Drift detected: expected HEAD at {expected_commit[:8]}, "
                f"but found {current_head[:8]}. Repository was modified externally."
            )

        return None

    def materialize(self, intent: WorkspaceMaterializationIntent) -> MaterializationResult:
        """Apply patches to the repository.

        Args:
            intent: The materialization intent with patches

        Returns:
            MaterializationResult with success/failure and metadata
        """
        if not intent.patches:
            return MaterializationResult.ok()

        # Drift detection: verify HEAD is at expected commit
        if intent.expected_base_commit:
            drift_error = self._check_drift(intent.target_path, intent.expected_base_commit)
            if drift_error:
                return MaterializationResult.failure(drift_error)

        affected_paths: list[str] = []
        target_path = intent.target_path

        try:
            # Apply each patch
            for patch in intent.patches:
                if not patch.patch.strip():
                    continue

                result = subprocess.run(
                    ["git", "apply", "--index"],
                    input=patch.patch,
                    cwd=str(target_path),
                    capture_output=True,
                    text=True,
                    check=False,
                )

                if result.returncode != 0:
                    return MaterializationResult.failure(f"git apply failed: {result.stderr}")

                affected_paths.extend(patch.files_changed)

            # Optionally commit
            metadata: dict[str, str] = {}
            if intent.commit_message:
                commit_result = subprocess.run(
                    ["git", "commit", "-m", intent.commit_message],
                    cwd=str(target_path),
                    capture_output=True,
                    text=True,
                    check=False,
                )

                if commit_result.returncode != 0:
                    return MaterializationResult.failure(f"git commit failed: {commit_result.stderr}")

                # Get new commit SHA
                sha_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(target_path),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                metadata["commit_sha"] = sha_result.stdout.strip()
                metadata["committed"] = "true"
            else:
                metadata["committed"] = "false"

            return MaterializationResult.ok(
                paths_affected=tuple(set(affected_paths)),
                **metadata,
            )

        except Exception as e:  # noqa: BLE001
            return MaterializationResult.failure(str(e))

    def can_rollback(self) -> bool:
        """WorkspaceMaterializer supports rollback via git reset."""
        return True

    def rollback(
        self,
        intent: WorkspaceMaterializationIntent,
        result: MaterializationResult,
    ) -> None:
        """Rollback materialization via git reset.

        If a commit was made, reset to HEAD~1.
        If only patches were applied (no commit), reset to HEAD.
        """
        target_path = intent.target_path
        committed = result.metadata.get("committed") == "true"

        if committed:
            # Undo the commit
            subprocess.run(
                ["git", "reset", "--hard", "HEAD~1"],
                cwd=str(target_path),
                capture_output=True,
                check=False,
            )
        else:
            # Just reset the index and working tree
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=str(target_path),
                capture_output=True,
                check=False,
            )


__all__ = [
    "WorkspaceMaterializationIntent",
    "WorkspaceMaterializer",
]
