"""Spike tests for Scope.commit() - full lifecycle validation.

Tests the complete flow:
1. Create workspace
2. Accumulate patches (simulating agent execution)
3. Call scope.commit()
4. Verify real filesystem changed
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import DiffPatch
from shepherd_core.types import ReversibilityLevel
from shepherd_runtime.context import BindableContext
from shepherd_runtime.materialization import MaterializationIntent, MaterializationResult
from shepherd_runtime.scope import Scope

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
def temp_git_repo(tmp_path: Path) -> Path:
    r"""Create a temporary git repository for testing.

    Note: This fixture creates a repo with hello.txt containing "Hello, World!\n"
    which matches the patch in sample_patch.
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
# Tests: WorkspaceRef Materializable Protocol
# =============================================================================


class TestWorkspaceRefMaterializable:
    """Tests for WorkspaceRef implementing Materializable protocol."""

    def test_has_pending_changes_false_initially(self, temp_git_repo: Path) -> None:
        """Fresh workspace should have no pending changes."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        assert workspace.has_pending_changes is False

    def test_has_pending_changes_true_after_patch(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Workspace with accumulated patch should have pending changes."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        workspace_with_patch = workspace.model_copy(update={"pending_patches": (sample_patch,)})
        assert workspace_with_patch.has_pending_changes is True

    def test_materialization_intent_contains_patches(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """materialization_intent() should include pending patches."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        workspace_with_patch = workspace.model_copy(update={"pending_patches": (sample_patch,)})

        intent = workspace_with_patch.materialization_intent()

        assert intent.context_type == "WorkspaceRef"
        assert intent.target_path == Path(temp_git_repo)
        assert len(intent.patches) == 1
        assert intent.patches[0] == sample_patch

    def test_with_materialized_clears_patches(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """with_materialized() should clear pending patches."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        workspace_with_patch = workspace.model_copy(update={"pending_patches": (sample_patch,)})

        result = MaterializationResult.ok(paths_affected=("hello.txt",))
        new_workspace = workspace_with_patch.with_materialized(result)

        assert new_workspace.has_pending_changes is False
        assert len(new_workspace.pending_patches) == 0

    def test_with_materialized_updates_base_commit(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """with_materialized() should update base_commit if commit was made."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        original_commit = workspace.base_commit

        workspace_with_patch = workspace.model_copy(update={"pending_patches": (sample_patch,)})

        result = MaterializationResult.ok(
            paths_affected=("hello.txt",),
            commit_sha="abc123newcommit",
        )
        new_workspace = workspace_with_patch.with_materialized(result)

        assert new_workspace.base_commit == "abc123newcommit"
        assert new_workspace.base_commit != original_commit


# =============================================================================
# Tests: Scope.commit()
# =============================================================================


class TestScopeCommit:
    """Tests for Scope.commit() method."""

    def test_commit_with_no_pending_changes(self, temp_git_repo: Path) -> None:
        """Commit with no pending changes should succeed with empty result."""
        from shepherd_contexts.workspace import WorkspaceRef

        with Scope(root=True) as scope:
            workspace_ref = scope.bind("workspace", WorkspaceRef.from_path(temp_git_repo))

            result = scope.commit()

            assert result["contexts"] == []
            assert result["total_paths_affected"] == 0

    def test_commit_applies_patches_to_filesystem(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Commit should apply patches to the real filesystem."""
        from shepherd_contexts.workspace import WorkspaceRef

        with Scope(root=True) as scope:
            workspace = WorkspaceRef.from_path(temp_git_repo)
            # Simulate accumulated patch (normally happens via effect application)
            workspace_with_patch = workspace.model_copy(update={"pending_patches": (sample_patch,)})
            scope.bind("workspace", workspace_with_patch)

            # Before commit: file unchanged
            content_before = (temp_git_repo / "hello.txt").read_text()
            assert "Goodbye" not in content_before

            result = scope.commit()

            # After commit: file changed
            content_after = (temp_git_repo / "hello.txt").read_text()
            assert "Goodbye, World!" in content_after

            assert len(result["contexts"]) == 1
            assert result["contexts"][0]["name"] == "workspace"
            assert "hello.txt" in result["contexts"][0]["paths_affected"]

    def test_commit_with_message_creates_git_commit(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """Commit with message should create a git commit."""
        from shepherd_contexts.workspace import WorkspaceRef

        with Scope(root=True) as scope:
            workspace = WorkspaceRef.from_path(temp_git_repo)
            workspace_with_patch = workspace.model_copy(update={"pending_patches": (sample_patch,)})
            scope.bind("workspace", workspace_with_patch)

            # Get commit count before
            log_before = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=temp_git_repo,
                capture_output=True,
                text=True,
                check=True,
            )
            count_before = int(log_before.stdout.strip())

            result = scope.commit(message="Apply agent changes")

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
            assert msg_result.stdout.strip() == "Apply agent changes"

            # Result includes commit SHA
            assert "commit_sha" in result["contexts"][0]["metadata"]

    def test_commit_clears_pending_patches_in_scope(self, temp_git_repo: Path, sample_patch: DiffPatch) -> None:
        """After commit, workspace in scope should have no pending patches."""
        from shepherd_contexts.workspace import WorkspaceRef

        with Scope(root=True) as scope:
            workspace = WorkspaceRef.from_path(temp_git_repo)
            workspace_with_patch = workspace.model_copy(update={"pending_patches": (sample_patch,)})
            workspace_ref = scope.bind("workspace", workspace_with_patch)

            # Before commit
            assert workspace_ref.has_pending_changes is True

            scope.commit()

            # After commit - context in scope is updated
            assert workspace_ref.has_pending_changes is False

    def test_commit_fails_on_child_scope(self, temp_git_repo: Path) -> None:
        """Commit should fail if called on a child scope."""
        from shepherd_contexts.workspace import WorkspaceRef

        with Scope(root=True) as root:
            root.bind("workspace", WorkspaceRef.from_path(temp_git_repo))

            child = root.child()

            with pytest.raises(RuntimeError, match="root scope"):
                child.commit()

    def test_commit_without_materializer_fails(self, temp_git_repo: Path) -> None:
        """Commit should fail if context has no registered materializer."""

        @dataclass(frozen=True)
        class UnregisteredContext(BindableContext):
            @property
            def context_id(self) -> str:
                return "unregistered"

            @property
            def reversibility(self) -> ReversibilityLevel:
                return ReversibilityLevel.AUTO

            @property
            def has_pending_changes(self) -> bool:
                return True

            def materialization_intent(self) -> MaterializationIntent:
                return MaterializationIntent(
                    context_type="UnregisteredContext",
                    context_id="unregistered",
                    target_path=Path("/tmp"),
                )

            def with_materialized(self, result: MaterializationResult) -> "UnregisteredContext":
                """Required by Materializable protocol."""
                return self

        with Scope(root=True) as scope:
            scope.bind("ctx", UnregisteredContext())

            with pytest.raises(RuntimeError, match="No materializer registered"):
                scope.commit()


# =============================================================================
# Tests: Full Lifecycle Simulation
# =============================================================================


class TestFullLifecycleSimulation:
    """Tests simulating full agent execution lifecycle."""

    def test_simulate_agent_modifies_file_then_commit(self, temp_git_repo: Path) -> None:
        """Simulate: Agent runs -> effect captured -> commit applies changes."""
        from shepherd_contexts.workspace import WorkspaceRef

        # Create patch that adds a new function to a file
        patch_content = """\
diff --git a/hello.txt b/hello.txt
index 8ab686e..3b18e51 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1,5 @@
 Hello, World!
+
+def greet(name):
+    return f"Hello, {name}!"
+
"""
        patch = DiffPatch.from_diff(patch_content, ["hello.txt"], "fix_auth_task")

        with Scope(root=True) as scope:
            # 1. Bind workspace
            workspace = WorkspaceRef.from_path(temp_git_repo)
            workspace_ref = scope.bind("workspace", workspace)

            # 2. Simulate effect from agent execution
            effect = WorkspacePatchCaptured(
                context_id=workspace.context_id,
                files_changed=("hello.txt",),
                patch_hash=patch.sha256 or "",
                patch_size_bytes=len(patch.patch),
                patch=patch,
            )
            scope.emit(effect)

            # 3. Verify pending changes accumulated
            assert workspace_ref.has_pending_changes is True
            assert len(workspace_ref.pending_patches) == 1

            # 4. Commit to real filesystem
            result = scope.commit(message="Add greet function")

            # 5. Verify filesystem changed
            content = (temp_git_repo / "hello.txt").read_text()
            assert "def greet(name):" in content
            assert "Hello, {name}!" in content

            # 6. Verify workspace state cleared
            assert workspace_ref.has_pending_changes is False

            # 7. Verify git commit made
            log_result = subprocess.run(
                ["git", "log", "-1", "--format=%s"],
                cwd=temp_git_repo,
                capture_output=True,
                text=True,
                check=True,
            )
            assert log_result.stdout.strip() == "Add greet function"

    def test_multiple_patches_accumulated_then_committed(self, temp_git_repo: Path) -> None:
        """Multiple patches from multiple task executions, then single commit."""
        from shepherd_contexts.workspace import WorkspaceRef

        # First patch: modify hello.txt
        patch1_content = """\
diff --git a/hello.txt b/hello.txt
index 8ab686e..cd5b157 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1,2 @@
 Hello, World!
+Line from patch 1
"""
        patch1 = DiffPatch.from_diff(patch1_content, ["hello.txt"], "task1")

        # Second patch: add more to hello.txt (applied on top of first)
        patch2_content = """\
diff --git a/hello.txt b/hello.txt
index cd5b157..abc1234 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1,2 +1,3 @@
 Hello, World!
 Line from patch 1
+Line from patch 2
"""
        patch2 = DiffPatch.from_diff(patch2_content, ["hello.txt"], "task2")

        with Scope(root=True) as scope:
            workspace = WorkspaceRef.from_path(temp_git_repo)
            workspace_ref = scope.bind("workspace", workspace)

            # Emit first effect (simulating first task)
            scope.emit(
                WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    files_changed=("hello.txt",),
                    patch_hash=patch1.sha256 or "",
                    patch_size_bytes=len(patch1.patch),
                    patch=patch1,
                )
            )

            # Emit second effect (simulating second task)
            scope.emit(
                WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    files_changed=("hello.txt",),
                    patch_hash=patch2.sha256 or "",
                    patch_size_bytes=len(patch2.patch),
                    patch=patch2,
                )
            )

            # Verify both patches accumulated
            assert len(workspace_ref.pending_patches) == 2

            # Commit all at once
            result = scope.commit(message="Apply both patches")

            # Both patches applied
            content = (temp_git_repo / "hello.txt").read_text()
            assert "Line from patch 1" in content
            assert "Line from patch 2" in content

            # Note: paths are deduplicated, so even though both patches touch
            # hello.txt, it only counts once in the final result
            assert result["total_paths_affected"] == 1
            assert "hello.txt" in result["contexts"][0]["paths_affected"]
