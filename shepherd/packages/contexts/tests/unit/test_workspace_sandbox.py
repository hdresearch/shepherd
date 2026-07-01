"""Tests for WorkspaceRef sandbox integration.

Covers:
- WorkspaceRef.requires_sandbox() returns True
- WorkspaceRef sandbox factory is registered on import
- Factory creates GitWorktreeSandbox with correct parameters
- Integration with _create_sandbox_for_context()
"""

import subprocess
from pathlib import Path

import pytest
from shepherd_runtime.context.sandbox import GITPYTHON_AVAILABLE, GitWorktreeSandbox
from shepherd_tests.runtime import create_sandbox_for_context, sandbox_factories

pytestmark = pytest.mark.skipif(not GITPYTHON_AVAILABLE, reason="GitPython not installed")

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository for testing."""
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

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo\n")
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
# Tests: WorkspaceRef.requires_sandbox()
# =============================================================================


class TestWorkspaceRefRequiresSandbox:
    """Tests for WorkspaceRef.requires_sandbox() method."""

    def test_requires_sandbox_returns_true(self) -> None:
        """WorkspaceRef.requires_sandbox() should return True."""
        from shepherd_contexts.workspace import WorkspaceRef

        assert WorkspaceRef.requires_sandbox() is True

    def test_requires_sandbox_is_classmethod(self) -> None:
        """requires_sandbox() should be callable on class, not just instance."""
        from shepherd_contexts.workspace import WorkspaceRef

        # Should work on class
        assert WorkspaceRef.requires_sandbox() is True

        # Should also work on instance (classmethod behavior)
        # Note: We don't create a real instance here to avoid needing a git repo


# =============================================================================
# Tests: Factory Registration
# =============================================================================


class TestWorkspaceRefFactoryRegistration:
    """Tests for WorkspaceRef sandbox factory registration."""

    def test_factory_registered_on_import(self) -> None:
        """WorkspaceRef factory should be registered when module is imported."""
        # Import the module (factory registration happens at import time)

        assert "WorkspaceRef" in sandbox_factories

    def test_factory_returns_git_worktree_sandbox(self, temp_git_repo: Path) -> None:
        """Factory should return a GitWorktreeSandbox instance."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        factory = sandbox_factories.get("WorkspaceRef")

        assert factory is not None

        sandbox = factory(workspace)
        assert isinstance(sandbox, GitWorktreeSandbox)

    def test_factory_passes_correct_parameters(self, temp_git_repo: Path) -> None:
        """Factory should pass path, base_commit, and pending_patches to sandbox."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        factory = sandbox_factories.get("WorkspaceRef")
        assert factory is not None

        sandbox = factory(workspace)

        assert sandbox.source_repo == workspace.path
        assert sandbox.base_commit == workspace.base_commit
        assert sandbox.pending_patches == workspace.pending_patches


# =============================================================================
# Tests: Integration with _create_sandbox_for_context()
# =============================================================================


class TestWorkspaceRefCreateSandbox:
    """Tests for _create_sandbox_for_context() with WorkspaceRef."""

    def test_create_sandbox_returns_git_worktree_sandbox(self, temp_git_repo: Path) -> None:
        """_create_sandbox_for_context() should return GitWorktreeSandbox for WorkspaceRef."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        sandbox = create_sandbox_for_context(workspace)

        assert sandbox is not None
        assert isinstance(sandbox, GitWorktreeSandbox)

    def test_create_sandbox_no_warning_for_workspace_ref(self, temp_git_repo: Path) -> None:
        """No warning should be emitted since factory is registered."""
        import warnings

        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sandbox = create_sandbox_for_context(workspace)

            assert sandbox is not None
            # Filter for sandbox-related warnings only
            sandbox_warnings = [x for x in w if "requires_sandbox" in str(x.message)]
            assert len(sandbox_warnings) == 0


# =============================================================================
# Tests: Sandbox Lifecycle
# =============================================================================


class TestWorkspaceRefSandboxLifecycle:
    """Tests for full sandbox lifecycle with WorkspaceRef."""

    def test_sandbox_setup_and_discard(self, temp_git_repo: Path) -> None:
        """Sandbox should set up and discard cleanly."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        sandbox = create_sandbox_for_context(workspace)

        assert sandbox is not None
        assert isinstance(sandbox, GitWorktreeSandbox)

        # Setup sandbox
        sandbox.setup(workspace)
        assert sandbox.path.exists()
        assert sandbox.path != Path(workspace.path)

        # Verify it's a worktree
        assert (sandbox.path / "README.md").exists()

        # Discard
        worktree_path = sandbox.path
        sandbox.discard()
        assert not worktree_path.exists()

    def test_sandbox_captures_changes(self, temp_git_repo: Path) -> None:
        """Sandbox should capture file changes as git diff."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)
        sandbox = create_sandbox_for_context(workspace)

        assert sandbox is not None
        sandbox.setup(workspace)

        try:
            # Make a change in the sandbox
            test_file = sandbox.path / "new_file.txt"
            test_file.write_text("Hello, world!\n")

            # Capture changes
            diff = sandbox.git_diff()
            changed = sandbox.changed_files()

            assert "new_file.txt" in diff
            assert "new_file.txt" in changed
        finally:
            sandbox.discard()

    def test_sandbox_context_manager(self, temp_git_repo: Path) -> None:
        """GitWorktreeSandbox should work as context manager."""
        from shepherd_contexts.workspace import WorkspaceRef

        workspace = WorkspaceRef.from_path(temp_git_repo)

        with GitWorktreeSandbox(
            source_repo=workspace.path,
            base_commit=workspace.base_commit,
        ) as sandbox:
            assert sandbox.path.exists()
            worktree_path = sandbox.path

        # After exit, worktree should be cleaned up
        assert not worktree_path.exists()
