"""Spike tests for materialization flow.

Validates the Intent -> Materializer -> Result pattern works end-to-end.
"""

import subprocess
from pathlib import Path

import pytest
from shepherd_contexts.workspace.materializer import (
    WorkspaceMaterializationIntent,
    WorkspaceMaterializer,
)
from shepherd_core.effects import DiffPatch
from shepherd_runtime.materialization import (
    MaterializationResult,
    get_materializer,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_patch() -> DiffPatch:
    """Create a sample patch that modifies hello.txt."""
    patch_content = """\
diff --git a/hello.txt b/hello.txt
index 8ab686e..cd5b157 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1,2 @@
 Hello, World!
+Goodbye, World!
"""
    return DiffPatch.from_diff(patch_content, ["hello.txt"])


@pytest.fixture
def new_file_patch() -> DiffPatch:
    """Create a patch that adds a new file."""
    patch_content = """\
diff --git a/new_file.txt b/new_file.txt
new file mode 100644
index 0000000..5dd01c1
--- /dev/null
+++ b/new_file.txt
@@ -0,0 +1 @@
+This is a new file!
"""
    return DiffPatch.from_diff(patch_content, ["new_file.txt"])


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    r"""Create a temporary git repository for testing.

    Note: This fixture creates a repo with hello.txt containing "Hello, World!\n"
    which matches the patches in sample_patch and new_file_patch.
    """
    repo_path = tmp_path / "test-repo"
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
    (repo_path / "hello.txt").write_text("Hello, World!\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True, timeout=30)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )

    return repo_path


# =============================================================================
# Tests: Intent Creation
# =============================================================================


class TestWorkspaceMaterializationIntent:
    """Tests for WorkspaceMaterializationIntent creation and manipulation."""

    def test_create_intent_with_patches(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Intent should hold patches and target path."""
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch,),
        )

        assert intent.context_type == "WorkspaceRef"
        assert intent.context_id == "workspace:test"
        assert intent.target_path == temp_git_repo
        assert len(intent.patches) == 1
        assert intent.commit_message is None

    def test_with_commit_message(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """with_commit_message should return new intent with message."""
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch,),
        )

        intent_with_msg = intent.with_commit_message("Test commit")

        # Original unchanged (immutable)
        assert intent.commit_message is None
        # New intent has message
        assert intent_with_msg.commit_message == "Test commit"
        # Other fields preserved
        assert intent_with_msg.patches == intent.patches
        assert intent_with_msg.target_path == intent.target_path


# =============================================================================
# Tests: Materializer
# =============================================================================


class TestWorkspaceMaterializer:
    """Tests for WorkspaceMaterializer execution."""

    def test_materialize_empty_patches_succeeds(self, temp_git_repo: Path) -> None:
        """Materializing empty patches should succeed immediately."""
        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(),
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert result.paths_affected == ()

    def test_materialize_applies_patch(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Materializing should apply patch to filesystem."""
        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch,),
        )

        # Before: only one line
        content_before = (temp_git_repo / "hello.txt").read_text()
        assert "Goodbye" not in content_before

        result = materializer.materialize(intent)

        # Materialization succeeded
        assert result.success is True
        assert "hello.txt" in result.paths_affected

        # File was modified
        content_after = (temp_git_repo / "hello.txt").read_text()
        assert "Goodbye, World!" in content_after

        # No commit made (no commit message)
        assert result.metadata.get("committed") == "false"

    def test_materialize_creates_new_file(self, temp_git_repo: Path, new_file_patch: DiffPatch) -> None:
        """Materializing should create new files."""
        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(new_file_patch,),
        )

        # Before: file doesn't exist
        assert not (temp_git_repo / "new_file.txt").exists()

        result = materializer.materialize(intent)

        assert result.success is True
        assert "new_file.txt" in result.paths_affected

        # File was created
        assert (temp_git_repo / "new_file.txt").exists()
        content = (temp_git_repo / "new_file.txt").read_text()
        assert "This is a new file!" in content

    def test_materialize_with_commit_message(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Materializing with commit message should create git commit."""
        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch,),
            commit_message="Apply test changes",
        )

        # Get commit count before
        log_before = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        count_before = int(log_before.stdout.strip())

        result = materializer.materialize(intent)

        assert result.success is True
        assert result.metadata.get("committed") == "true"
        assert "commit_sha" in result.metadata

        # Commit count increased
        log_after = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        count_after = int(log_after.stdout.strip())
        assert count_after == count_before + 1

        # Commit message is correct
        msg_result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert msg_result.stdout.strip() == "Apply test changes"

    def test_materialize_multiple_patches(
        self, temp_git_repo: Path, sample_patch: DiffPatch, new_file_patch: DiffPatch
    ) -> None:
        """Materializing should apply multiple patches in order."""
        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch, new_file_patch),
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert "hello.txt" in result.paths_affected
        assert "new_file.txt" in result.paths_affected

        # Both changes applied
        assert "Goodbye, World!" in (temp_git_repo / "hello.txt").read_text()
        assert (temp_git_repo / "new_file.txt").exists()

    def test_materialize_invalid_patch_fails(self, temp_git_repo: Path) -> None:
        """Materializing invalid patch should return failure result."""
        materializer = WorkspaceMaterializer()
        bad_patch = DiffPatch.from_diff("this is not a valid patch", ["nonexistent.txt"])
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(bad_patch,),
        )

        result = materializer.materialize(intent)

        assert result.success is False
        assert result.error is not None
        assert "git apply failed" in result.error


# =============================================================================
# Tests: Rollback
# =============================================================================


class TestWorkspaceMaterializerRollback:
    """Tests for rollback functionality."""

    def test_can_rollback_returns_true(self) -> None:
        """WorkspaceMaterializer should support rollback."""
        materializer = WorkspaceMaterializer()
        assert materializer.can_rollback() is True

    def test_rollback_uncommitted_changes(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Rollback should undo uncommitted changes."""
        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch,),
        )

        # Apply without commit
        result = materializer.materialize(intent)
        assert result.success is True
        assert "Goodbye" in (temp_git_repo / "hello.txt").read_text()

        # Rollback
        materializer.rollback(intent, result)

        # Changes are undone
        content = (temp_git_repo / "hello.txt").read_text()
        assert "Goodbye" not in content
        assert content.strip() == "Hello, World!"

    def test_rollback_committed_changes(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Rollback should undo committed changes."""
        materializer = WorkspaceMaterializer()
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch,),
            commit_message="Test commit to rollback",
        )

        # Get original HEAD
        original_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # Apply with commit
        result = materializer.materialize(intent)
        assert result.success is True
        assert result.metadata.get("committed") == "true"

        # HEAD changed
        new_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert new_head != original_head

        # Rollback
        materializer.rollback(intent, result)

        # HEAD is back to original
        restored_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert restored_head == original_head

        # File content restored
        content = (temp_git_repo / "hello.txt").read_text()
        assert "Goodbye" not in content


# =============================================================================
# Tests: Registry Integration
# =============================================================================


class TestMaterializerRegistry:
    """Tests for materializer registry integration."""

    def test_materializer_registered_on_import(self) -> None:
        """WorkspaceMaterializer should be registered when module is imported."""
        # Import triggers registration

        materializer = get_materializer("WorkspaceRef")
        assert materializer is not None
        assert isinstance(materializer, WorkspaceMaterializer)

    def test_end_to_end_via_registry(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Full flow through registry should work."""
        # Import to ensure registration

        # Create intent
        intent = WorkspaceMaterializationIntent(
            context_id="workspace:test",
            target_path=temp_git_repo,
            patches=(sample_patch,),
        )

        # Look up materializer
        materializer = get_materializer("WorkspaceRef")
        assert materializer is not None

        # Execute
        result = materializer.materialize(intent)

        assert result.success is True
        assert "Goodbye, World!" in (temp_git_repo / "hello.txt").read_text()


# =============================================================================
# Tests: Result Type
# =============================================================================


class TestMaterializationResult:
    """Tests for MaterializationResult helpers."""

    def test_ok_creates_success_result(self) -> None:
        """MaterializationResult.ok() should create success result."""
        result = MaterializationResult.ok(
            paths_affected=("a.txt", "b.txt"),
            commit_sha="abc123",
        )

        assert result.success is True
        assert result.paths_affected == ("a.txt", "b.txt")
        assert result.error is None
        assert result.metadata["commit_sha"] == "abc123"

    def test_failure_creates_failed_result(self) -> None:
        """MaterializationResult.failure() should create failed result."""
        result = MaterializationResult.failure("Something went wrong")

        assert result.success is False
        assert result.error == "Something went wrong"
        assert result.paths_affected == ()
