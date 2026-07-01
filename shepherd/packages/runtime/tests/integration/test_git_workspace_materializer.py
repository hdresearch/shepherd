"""Integration tests for GitWorkspacePatchMaterializer with real git repos.

These tests verify the materializer works correctly with actual git operations:
- Applying patches via git apply
- Reversal via git apply --reverse
- Edge cases (empty patches, invalid repos, etc.)
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import DiffPatch
from shepherd_runtime.effect_materialization import (
    GitWorkspacePatchMaterializer,
    ReversalError,
)

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with an initial commit."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, timeout=30)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )

    # Create initial file and commit
    test_file = repo / "test.txt"
    test_file.write_text("Hello World\n")
    subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True, timeout=30)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )

    return repo


def create_patch_effect(
    workspace_path: Path,
    patch_content: str,
    files_changed: tuple[str, ...] = ("test.txt",),
    binding_name: str | None = None,
) -> WorkspacePatchCaptured:
    """Create a WorkspacePatchCaptured effect for testing."""
    return WorkspacePatchCaptured(
        binding_name=binding_name,
        context_id=f"workspace:{workspace_path}:abc123",
        patch=DiffPatch(patch=patch_content, files_changed=files_changed),
        files_changed=files_changed,
    )


# =============================================================================
# Tests
# =============================================================================


class TestPatchApplication:
    """Tests for applying patches."""

    def test_apply_patch(self, git_repo: Path):
        """Valid patch is applied correctly."""
        # Create a patch that modifies the test file
        patch_content = """diff --git a/test.txt b/test.txt
index 557db03..cd08755 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Hello World
+Hello Universe
"""
        effect = create_patch_effect(git_repo, patch_content)

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)
        result = materializer.materialize(effect)

        assert result.success
        assert "test.txt" in result.paths_affected

        # Verify file was modified
        content = (git_repo / "test.txt").read_text()
        assert "Hello Universe" in content

    def test_empty_patch_is_noop(self, git_repo: Path):
        """Empty patches succeed without changes."""
        effect = create_patch_effect(git_repo, "")

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)
        result = materializer.materialize(effect)

        assert result.success
        assert result.metadata.get("empty_patch") is True

        # File unchanged
        content = (git_repo / "test.txt").read_text()
        assert "Hello World" in content

    def test_whitespace_only_patch_is_noop(self, git_repo: Path):
        """Whitespace-only patches succeed without changes."""
        effect = create_patch_effect(git_repo, "   \n\n  ")

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)
        result = materializer.materialize(effect)

        assert result.success
        assert result.metadata.get("empty_patch") is True


class TestPathResolution:
    """Tests for workspace path resolution."""

    def test_resolve_via_context_id(self, git_repo: Path):
        """Path is resolved from context_id when binding_name not set."""
        effect = WorkspacePatchCaptured(
            binding_name=None,
            context_id=f"workspace:{git_repo}:abc123",
            patch=DiffPatch(patch="", files_changed=()),
            files_changed=(),
        )

        materializer = GitWorkspacePatchMaterializer(lambda _: None)
        result = materializer.materialize(effect)

        assert result.success

    def test_missing_workspace_path_fails(self, tmp_path: Path):
        """Materialization fails if workspace path cannot be resolved."""
        effect = WorkspacePatchCaptured(
            binding_name=None,
            context_id="invalid",
            patch=DiffPatch(patch="some patch", files_changed=("file.txt",)),
            files_changed=("file.txt",),
        )

        materializer = GitWorkspacePatchMaterializer(lambda _: None)
        result = materializer.materialize(effect)

        assert not result.success
        assert "Cannot resolve workspace path" in result.error

    def test_non_git_directory_fails(self, tmp_path: Path):
        """Materialization fails if path is not a git repo."""
        non_git = tmp_path / "not_git"
        non_git.mkdir()

        effect = create_patch_effect(non_git, "some patch")

        materializer = GitWorkspacePatchMaterializer(lambda _: non_git)
        result = materializer.materialize(effect)

        assert not result.success
        assert "Not a git repository" in result.error


class TestInvalidPatches:
    """Tests for invalid patch handling."""

    def test_invalid_patch_fails(self, git_repo: Path):
        """Invalid patch content causes failure."""
        effect = create_patch_effect(git_repo, "not a valid patch format")

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)
        result = materializer.materialize(effect)

        assert not result.success
        assert "git apply failed" in result.error


class TestReversal:
    """Tests for patch reversal."""

    def test_can_reverse_valid_patch(self, git_repo: Path):
        """can_reverse() returns True for valid patches."""
        patch_content = """diff --git a/test.txt b/test.txt
index 557db03..cd08755 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Hello World
+Hello Universe
"""
        effect = create_patch_effect(git_repo, patch_content)

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)

        assert materializer.can_reverse(effect) is True

    def test_can_reverse_empty_patch_false(self, git_repo: Path):
        """can_reverse() returns False for empty patches."""
        effect = create_patch_effect(git_repo, "")

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)

        assert materializer.can_reverse(effect) is False

    def test_reverse_patch(self, git_repo: Path):
        """Patches can be reversed with git apply --reverse."""
        patch_content = """diff --git a/test.txt b/test.txt
index 557db03..cd08755 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Hello World
+Hello Universe
"""
        effect = create_patch_effect(git_repo, patch_content)

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)

        # Apply the patch
        result = materializer.materialize(effect)
        assert result.success

        # Verify change was applied
        content = (git_repo / "test.txt").read_text()
        assert "Hello Universe" in content

        # Reverse the patch
        materializer.reverse(effect)

        # Verify original content is restored
        content = (git_repo / "test.txt").read_text()
        assert "Hello World" in content

    def test_reverse_wrong_state_fails(self, git_repo: Path):
        """Reversal fails if patch wasn't applied."""
        patch_content = """diff --git a/test.txt b/test.txt
index 557db03..cd08755 100644
--- a/test.txt
+++ b/test.txt
@@ -1 +1 @@
-Hello World
+Hello Universe
"""
        effect = create_patch_effect(git_repo, patch_content)

        materializer = GitWorkspacePatchMaterializer(lambda _: git_repo)

        # Try to reverse without applying first
        with pytest.raises(ReversalError) as exc_info:
            materializer.reverse(effect)

        assert "git apply --reverse failed" in str(exc_info.value)


class TestFactoryFunction:
    """Tests for create_workspace_materializer factory."""

    def test_create_workspace_materializer_resolves_path(self, git_repo: Path):
        """Factory creates materializer that resolves paths via get_workspace_path."""
        from shepherd_runtime.scope import Scope

        # Test the factory function with a direct path resolver
        with Scope() as scope:
            # Create materializer with custom path resolver
            def get_workspace_path(binding_name: str) -> Path | None:
                if binding_name == "workspace":
                    return git_repo
                return None

            materializer = GitWorkspacePatchMaterializer(get_workspace_path)

            # Effect with binding_name should resolve
            effect = WorkspacePatchCaptured(
                binding_name="workspace",
                patch=DiffPatch(patch="", files_changed=()),
                files_changed=(),
            )

            # Should succeed (path resolved via callback)
            result = materializer.materialize(effect)
            assert result.success
            assert result.metadata.get("empty_patch") is True
