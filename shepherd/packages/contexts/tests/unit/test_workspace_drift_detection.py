"""Unit tests for drift detection in WorkspaceMaterializer.

Drift detection ensures that the repository HEAD hasn't changed
between when patches were created and when they are materialized.

Tests:
- No drift allows materialization to proceed
- Drift (HEAD changed) detected and fails
- Drift detection handles git errors gracefully
- No drift check when expected_base_commit is None
"""

import subprocess
from pathlib import Path

import pytest
from shepherd_core.effects import DiffPatch

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with an initial commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True, timeout=30)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )

    # Create initial file and commit
    (repo_path / "existing.txt").write_text("original content\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True, timeout=30)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )

    return repo_path


@pytest.fixture
def initial_commit(git_repo: Path) -> str:
    """Get the initial commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


@pytest.fixture
def sample_patch() -> DiffPatch:
    """A sample patch that modifies existing.txt."""
    return DiffPatch(
        patch="""diff --git a/existing.txt b/existing.txt
index 1234567..abcdefg 100644
--- a/existing.txt
+++ b/existing.txt
@@ -1 +1 @@
-original content
+modified content
""",
        files_changed=("existing.txt",),
    )


# =============================================================================
# Tests: _get_head_commit() Method
# =============================================================================


class TestGetHeadCommit:
    """Tests for WorkspaceMaterializer._get_head_commit()."""

    def test_returns_commit_sha(self, git_repo: Path, initial_commit: str) -> None:
        """Should return the current HEAD commit SHA."""
        from shepherd_contexts.workspace.materializer import WorkspaceMaterializer

        materializer = WorkspaceMaterializer()
        head = materializer._get_head_commit(git_repo)

        assert head is not None
        assert head == initial_commit

    def test_returns_none_for_non_git_directory(self, tmp_path: Path) -> None:
        """Should return None for a non-git directory."""
        from shepherd_contexts.workspace.materializer import WorkspaceMaterializer

        materializer = WorkspaceMaterializer()
        head = materializer._get_head_commit(tmp_path)

        assert head is None

    def test_returns_none_for_nonexistent_path(self, tmp_path: Path) -> None:
        """Should return None for a nonexistent path."""
        from shepherd_contexts.workspace.materializer import WorkspaceMaterializer

        materializer = WorkspaceMaterializer()
        head = materializer._get_head_commit(tmp_path / "nonexistent")

        assert head is None


# =============================================================================
# Tests: _check_drift() Method
# =============================================================================


class TestCheckDriftMethod:
    """Tests for WorkspaceMaterializer._check_drift()."""

    def test_no_drift_returns_none(self, git_repo: Path, initial_commit: str) -> None:
        """When HEAD matches expected, _check_drift returns None."""
        from shepherd_contexts.workspace.materializer import WorkspaceMaterializer

        materializer = WorkspaceMaterializer()
        result = materializer._check_drift(git_repo, initial_commit)

        assert result is None

    def test_no_drift_with_short_sha(self, git_repo: Path, initial_commit: str) -> None:
        """Should work with short SHA (8 chars) as expected commit."""
        from shepherd_contexts.workspace.materializer import WorkspaceMaterializer

        materializer = WorkspaceMaterializer()
        short_sha = initial_commit[:8]
        result = materializer._check_drift(git_repo, short_sha)

        assert result is None

    def test_drift_detected_when_head_differs(self, git_repo: Path, initial_commit: str) -> None:
        """When HEAD changed, _check_drift returns error message."""
        from shepherd_contexts.workspace.materializer import WorkspaceMaterializer

        # Create a new commit to change HEAD
        (git_repo / "new_file.txt").write_text("new content\n")
        subprocess.run(["git", "add", "."], check=False, cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "New commit"],
            check=False,
            cwd=git_repo,
            capture_output=True,
        )

        materializer = WorkspaceMaterializer()
        result = materializer._check_drift(git_repo, initial_commit)

        assert result is not None
        assert "Drift detected" in result
        assert "modified externally" in result

    def test_drift_detection_fails_gracefully_for_non_git(self, tmp_path: Path) -> None:
        """Should return error for non-git directory."""
        from shepherd_contexts.workspace.materializer import WorkspaceMaterializer

        materializer = WorkspaceMaterializer()
        result = materializer._check_drift(tmp_path, "some_commit")

        assert result is not None
        assert "Drift detection failed" in result


# =============================================================================
# Tests: Drift Detection in Materialization
# =============================================================================


class TestDriftDetectionInMaterialize:
    """Tests for drift detection during materialize()."""

    def test_no_drift_allows_materialization(
        self, git_repo: Path, initial_commit: str, sample_patch: DiffPatch
    ) -> None:
        """When no drift, materialization should succeed."""
        from shepherd_contexts.workspace.materializer import (
            WorkspaceMaterializationIntent,
            WorkspaceMaterializer,
        )

        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=git_repo,
            patches=(sample_patch,),
            expected_base_commit=initial_commit,
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert (git_repo / "existing.txt").read_text() == "modified content\n"

    def test_drift_detected_fails_materialization(
        self, git_repo: Path, initial_commit: str, sample_patch: DiffPatch
    ) -> None:
        """When drift detected, materialization should fail."""
        from shepherd_contexts.workspace.materializer import (
            WorkspaceMaterializationIntent,
            WorkspaceMaterializer,
        )

        # Create a new commit to change HEAD
        (git_repo / "other.txt").write_text("other content\n")
        subprocess.run(["git", "add", "."], check=False, cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "External commit"],
            check=False,
            cwd=git_repo,
            capture_output=True,
        )

        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=git_repo,
            patches=(sample_patch,),
            expected_base_commit=initial_commit,
        )

        result = materializer.materialize(intent)

        assert result.success is False
        assert result.error is not None
        assert "Drift detected" in result.error

    def test_no_drift_check_when_expected_commit_none(
        self, git_repo: Path, initial_commit: str, sample_patch: DiffPatch
    ) -> None:
        """When expected_base_commit is None, skip drift check."""
        from shepherd_contexts.workspace.materializer import (
            WorkspaceMaterializationIntent,
            WorkspaceMaterializer,
        )

        # Create a new commit - normally would trigger drift
        (git_repo / "other.txt").write_text("other content\n")
        subprocess.run(["git", "add", "."], check=False, cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "External commit"],
            check=False,
            cwd=git_repo,
            capture_output=True,
        )

        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=git_repo,
            patches=(sample_patch,),
            expected_base_commit=None,  # No drift check
        )

        result = materializer.materialize(intent)

        # Should succeed because no drift check performed
        assert result.success is True

    def test_empty_patches_skips_drift_check(self, git_repo: Path, initial_commit: str) -> None:
        """When no patches, should return ok without drift check."""
        from shepherd_contexts.workspace.materializer import (
            WorkspaceMaterializationIntent,
            WorkspaceMaterializer,
        )

        # Create a new commit
        (git_repo / "other.txt").write_text("other content\n")
        subprocess.run(["git", "add", "."], check=False, cwd=git_repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "External commit"],
            check=False,
            cwd=git_repo,
            capture_output=True,
        )

        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=git_repo,
            patches=(),  # Empty
            expected_base_commit=initial_commit,
        )

        result = materializer.materialize(intent)

        # Should succeed - no patches means early return
        assert result.success is True


# =============================================================================
# Tests: WorkspaceRef Integration
# =============================================================================


class TestWorkspaceRefIntegration:
    """Tests for WorkspaceRef.materialization_intent() with drift detection."""

    def test_materialization_intent_includes_base_commit(self, git_repo: Path, initial_commit: str) -> None:
        """materialization_intent should include expected_base_commit."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(git_repo)

        intent = workspace.materialization_intent()

        assert intent.expected_base_commit is not None
        assert intent.expected_base_commit == initial_commit

    def test_intent_uses_current_base_commit(self, git_repo: Path) -> None:
        """Intent should use the workspace's current base_commit field."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(git_repo)

        # Manually set a different base_commit (must be valid 40-char SHA)
        custom_commit = "b" * 40
        workspace_modified = workspace.model_copy(update={"base_commit": custom_commit})

        intent = workspace_modified.materialization_intent()

        assert intent.expected_base_commit == custom_commit
