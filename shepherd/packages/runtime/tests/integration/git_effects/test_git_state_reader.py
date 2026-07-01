"""Tests for GitStateReader (Spike A1 Validation).

These tests validate that direct .git reading works correctly.
All tests should PASS with the prototype implementation.

Design Decisions Validated:
- D1: Direct file reading is primary approach (46x faster)
- A4: Loose refs/objects work (no dulwich needed)
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from .fixtures import GitStateReader

if TYPE_CHECKING:
    from pathlib import Path


class TestGitStateReaderBasics:
    """Basic functionality tests."""

    def test_snapshot_returns_valid_object(self, temp_git_repo: Path) -> None:
        """Snapshot returns a GitStateSnapshot with expected fields."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        assert snapshot is not None
        assert isinstance(snapshot.branches, dict)
        assert isinstance(snapshot.tags, dict)
        assert snapshot.head_commit  # Should have a commit

    def test_reads_main_branch(self, temp_git_repo: Path) -> None:
        """Can read the main/master branch from refs/heads."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        # Git might use 'main' or 'master' depending on config
        assert "main" in snapshot.branches or "master" in snapshot.branches

    def test_reads_head_ref(self, temp_git_repo: Path) -> None:
        """Can read current branch from HEAD."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        # Should be on main or master
        assert snapshot.head_ref in ("main", "master")

    def test_reads_head_commit_sha(self, temp_git_repo: Path) -> None:
        """HEAD commit is a valid 40-char SHA."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        assert len(snapshot.head_commit) == 40
        assert all(c in "0123456789abcdef" for c in snapshot.head_commit)


class TestBranchOperations:
    """Tests for branch-related operations."""

    def test_detects_new_branch(self, temp_git_repo: Path) -> None:
        """Creating a branch adds it to snapshot.branches."""
        reader = GitStateReader(temp_git_repo / ".git")

        before = reader.snapshot()
        assert "test-branch" not in before.branches

        # Create branch
        subprocess.run(
            ["git", "checkout", "-b", "test-branch"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        after = reader.snapshot()
        assert "test-branch" in after.branches
        assert after.branches["test-branch"] == before.head_commit

    def test_detects_branch_with_nested_name(self, temp_git_repo: Path) -> None:
        """Branches with slashes (feature/foo) are handled correctly."""
        reader = GitStateReader(temp_git_repo / ".git")

        subprocess.run(
            ["git", "checkout", "-b", "feature/nested/branch"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        snapshot = reader.snapshot()
        assert "feature/nested/branch" in snapshot.branches

    def test_get_branch_sha(self, temp_git_repo: Path) -> None:
        """get_branch_sha returns correct SHA for existing branch."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        # Get SHA via helper method
        branch_name = "main" if "main" in snapshot.branches else "master"
        sha = reader.get_branch_sha(branch_name)

        assert sha == snapshot.branches[branch_name]

    def test_get_branch_sha_nonexistent(self, temp_git_repo: Path) -> None:
        """get_branch_sha returns None for non-existent branch."""
        reader = GitStateReader(temp_git_repo / ".git")
        sha = reader.get_branch_sha("nonexistent-branch")
        assert sha is None


class TestCommitParsing:
    """Tests for commit object parsing (A4 validation)."""

    def test_parses_head_commit(self, temp_git_repo: Path) -> None:
        """Can parse the HEAD commit from loose objects."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        commit = reader.read_commit(snapshot.head_commit)

        assert commit is not None
        assert commit["type"] == "commit"
        assert commit["sha"] == snapshot.head_commit
        assert "message" in commit
        assert "Initial commit" in commit["message"]

    def test_commit_has_tree(self, temp_git_repo: Path) -> None:
        """Parsed commit includes tree SHA."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        commit = reader.read_commit(snapshot.head_commit)

        assert commit is not None
        assert "tree" in commit
        assert len(commit["tree"]) == 40

    def test_commit_has_author(self, temp_git_repo: Path) -> None:
        """Parsed commit includes author info."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        commit = reader.read_commit(snapshot.head_commit)

        assert commit is not None
        assert "author" in commit
        assert "Shepherd Test" in commit["author"]

    def test_commit_parent_chain(self, temp_git_repo: Path) -> None:
        """Commits correctly report parent SHAs."""
        reader = GitStateReader(temp_git_repo / ".git")

        # Create a second commit
        (temp_git_repo / "second.txt").write_text("Second file\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Second commit"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        snapshot = reader.snapshot()
        commit = reader.read_commit(snapshot.head_commit)

        assert commit is not None
        assert len(commit["parents"]) == 1
        assert len(commit["parents"][0]) == 40

    def test_read_nonexistent_commit_returns_none(self, temp_git_repo: Path) -> None:
        """Reading non-existent SHA returns None."""
        reader = GitStateReader(temp_git_repo / ".git")

        result = reader.read_commit("0" * 40)  # Non-existent SHA
        assert result is None


class TestTreeParsing:
    """Tests for tree object parsing."""

    def test_parses_tree_from_commit(self, temp_git_repo: Path) -> None:
        """Can parse tree object referenced by commit."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        commit = reader.read_commit(snapshot.head_commit)
        assert commit is not None

        tree = reader.read_tree(commit["tree"])

        assert tree is not None
        assert len(tree) > 0
        assert any(entry["name"] == "README.md" for entry in tree)

    def test_tree_entries_have_mode(self, temp_git_repo: Path) -> None:
        """Tree entries include file mode."""
        reader = GitStateReader(temp_git_repo / ".git")
        snapshot = reader.snapshot()

        commit = reader.read_commit(snapshot.head_commit)
        tree = reader.read_tree(commit["tree"])

        assert tree is not None
        for entry in tree:
            assert "mode" in entry
            assert "name" in entry
            assert "sha" in entry


class TestDetachedHead:
    """Tests for detached HEAD state."""

    def test_detached_head_returns_none_for_ref(self, temp_git_repo: Path) -> None:
        """When in detached HEAD, head_ref is None."""
        reader = GitStateReader(temp_git_repo / ".git")

        # Get current HEAD SHA
        snapshot = reader.snapshot()
        head_sha = snapshot.head_commit

        # Detach HEAD
        subprocess.run(
            ["git", "checkout", head_sha],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        detached_snapshot = reader.snapshot()
        assert detached_snapshot.head_ref is None
        assert detached_snapshot.head_commit == head_sha


class TestTagOperations:
    """Tests for tag-related operations."""

    def test_detects_lightweight_tag(self, temp_git_repo: Path) -> None:
        """Creating a tag adds it to snapshot.tags."""
        reader = GitStateReader(temp_git_repo / ".git")

        subprocess.run(
            ["git", "tag", "v1.0"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        snapshot = reader.snapshot()
        assert "v1.0" in snapshot.tags

    def test_tag_points_to_correct_commit(self, temp_git_repo: Path) -> None:
        """Tag SHA matches the commit it was created at."""
        reader = GitStateReader(temp_git_repo / ".git")
        before = reader.snapshot()

        subprocess.run(
            ["git", "tag", "v1.0"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        after = reader.snapshot()
        assert after.tags["v1.0"] == before.head_commit


class TestLooseObjects:
    """Tests for loose object enumeration."""

    def test_list_loose_objects_finds_commits(self, temp_git_repo: Path) -> None:
        """list_loose_objects returns at least the initial commit."""
        reader = GitStateReader(temp_git_repo / ".git")

        objects = reader.list_loose_objects()

        assert len(objects) > 0
        assert all(len(sha) == 40 for sha in objects)

    def test_list_loose_objects_respects_limit(self, temp_git_repo: Path) -> None:
        """list_loose_objects respects the limit parameter."""
        reader = GitStateReader(temp_git_repo / ".git")

        objects = reader.list_loose_objects(limit=1)

        assert len(objects) <= 1


class TestSnapshotDiffing:
    """Tests for comparing snapshots (used for effect extraction)."""

    def test_diff_branches_finds_new(self, temp_git_repo: Path) -> None:
        """diff_branches correctly identifies new branches."""
        reader = GitStateReader(temp_git_repo / ".git")

        before = reader.snapshot()

        subprocess.run(
            ["git", "checkout", "-b", "new-branch"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        after = reader.snapshot()

        new_branches = before.diff_branches(after)
        assert "new-branch" in new_branches
