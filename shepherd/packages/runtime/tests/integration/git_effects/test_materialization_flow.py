"""Tests for Materialization Flow (Spikes E1/C1 Validation).

These tests validate the STOP_ON_FIRST materialization strategy and
conflict resolution. Tests are marked xfail until GitOperationMaterializer
is implemented.

Design Decisions Validated:
- D11: STOP_ON_FIRST - Stop at first failure, provide manual rollback
- C1: Conflict resolution table (same SHA = no-op, different SHA = fail)
- E1: 4 failure scenarios documented
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from .fixtures import (
    GitBranchCreated,
    GitBranchDeleted,
    GitCheckoutPerformed,
    GitCommitCreated,
)
from .fixtures.mock_device import MaterializationAttempt, PartialMaterializationResult

if TYPE_CHECKING:
    from pathlib import Path


# Most tests are xfail until materializer is implemented
class TestMaterializationAttemptDataclass:
    """Tests for MaterializationAttempt dataclass (should pass)."""

    def test_successful_attempt(self) -> None:
        """Create a successful attempt record."""
        attempt = MaterializationAttempt(
            effect_type="git_branch_created",
            effect_id="123",
            success=True,
        )
        assert attempt.success
        assert attempt.error is None
        assert attempt.rollback_action is None

    def test_failed_attempt_with_error(self) -> None:
        """Create a failed attempt with error message."""
        attempt = MaterializationAttempt(
            effect_type="git_branch_created",
            effect_id="456",
            success=False,
            error="Branch 'main' already exists at different SHA",
            rollback_action="git branch -D main",
        )
        assert not attempt.success
        assert "already exists" in attempt.error
        assert attempt.rollback_action is not None


class TestPartialMaterializationResult:
    """Tests for PartialMaterializationResult dataclass (should pass)."""

    def test_successful_result(self) -> None:
        """Result for fully successful materialization."""
        result = PartialMaterializationResult(
            overall_success=True,
            effects_applied=3,
            effects_failed=0,
            effects_skipped=0,
        )
        assert result.overall_success
        assert result.effects_applied == 3

    def test_partial_failure_result(self) -> None:
        """Result for D11 STOP_ON_FIRST scenario."""
        result = PartialMaterializationResult(
            overall_success=False,
            effects_applied=1,
            effects_failed=1,
            effects_skipped=2,
            attempts=[
                MaterializationAttempt("git_branch_created", "1", True),
                MaterializationAttempt("git_commit_created", "2", False, "Parent not found"),
            ],
            recovery_actions=["git branch -D feature-x"],
        )
        assert not result.overall_success
        assert result.effects_skipped == 2
        assert len(result.recovery_actions) == 1


@pytest.mark.xfail(
    reason="GitOperationMaterializer not yet implemented",
    strict=True,
)
class TestStopOnFirstStrategy:
    """Tests for D11: STOP_ON_FIRST materialization strategy."""

    def test_stops_at_first_failure(self, temp_git_repo: Path) -> None:
        """Materialization stops at first failure, skips rest."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        materializer = GitOperationMaterializer(temp_git_repo)

        # Get current HEAD for valid from_commit
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        effects = [
            GitBranchCreated(branch_name="will-succeed", from_commit=head),
            GitBranchCreated(branch_name="will-fail", from_commit="nonexistent" + "0" * 31),
            GitBranchCreated(branch_name="never-tried", from_commit=head),
        ]

        result = materializer.materialize(effects)

        assert not result.overall_success
        assert result.effects_applied == 1
        assert result.effects_failed == 1
        assert result.effects_skipped == 1

    def test_provides_recovery_actions(self, temp_git_repo: Path) -> None:
        """Failed materialization provides rollback instructions."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        materializer = GitOperationMaterializer(temp_git_repo)

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        effects = [
            GitBranchCreated(branch_name="to-rollback", from_commit=head),
            GitBranchCreated(branch_name="fails", from_commit="bad" + "0" * 37),
        ]

        result = materializer.materialize(effects)

        assert len(result.recovery_actions) > 0
        assert any("to-rollback" in action for action in result.recovery_actions)


@pytest.mark.xfail(
    reason="GitOperationMaterializer not yet implemented",
    strict=True,
)
class TestConflictResolution:
    """Tests for C1: Conflict resolution scenarios."""

    def test_new_branch_creates(self, temp_git_repo: Path) -> None:
        """C1: New branch at valid SHA → CREATE."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        materializer = GitOperationMaterializer(temp_git_repo)

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        result = materializer._create_branch("new-branch", head)

        assert result.success
        # Verify branch was created
        branches = subprocess.run(
            ["git", "branch", "--list", "new-branch"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new-branch" in branches.stdout

    def test_existing_branch_same_sha_is_noop(self, temp_git_repo: Path) -> None:
        """C1: Branch at same SHA → NO-OP (idempotent)."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        # Create branch first
        subprocess.run(
            ["git", "branch", "existing"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        head = subprocess.run(
            ["git", "rev-parse", "existing"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        materializer = GitOperationMaterializer(temp_git_repo)
        result = materializer._create_branch("existing", head)

        # Should succeed (no-op)
        assert result.success

    def test_existing_branch_different_sha_fails(self, temp_git_repo: Path) -> None:
        """C1: Branch at different SHA → CONFLICT (fails)."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        # Create branch first
        subprocess.run(
            ["git", "branch", "conflicting"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        materializer = GitOperationMaterializer(temp_git_repo)
        result = materializer._create_branch("conflicting", "different" + "0" * 32)

        assert not result.success
        assert "exists at" in result.error or "different" in result.error.lower()

    def test_nonexistent_target_sha_fails(self, temp_git_repo: Path) -> None:
        """C1: Target SHA doesn't exist → FAIL."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        materializer = GitOperationMaterializer(temp_git_repo)

        result = materializer._create_branch("bad-target", "0" * 40)

        assert not result.success


@pytest.mark.xfail(
    reason="GitOperationMaterializer not yet implemented",
    strict=True,
)
class TestOrderingInvariants:
    """Tests for C2: Effect ordering invariants."""

    def test_checkout_before_branch_create_fails(self, temp_git_repo: Path) -> None:
        """C2: Cannot checkout branch before it exists (unless pre-existing)."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        materializer = GitOperationMaterializer(temp_git_repo)

        # Try to checkout non-existent branch
        effects = [
            GitCheckoutPerformed(target_ref="not-yet-created"),
        ]

        result = materializer.materialize(effects)

        assert not result.overall_success

    def test_delete_checked_out_branch_fails(self, temp_git_repo: Path) -> None:
        """C2: Cannot delete currently checked-out branch."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        materializer = GitOperationMaterializer(temp_git_repo)

        # Try to delete current branch (main/master)
        current = subprocess.run(
            ["git", "branch", "--show-current"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        effects = [
            GitBranchDeleted(branch_name=current, was_at_commit=head),
        ]

        result = materializer.materialize(effects)

        assert not result.overall_success


@pytest.mark.xfail(
    reason="GitOperationMaterializer not yet implemented",
    strict=True,
)
class TestShaTranslationDuringMaterialization:
    """Tests for D12: SHA translation during materialization."""

    def test_commit_parent_translated(self, temp_git_repo: Path) -> None:
        """Parent SHA references are translated during commit creation."""
        from shepherd_contexts.workspace.materializer import GitOperationMaterializer

        materializer = GitOperationMaterializer(temp_git_repo)

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        effects = [
            GitBranchCreated(branch_name="feature", from_commit=head),
            GitCheckoutPerformed(target_ref="feature"),
            GitCommitCreated(
                sha="original_sha_1" + "0" * 26,
                message="First commit",
                author="Test <test@test.com>",
                parent_shas=(head,),
            ),
            GitCommitCreated(
                sha="original_sha_2" + "0" * 26,
                message="Second commit",
                author="Test <test@test.com>",
                parent_shas=("original_sha_1" + "0" * 26,),  # References first commit
            ),
        ]

        result = materializer.materialize(effects)

        assert result.overall_success
        # The materialized second commit should have first commit as parent
        # (via SHA translation)
