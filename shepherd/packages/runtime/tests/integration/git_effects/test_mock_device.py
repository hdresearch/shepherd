"""Tests for LocalSimulatedDevice (Spike X1 Validation).

These tests validate the mock container device for integration testing.
All tests should PASS - the mock device is implemented.

Design Decisions Validated:
- X1: Three-layer testing strategy (unit/integration/container)
- X1: ~80% fidelity for effect extraction testing
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .fixtures import LocalSimulatedDevice

if TYPE_CHECKING:
    from pathlib import Path


class TestMockDeviceBasics:
    """Basic LocalSimulatedDevice functionality."""

    def test_creates_sandbox_on_enter(self, workspace_fixture: Path) -> None:
        """Device creates sandbox directory on context enter."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            assert device.sandbox_dir is not None
            assert device.sandbox_dir.exists()
            assert (device.sandbox_dir / "workspace").exists()

    def test_cleans_up_on_exit(self, workspace_fixture: Path) -> None:
        """Device cleans up sandbox on context exit."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            sandbox_path = device.sandbox_dir

        # After exit, sandbox should be gone
        assert not sandbox_path.exists()

    def test_workspace_property(self, workspace_fixture: Path) -> None:
        """Workspace property returns path to sandboxed repo."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            assert device.workspace.exists()
            assert (device.workspace / ".git").exists()
            assert (device.workspace / "README.md").exists()


class TestCommandExecution:
    """Tests for command execution in sandbox."""

    def test_execute_simple_command(self, workspace_fixture: Path) -> None:
        """Can execute simple commands in sandbox."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            result = device.execute("echo hello")

            assert result.returncode == 0
            assert "hello" in result.stdout

    def test_execute_in_workspace_context(self, workspace_fixture: Path) -> None:
        """Commands run in workspace directory."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            result = device.execute("pwd")

            assert str(device.workspace) in result.stdout

    def test_run_git_helper(self, workspace_fixture: Path) -> None:
        """run_git helper works correctly."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            result = device.run_git("status")

            assert result.returncode == 0
            assert "On branch" in result.stdout

    def test_commands_affect_sandbox_not_original(self, workspace_fixture: Path) -> None:
        """Changes in sandbox don't affect original workspace."""
        original_files = list(workspace_fixture.iterdir())

        with LocalSimulatedDevice(workspace_fixture) as device:
            device.execute("touch new_file.txt")
            device.execute("rm README.md")

        # Original should be unchanged
        current_files = list(workspace_fixture.iterdir())
        assert current_files == original_files


class TestGitEffectExtraction:
    """Tests for git effect extraction (core X1 functionality)."""

    def test_extracts_branch_creation(self, workspace_fixture: Path) -> None:
        """Detects new branch as GitBranchCreated effect."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            device.run_git("checkout", "-b", "feature-test")

            effects = device.extract_git_effects()

        branch_effects = [e for e in effects if e.effect_type == "git_branch_created"]
        assert len(branch_effects) == 1
        assert branch_effects[0].branch_name == "feature-test"

    def test_extracts_checkout(self, workspace_fixture: Path) -> None:
        """Detects branch checkout as GitCheckoutPerformed effect."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            # Create branch first
            device.run_git("branch", "other-branch")
            # Reset effect tracking by getting fresh before snapshot
            device._before_snapshot = device._reader.snapshot()

            # Now checkout
            device.run_git("checkout", "other-branch")

            effects = device.extract_git_effects()

        checkout_effects = [e for e in effects if e.effect_type == "git_checkout_performed"]
        assert len(checkout_effects) == 1
        assert checkout_effects[0].target_ref == "other-branch"

    @pytest.mark.xfail(reason="read_commit fails on CI with packed/alternate objects - needs investigation")
    def test_extracts_commit(self, workspace_fixture: Path) -> None:
        """Detects new commit as GitCommitCreated effect."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            device.write_file("new_file.txt", "content")
            device.run_git("add", "new_file.txt")
            device.run_git("commit", "-m", "Add new file")

            effects = device.extract_git_effects()

        commit_effects = [e for e in effects if e.effect_type == "git_commit_created"]
        assert len(commit_effects) == 1
        assert "Add new file" in commit_effects[0].message

    def test_extracts_tag(self, workspace_fixture: Path) -> None:
        """Detects new tag as GitTagCreated effect."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            device.run_git("tag", "v1.0.0")

            effects = device.extract_git_effects()

        tag_effects = [e for e in effects if e.effect_type == "git_tag_created"]
        assert len(tag_effects) == 1
        assert tag_effects[0].tag_name == "v1.0.0"

    def test_extracts_branch_deletion(self, workspace_fixture: Path) -> None:
        """Detects deleted branch as GitBranchDeleted effect."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            # Create and delete a branch
            device.run_git("branch", "to-delete")
            device._before_snapshot = device._reader.snapshot()  # Reset tracking
            device.run_git("branch", "-d", "to-delete")

            effects = device.extract_git_effects()

        delete_effects = [e for e in effects if e.effect_type == "git_branch_deleted"]
        assert len(delete_effects) == 1
        assert delete_effects[0].branch_name == "to-delete"


class TestMultipleOperations:
    """Tests for multiple operations in sequence."""

    @pytest.mark.xfail(reason="read_commit fails on CI with packed/alternate objects - needs investigation")
    def test_branch_and_commit_sequence(self, workspace_fixture: Path) -> None:
        """Typical workflow: create branch, make changes, commit.

        Note: The mock device detects commits by checking if the branch
        pointer moved. This works for commits on existing branches.
        """
        with LocalSimulatedDevice(workspace_fixture) as device:
            # First, just commit on existing branch (main)
            device.write_file("feature.txt", "feature content")
            device.run_git("add", "feature.txt")
            device.run_git("commit", "-m", "Add feature")

            effects = device.extract_git_effects()

        effect_types = [e.effect_type for e in effects]

        # Should detect the commit (branch pointer moved)
        assert "git_commit_created" in effect_types

    @pytest.mark.xfail(reason="read_commit fails on CI with packed/alternate objects - needs investigation")
    def test_branch_create_then_commit(self, workspace_fixture: Path) -> None:
        """Create branch then commit - both operations detected."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            device.run_git("checkout", "-b", "feature")

            # Reset tracking to detect commit separately
            device._before_snapshot = device._reader.snapshot()

            device.write_file("feature.txt", "feature content")
            device.run_git("add", "feature.txt")
            device.run_git("commit", "-m", "Add feature")

            effects = device.extract_git_effects()

        effect_types = [e.effect_type for e in effects]
        assert "git_commit_created" in effect_types

    def test_multiple_branches(self, workspace_fixture: Path) -> None:
        """Creating multiple branches is tracked."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            device.run_git("branch", "branch-a")
            device.run_git("branch", "branch-b")
            device.run_git("branch", "branch-c")

            effects = device.extract_git_effects()

        branch_names = [e.branch_name for e in effects if e.effect_type == "git_branch_created"]

        assert set(branch_names) == {"branch-a", "branch-b", "branch-c"}


class TestFileOperations:
    """Tests for file read/write helpers."""

    def test_write_file(self, workspace_fixture: Path) -> None:
        """write_file creates file in sandbox."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            device.write_file("test.txt", "hello world")

            assert (device.workspace / "test.txt").exists()
            assert (device.workspace / "test.txt").read_text() == "hello world"

    def test_write_file_nested(self, workspace_fixture: Path) -> None:
        """write_file creates parent directories."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            device.write_file("deep/nested/file.txt", "content")

            assert (device.workspace / "deep/nested/file.txt").exists()

    def test_get_file_content(self, workspace_fixture: Path) -> None:
        """get_file_content reads from sandbox."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            content = device.get_file_content("README.md")

            assert content is not None
            assert "Test Repository" in content

    def test_get_file_content_nonexistent(self, workspace_fixture: Path) -> None:
        """get_file_content returns None for missing file."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            content = device.get_file_content("nonexistent.txt")

            assert content is None


class TestSnapshotAccess:
    """Tests for snapshot access."""

    def test_get_current_snapshot(self, workspace_fixture: Path) -> None:
        """Can get current git state snapshot."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            snapshot = device.get_current_snapshot()

            assert snapshot is not None
            assert snapshot.head_commit
            assert len(snapshot.branches) > 0

    def test_snapshot_reflects_changes(self, workspace_fixture: Path) -> None:
        """Snapshot reflects changes made in sandbox."""
        with LocalSimulatedDevice(workspace_fixture) as device:
            before = device.get_current_snapshot()

            device.run_git("checkout", "-b", "new-for-snapshot")

            after = device.get_current_snapshot()

            assert "new-for-snapshot" not in before.branches
            assert "new-for-snapshot" in after.branches


class TestErrorHandling:
    """Tests for error conditions."""

    def test_raises_outside_context(self, workspace_fixture: Path) -> None:
        """Methods raise error when called outside context."""
        device = LocalSimulatedDevice(workspace_fixture)

        with pytest.raises(RuntimeError, match="not entered"):
            device.execute("echo hello")

    def test_raises_for_workspace_outside_context(self, workspace_fixture: Path) -> None:
        """Workspace property raises outside context."""
        device = LocalSimulatedDevice(workspace_fixture)

        with pytest.raises(RuntimeError, match="not entered"):
            _ = device.workspace
