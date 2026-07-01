"""Tests for WorkspaceRef Git State Machine (Spike B1 Validation).

These tests validate the state transitions defined in DESIGN-git-operation-effects.md.

Design Decisions Validated:
- D5: Separate pending_patches and pending_git_operations
- D6: GitCommitCreated does NOT clear pending_patches
- D7: base_commit is STABLE during execution
- D8: content_hash ignores git structural state

State Fields to be Added:
- current_branch: str | None
- pending_git_operations: tuple[PendingGitOp, ...]
- virtual_branches: frozenset[str]
- virtual_commits: tuple[str, ...]

IMPLEMENTATION NOTE:
Tests marked xfail(strict=True) will fail if they accidentally pass.
This prevents false positives where apply_effect() returns self unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .fixtures import (
    GitBranchCreated,
    GitBranchDeleted,
    GitCheckoutPerformed,
    GitCommitCreated,
)

if TYPE_CHECKING:
    from pathlib import Path

# Note: Individual tests are marked xfail as appropriate.
# Some tests validate existing functionality and should pass.


class TestBranchCreatedEffect:
    """Tests for GitBranchCreated effect application."""

    @pytest.mark.xfail(
        reason="WorkspaceRef.virtual_branches not yet implemented",
        strict=True,
    )
    def test_adds_to_virtual_branches(self, temp_git_repo: Path) -> None:
        """GitBranchCreated adds branch to virtual_branches."""
        # Import here to get the real implementation when available
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        effect = GitBranchCreated(
            branch_name="feature-x",
            from_commit="abc123" + "0" * 34,  # 40 chars
        )

        updated = workspace.apply_effect(effect)

        assert "feature-x" in updated.virtual_branches

    @pytest.mark.xfail(
        reason="WorkspaceRef.pending_git_operations not yet implemented",
        strict=True,
    )
    def test_adds_to_pending_git_operations(self, temp_git_repo: Path) -> None:
        """GitBranchCreated adds operation to pending_git_operations."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        effect = GitBranchCreated(
            branch_name="feature-x",
            from_commit="abc123" + "0" * 34,
        )

        updated = workspace.apply_effect(effect)

        assert len(updated.pending_git_operations) == 1
        assert updated.pending_git_operations[0].op_type == "branch_create"


class TestCommitCreatedEffect:
    """Tests for GitCommitCreated effect application."""

    @pytest.mark.xfail(
        reason="WorkspaceRef.virtual_commits not yet implemented",
        strict=True,
    )
    def test_adds_to_virtual_commits(self, temp_git_repo: Path) -> None:
        """GitCommitCreated adds SHA to virtual_commits."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        effect = GitCommitCreated(
            sha="abc123" + "0" * 34,
            message="Test commit",
            author="Test <test@test.com>",
            parent_shas=(),
        )

        updated = workspace.apply_effect(effect)

        assert "abc123" + "0" * 34 in updated.virtual_commits

    @pytest.mark.xfail(
        reason="WorkspaceRef.virtual_commits not yet implemented - test requires git effect to be processed",
        strict=True,
    )
    def test_does_not_clear_pending_patches(self, temp_git_repo: Path) -> None:
        """D6: GitCommitCreated does NOT clear pending_patches.

        Patches record file changes, commits record structural operations.
        Both are needed for materialization.

        NOTE: This test must verify that the git effect WAS processed
        (virtual_commits updated) AND patches were preserved. Otherwise
        it passes trivially because apply_effect returns self unchanged.
        """
        from shepherd_contexts.workspace import WorkspaceRef
        from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
        from shepherd_core.effects import DiffPatch

        workspace = WorkspaceRef.from_path(temp_git_repo)

        # First, add a patch
        patch_effect = WorkspacePatchCaptured(
            patch=DiffPatch(patch="diff --git a/file.txt b/file.txt\n..."),
        )
        with_patch = workspace.apply_effect(patch_effect)
        assert len(with_patch.pending_patches) == 1

        # Now apply commit - patches should remain
        commit_sha = "abc123" + "0" * 34
        commit_effect = GitCommitCreated(
            sha=commit_sha,
            message="Test commit",
            author="Test <test@test.com>",
            parent_shas=(),
        )

        updated = with_patch.apply_effect(commit_effect)

        # Verify git effect WAS processed (not just ignored)
        assert commit_sha in updated.virtual_commits

        # D6: patches NOT cleared
        assert len(updated.pending_patches) == 1

    @pytest.mark.xfail(
        reason="WorkspaceRef.virtual_commits not yet implemented - test requires git effect to be processed",
        strict=True,
    )
    def test_base_commit_unchanged(self, temp_git_repo: Path) -> None:
        """D7: base_commit is STABLE during execution.

        Only changes on materialization, not during effect application.

        NOTE: This test must verify that the git effect WAS processed
        (virtual_commits updated) AND base_commit was preserved.
        """
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        original_base = workspace.base_commit

        commit_sha = "abc123" + "0" * 34
        effect = GitCommitCreated(
            sha=commit_sha,
            message="Test commit",
            author="Test <test@test.com>",
            parent_shas=(original_base,),
        )

        updated = workspace.apply_effect(effect)

        # Verify git effect WAS processed (not just ignored)
        assert commit_sha in updated.virtual_commits

        # D7: base_commit unchanged
        assert updated.base_commit == original_base


class TestCheckoutPerformedEffect:
    """Tests for GitCheckoutPerformed effect application."""

    @pytest.mark.xfail(
        reason="WorkspaceRef.current_branch not yet implemented",
        strict=True,
    )
    def test_updates_current_branch(self, temp_git_repo: Path) -> None:
        """GitCheckoutPerformed updates current_branch."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        effect = GitCheckoutPerformed(
            target_ref="feature-x",
            previous_ref="main",
        )

        updated = workspace.apply_effect(effect)

        assert updated.current_branch == "feature-x"

    @pytest.mark.xfail(
        reason="WorkspaceRef.current_branch not yet implemented",
        strict=True,
    )
    def test_detached_head_sets_none(self, temp_git_repo: Path) -> None:
        """Checkout to SHA (detached HEAD) sets current_branch to None."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        # Checkout to a SHA (detached HEAD)
        effect = GitCheckoutPerformed(
            target_ref="abc123" + "0" * 34,  # SHA, not branch
            previous_ref="main",
        )

        updated = workspace.apply_effect(effect)

        # Detached HEAD - no current branch
        assert updated.current_branch is None


class TestBranchDeletedEffect:
    """Tests for GitBranchDeleted effect application."""

    @pytest.mark.xfail(
        reason="WorkspaceRef.virtual_branches not yet implemented",
        strict=True,
    )
    def test_removes_from_virtual_branches(self, temp_git_repo: Path) -> None:
        """GitBranchDeleted removes branch from virtual_branches."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        # First create the branch
        create_effect = GitBranchCreated(
            branch_name="to-delete",
            from_commit="abc123" + "0" * 34,
        )
        with_branch = workspace.apply_effect(create_effect)
        assert "to-delete" in with_branch.virtual_branches

        # Now delete it
        delete_effect = GitBranchDeleted(
            branch_name="to-delete",
            was_at_commit="abc123" + "0" * 34,
        )
        updated = with_branch.apply_effect(delete_effect)

        assert "to-delete" not in updated.virtual_branches

    @pytest.mark.xfail(
        reason="WorkspaceRef.current_branch not yet implemented",
        strict=True,
    )
    def test_clears_current_branch_if_deleted(self, temp_git_repo: Path) -> None:
        """Deleting current branch sets current_branch to None."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        # Create and checkout branch
        create = GitBranchCreated(branch_name="current", from_commit="abc123" + "0" * 34)
        checkout = GitCheckoutPerformed(target_ref="current", previous_ref="main")

        ws = workspace.apply_effect(create).apply_effect(checkout)
        assert ws.current_branch == "current"

        # Delete the current branch
        delete = GitBranchDeleted(branch_name="current", was_at_commit="abc123" + "0" * 34)
        updated = ws.apply_effect(delete)

        assert updated.current_branch is None


class TestContentHashStability:
    """Tests for D8: content_hash ignores git structural state.

    These tests validate that adding git fields doesn't break cache key computation.

    IMPORTANT: Each test must verify that the effect WAS processed
    (state changed) AND content_hash remained stable. Otherwise tests
    pass trivially because apply_effect returns self unchanged.
    """

    @pytest.mark.xfail(
        reason="WorkspaceRef.current_branch not yet implemented",
        strict=True,
    )
    def test_content_hash_ignores_current_branch(self, temp_git_repo: Path) -> None:
        """D8: content_hash is same regardless of current_branch."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        # Change current branch
        checkout = GitCheckoutPerformed(target_ref="feature", previous_ref="main")
        updated = workspace.apply_effect(checkout)

        # Verify effect WAS processed (state changed)
        assert updated.current_branch == "feature"

        # D8: content_hash should be the same
        assert workspace.content_hash == updated.content_hash

    @pytest.mark.xfail(
        reason="WorkspaceRef.virtual_branches not yet implemented",
        strict=True,
    )
    def test_content_hash_ignores_virtual_branches(self, temp_git_repo: Path) -> None:
        """D8: content_hash is same regardless of virtual_branches."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        # Add virtual branches
        create = GitBranchCreated(branch_name="virtual", from_commit="abc123" + "0" * 34)
        updated = workspace.apply_effect(create)

        # Verify effect WAS processed (state changed)
        assert "virtual" in updated.virtual_branches

        # D8: content_hash should be the same
        assert workspace.content_hash == updated.content_hash

    @pytest.mark.xfail(
        reason="WorkspaceRef.virtual_commits not yet implemented",
        strict=True,
    )
    def test_content_hash_ignores_virtual_commits(self, temp_git_repo: Path) -> None:
        """D8: content_hash is same regardless of virtual_commits."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        commit_sha = "abc123" + "0" * 34
        commit = GitCommitCreated(
            sha=commit_sha,
            message="Virtual",
            author="Test",
            parent_shas=(),
        )
        updated = workspace.apply_effect(commit)

        # Verify effect WAS processed (state changed)
        assert commit_sha in updated.virtual_commits

        # D8: content_hash should be the same
        assert workspace.content_hash == updated.content_hash


class TestMultipleEffectsSequence:
    """Tests for applying multiple effects in sequence."""

    @pytest.mark.xfail(
        reason="WorkspaceRef git fields not yet implemented",
        strict=True,
    )
    def test_branch_checkout_commit_sequence(self, temp_git_repo: Path) -> None:
        """Typical sequence: create branch, checkout, commit."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        # Create branch
        e1 = GitBranchCreated(branch_name="feature", from_commit=workspace.base_commit)
        ws1 = workspace.apply_effect(e1)

        # Checkout
        e2 = GitCheckoutPerformed(target_ref="feature", previous_ref="main")
        ws2 = ws1.apply_effect(e2)

        # Commit
        e3 = GitCommitCreated(
            sha="newcommit" + "0" * 31,
            message="Feature commit",
            author="Test",
            parent_shas=(workspace.base_commit,),
        )
        ws3 = ws2.apply_effect(e3)

        # Verify state
        assert "feature" in ws3.virtual_branches
        assert ws3.current_branch == "feature"
        assert "newcommit" + "0" * 31 in ws3.virtual_commits
        assert len(ws3.pending_git_operations) == 2  # branch + commit
