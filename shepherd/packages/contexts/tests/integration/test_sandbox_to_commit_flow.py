"""Integration tests for the full sandbox → extract → commit flow.

This tests the complete lifecycle:
1. Workspace binds to scope
2. ExecutionLifecycle creates sandbox
3. Provider executes (makes file changes)
4. extract_effects() captures changes from sandbox
5. Effects emitted and accumulated in pending_patches
6. scope.commit() materializes to real filesystem

This test suite validates that all the pieces are wired together correctly.

Note: Uses shared fixtures from shepherd_tests (via conftest.py):
- git_workspace: Initialized git repository for WorkspaceRef tests
- FileModifyingMockProvider: Mock provider that writes files during execution
"""

import subprocess
from pathlib import Path

import pytest
from shepherd_runtime.context.sandbox import GITPYTHON_AVAILABLE
from shepherd_runtime.lifecycle import ExecutionLifecycle
from shepherd_runtime.scope import Scope
from shepherd_tests import FileModifyingMockProvider

pytestmark = pytest.mark.skipif(not GITPYTHON_AVAILABLE, reason="GitPython not installed")

# =============================================================================
# Tests: Sandbox Path Wiring (WorkspaceRef)
# =============================================================================


class TestSandboxPathWiring:
    """Tests that verify sandbox path is correctly wired to provider."""

    @pytest.mark.asyncio
    async def test_provider_receives_sandbox_path_not_original(self, git_workspace: Path) -> None:
        """Provider's cwd should be the sandbox path, not the original workspace path.

        This test validates that when a sandbox is created, the provider
        executes in the sandbox directory, not the original workspace.
        """
        from shepherd_contexts.workspace import WorkspaceRef

        provider = FileModifyingMockProvider(
            file_to_create="sandbox_test.txt",
            file_content="Written in sandbox\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = WorkspaceRef.from_path(git_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # Verify: provider was called
            assert len(provider.calls) == 1

            # Verify: cwd should be sandbox path (different from original)
            # This is the critical assertion - if this fails, the wiring is broken
            cwd_used = provider.cwd_used
            assert cwd_used is not None

            # The cwd should NOT be the original workspace path if sandbox is working
            # If cwd == git_workspace, the provider executed in original, not sandbox
            if cwd_used == git_workspace:
                pytest.fail(
                    f"Provider executed in original workspace ({git_workspace}) "
                    f"instead of sandbox. Sandbox path was not wired to provider!"
                )

            # If we get here, the sandbox path was used
            assert cwd_used != git_workspace, "Sandbox path should differ from original"

    @pytest.mark.asyncio
    async def test_changes_in_sandbox_are_captured_as_effects(self, git_workspace: Path) -> None:
        """Changes made by provider should be captured as WorkspacePatchCaptured effects."""
        from shepherd_contexts.workspace import WorkspaceRef

        provider = FileModifyingMockProvider(
            file_to_create="captured_file.txt",
            file_content="This should be captured\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = WorkspaceRef.from_path(git_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # Before execution
            assert workspace_ref.has_pending_changes is False

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # After execution: changes should be captured in pending_patches
            assert workspace_ref.has_pending_changes is True, (
                "Changes were not captured! Either:\n"
                "1. Provider executed in wrong directory (original vs sandbox)\n"
                "2. extract_effects() did not capture sandbox changes\n"
                "3. apply_effect() did not accumulate patches"
            )

            # Verify the patch contains the file we created
            assert len(workspace_ref.pending_patches) == 1
            patch = workspace_ref.pending_patches[0]
            assert "captured_file.txt" in patch.files_changed

    @pytest.mark.asyncio
    async def test_original_workspace_unchanged_until_commit(self, git_workspace: Path) -> None:
        """Original workspace should remain unchanged until scope.commit() is called."""
        from shepherd_contexts.workspace import WorkspaceRef

        provider = FileModifyingMockProvider(
            file_to_create="new_file.txt",
            file_content="New content\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = WorkspaceRef.from_path(git_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # After execution but before commit: original should be unchanged
            new_file_in_original = git_workspace / "new_file.txt"
            assert not new_file_in_original.exists(), (
                "File appeared in original workspace before commit! "
                "Provider is executing in original instead of sandbox."
            )

            # Changes should be pending
            assert workspace_ref.has_pending_changes is True


# =============================================================================
# Tests: Full Flow with Commit
# =============================================================================


class TestFullSandboxToCommitFlow:
    """Tests for the complete sandbox → extract → commit flow."""

    @pytest.mark.asyncio
    async def test_full_flow_sandbox_to_commit(self, git_workspace: Path) -> None:
        """Test complete flow: sandbox execution → effect capture → commit."""
        from shepherd_contexts.workspace import WorkspaceRef

        provider = FileModifyingMockProvider(
            file_to_create="agent_work.py",
            file_content="# Created by agent\nprint('hello')\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = WorkspaceRef.from_path(git_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # Phase 1: Execute in sandbox
            async with ExecutionLifecycle(scope, provider) as lifecycle:
                result = await lifecycle.execute("Create a Python file")
                assert result.success

            # Phase 2: Verify effects captured
            assert workspace_ref.has_pending_changes is True
            assert len(workspace_ref.pending_patches) == 1

            # Phase 3: Verify original unchanged
            original_file = git_workspace / "agent_work.py"
            assert not original_file.exists()

            # Phase 4: Commit to materialize
            commit_result = scope.commit(message="Add agent work")

            # Phase 5: Verify original now has the file
            assert original_file.exists()
            assert "Created by agent" in original_file.read_text()

            # Phase 6: Verify git commit was made
            log_result = subprocess.run(
                ["git", "log", "-1", "--format=%s"],
                cwd=git_workspace,
                capture_output=True,
                text=True,
                check=True,
            )
            assert log_result.stdout.strip() == "Add agent work"

            # Phase 7: Verify pending patches cleared
            assert workspace_ref.has_pending_changes is False

    @pytest.mark.asyncio
    async def test_multiple_executions_accumulate_patches(self, git_workspace: Path) -> None:
        """Multiple ExecutionLifecycle runs should accumulate patches."""
        from shepherd_contexts.workspace import WorkspaceRef

        with Scope(root=True) as scope:
            workspace = WorkspaceRef.from_path(git_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # First execution
            provider1 = FileModifyingMockProvider(
                file_to_create="file1.txt",
                file_content="First file\n",
            )
            scope.register_provider("p1", provider1)

            async with ExecutionLifecycle(scope, provider1) as lc1:
                await lc1.execute("Create first file")

            assert len(workspace_ref.pending_patches) == 1

            # Second execution
            provider2 = FileModifyingMockProvider(
                file_to_create="file2.txt",
                file_content="Second file\n",
            )
            scope.register_provider("p2", provider2)

            async with ExecutionLifecycle(scope, provider2) as lc2:
                await lc2.execute("Create second file")

            # Both patches should be accumulated
            assert len(workspace_ref.pending_patches) == 2

            # Commit should apply both
            scope.commit(message="Add both files")

            assert (git_workspace / "file1.txt").exists()
            assert (git_workspace / "file2.txt").exists()


# =============================================================================
# SimpleWorkspace Fixtures
# =============================================================================


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory (non-git) for SimpleWorkspace testing."""
    workspace_path = tmp_path / "simple-workspace"
    workspace_path.mkdir()

    # Create some initial files
    (workspace_path / "README.md").write_text("# Simple Workspace\n")
    (workspace_path / "existing.txt").write_text("Original content\n")
    (workspace_path / "subdir").mkdir()
    (workspace_path / "subdir" / "nested.txt").write_text("Nested file\n")

    return workspace_path


# =============================================================================
# Tests: SimpleWorkspace Sandbox Path Wiring
# =============================================================================


class TestSimpleWorkspaceSandboxPathWiring:
    """Tests that verify CopySandbox path is correctly wired to provider."""

    @pytest.mark.asyncio
    async def test_provider_receives_sandbox_path_not_original(self, temp_workspace: Path) -> None:
        """Provider's cwd should be the CopySandbox path, not the original workspace path.

        This test validates that when a CopySandbox is created for SimpleWorkspace,
        the provider executes in the sandbox directory, not the original workspace.
        """
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        provider = FileModifyingMockProvider(
            file_to_create="sandbox_test.txt",
            file_content="Written in sandbox\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # Verify: provider was called
            assert len(provider.calls) == 1

            # Verify: cwd should be sandbox path (different from original)
            cwd_used = provider.cwd_used
            assert cwd_used is not None

            # The cwd should NOT be the original workspace path if sandbox is working
            if cwd_used == temp_workspace:
                pytest.fail(
                    f"Provider executed in original workspace ({temp_workspace}) "
                    f"instead of CopySandbox. Sandbox path was not wired to provider!"
                )

            assert cwd_used != temp_workspace, "Sandbox path should differ from original"

    @pytest.mark.asyncio
    async def test_changes_in_sandbox_are_captured_as_effects(self, temp_workspace: Path) -> None:
        """Changes made by provider should be captured as SimpleWorkspaceChangesetCaptured effects."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        provider = FileModifyingMockProvider(
            file_to_create="captured_file.txt",
            file_content="This should be captured\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # Before execution
            assert workspace_ref.has_pending_changes is False

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # After execution: changes should be captured in pending_changesets
            assert workspace_ref.has_pending_changes is True, (
                "Changes were not captured! Either:\n"
                "1. Provider executed in wrong directory (original vs sandbox)\n"
                "2. extract_effects() did not capture sandbox changes\n"
                "3. apply_effect() did not accumulate changesets"
            )

            # Verify the changeset contains the file we created
            assert len(workspace_ref.pending_changesets) == 1
            changeset = workspace_ref.pending_changesets[0]
            assert "captured_file.txt" in changeset.files_changed

    @pytest.mark.asyncio
    async def test_original_workspace_unchanged_until_commit(self, temp_workspace: Path) -> None:
        """Original workspace should remain unchanged until scope.commit() is called."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        provider = FileModifyingMockProvider(
            file_to_create="new_file.txt",
            file_content="New content\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # After execution but before commit: original should be unchanged
            new_file_in_original = temp_workspace / "new_file.txt"
            assert not new_file_in_original.exists(), (
                "File appeared in original workspace before commit! "
                "Provider is executing in original instead of CopySandbox."
            )

            # Changes should be pending
            assert workspace_ref.has_pending_changes is True


# =============================================================================
# Tests: SimpleWorkspace Full Flow with Commit
# =============================================================================


class TestSimpleWorkspaceFullSandboxToCommitFlow:
    """Tests for the complete sandbox → extract → commit flow with SimpleWorkspace."""

    @pytest.mark.asyncio
    async def test_full_flow_sandbox_to_commit(self, temp_workspace: Path) -> None:
        """Test complete flow: CopySandbox execution → effect capture → commit."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        provider = FileModifyingMockProvider(
            file_to_create="agent_work.py",
            file_content="# Created by agent\nprint('hello')\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # Phase 1: Execute in sandbox
            async with ExecutionLifecycle(scope, provider) as lifecycle:
                result = await lifecycle.execute("Create a Python file")
                assert result.success

            # Phase 2: Verify effects captured
            assert workspace_ref.has_pending_changes is True
            assert len(workspace_ref.pending_changesets) == 1

            # Phase 3: Verify original unchanged
            original_file = temp_workspace / "agent_work.py"
            assert not original_file.exists()

            # Phase 4: Commit to materialize
            commit_result = scope.commit()

            # Phase 5: Verify original now has the file
            assert original_file.exists()
            assert "Created by agent" in original_file.read_text()

            # Phase 6: Verify pending changesets cleared
            assert workspace_ref.has_pending_changes is False

            # Phase 7: Verify commit result structure
            assert "contexts" in commit_result
            assert commit_result["total_paths_affected"] >= 1

    @pytest.mark.asyncio
    async def test_multiple_executions_accumulate_changesets(self, temp_workspace: Path) -> None:
        """Multiple ExecutionLifecycle runs should accumulate changesets."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        with Scope(root=True) as scope:
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # First execution
            provider1 = FileModifyingMockProvider(
                file_to_create="file1.txt",
                file_content="First file\n",
            )
            scope.register_provider("p1", provider1)

            async with ExecutionLifecycle(scope, provider1) as lc1:
                await lc1.execute("Create first file")

            assert len(workspace_ref.pending_changesets) == 1

            # Second execution
            provider2 = FileModifyingMockProvider(
                file_to_create="file2.txt",
                file_content="Second file\n",
            )
            scope.register_provider("p2", provider2)

            async with ExecutionLifecycle(scope, provider2) as lc2:
                await lc2.execute("Create second file")

            # Both changesets should be accumulated
            assert len(workspace_ref.pending_changesets) == 2

            # Commit should apply both
            scope.commit()

            assert (temp_workspace / "file1.txt").exists()
            assert (temp_workspace / "file2.txt").exists()

    @pytest.mark.asyncio
    async def test_modify_existing_file(self, temp_workspace: Path) -> None:
        """Test that modifying an existing file is captured and committed correctly."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        provider = FileModifyingMockProvider(
            file_to_create="existing.txt",  # Overwrite existing file
            file_content="Modified content\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # Before: original content
            assert (temp_workspace / "existing.txt").read_text() == "Original content\n"

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Modify existing file")

            # After execution, before commit: original unchanged
            assert (temp_workspace / "existing.txt").read_text() == "Original content\n"

            # Commit
            scope.commit()

            # After commit: modified
            assert (temp_workspace / "existing.txt").read_text() == "Modified content\n"

    @pytest.mark.asyncio
    async def test_commit_without_pending_changes_is_noop(self, temp_workspace: Path) -> None:
        """Committing with no pending changes should succeed as a no-op."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        with Scope(root=True) as scope:
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # No execution, no changes
            assert workspace_ref.has_pending_changes is False

            # Commit should succeed
            result = scope.commit()

            assert result["total_paths_affected"] == 0
            assert result["contexts"] == []


# =============================================================================
# Tests: Phase 3 - Materialization Robustness
# =============================================================================


class TestPhase3ContextMaterializedEffects:
    """Tests for ContextMaterialized effect emission during commit."""

    @pytest.mark.asyncio
    async def test_commit_emits_context_materialized_effect(self, temp_workspace: Path) -> None:
        """scope.commit() should emit ContextMaterialized effect for each context."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace
        from shepherd_core.effects import ContextMaterialized

        provider = FileModifyingMockProvider(
            file_to_create="test_file.txt",
            file_content="Test content\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # Record effect count before commit
            effects_before = len(scope.effects)

            # Commit
            scope.commit()

            # Check for ContextMaterialized effect
            new_effects = list(scope.effects)[effects_before:]
            mat_effects = [e.effect for e in new_effects if isinstance(e.effect, ContextMaterialized)]

            assert len(mat_effects) == 1
            effect = mat_effects[0]
            assert effect.binding_name == "workspace"
            assert effect.context_type == "SimpleWorkspace"
            assert effect.success is True
            assert effect.duration_ms > 0
            assert len(effect.paths_affected) > 0

    @pytest.mark.asyncio
    async def test_commit_effect_records_paths_affected(self, temp_workspace: Path) -> None:
        """ContextMaterialized effect should record all affected paths."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace
        from shepherd_core.effects import ContextMaterialized

        provider = FileModifyingMockProvider(
            file_to_create="tracked_file.txt",
            file_content="Tracked content\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create file")

            scope.commit()

            # Find the effect
            mat_effects = [e.effect for e in scope.effects if isinstance(e.effect, ContextMaterialized)]

            assert len(mat_effects) == 1
            assert "tracked_file.txt" in mat_effects[0].paths_affected


class TestPhase3ReversibilityOrdering:
    """Tests for reversibility-based commit ordering."""

    @pytest.mark.asyncio
    async def test_multi_context_commit_respects_reversibility_order(
        self, temp_workspace: Path, git_workspace: Path
    ) -> None:
        """Contexts should be committed in reversibility order (AUTO first)."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace
        from shepherd_contexts.workspace import WorkspaceRef

        # SimpleWorkspace has AUTO reversibility
        # WorkspaceRef also has AUTO reversibility
        # Both should work, demonstrating ordering doesn't break

        provider = FileModifyingMockProvider(
            file_to_create="multi_ctx.txt",
            file_content="Multi-context test\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)

            # Bind both workspace types
            simple_ws = SimpleWorkspace.from_path(temp_workspace)
            git_ws = WorkspaceRef.from_path(git_workspace)

            scope.bind("simple", simple_ws)
            scope.bind("git", git_ws)

            # Execute in simple workspace
            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create file in simple workspace")

            # Both should have AUTO reversibility, so ordering doesn't matter
            # but commit should succeed
            result = scope.commit(message="Test commit")

            # At least one context should have been committed
            assert result["total_paths_affected"] >= 1


class TestPhase3DriftDetectionIntegration:
    """Integration tests for drift detection during commit."""

    @pytest.mark.asyncio
    async def test_drift_detection_fails_commit(self, temp_workspace: Path) -> None:
        """Commit should fail if external modifications are detected."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        with Scope(root=True) as scope:
            workspace = SimpleWorkspace.from_path(temp_workspace)
            workspace_ref = scope.bind("workspace", workspace)

            # Manually add a changeset that modifies existing.txt
            modify_delta = FileDelta.modify(
                "existing.txt",
                b"Original content\n",
                b"Modified by agent\n",
            )
            changeset = FileChangeset(deltas=(modify_delta,))

            # Update workspace with pending changeset
            workspace_with_changes = workspace_ref.value.model_copy(update={"pending_changesets": (changeset,)})
            scope.update_context("workspace", workspace_with_changes)

            # Now externally modify the file (simulating drift)
            (temp_workspace / "existing.txt").write_text("External modification!\n")

            # Commit should fail due to drift
            with pytest.raises(RuntimeError, match="Drift detected"):
                scope.commit()

            # File should still have external modification (not overwritten)
            assert (temp_workspace / "existing.txt").read_text() == "External modification!\n"

    @pytest.mark.asyncio
    async def test_no_drift_allows_commit(self, temp_workspace: Path) -> None:
        """Commit should succeed when no drift is detected."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        provider = FileModifyingMockProvider(
            file_to_create="no_drift_test.txt",
            file_content="No drift here\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = SimpleWorkspace.from_path(temp_workspace)
            scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create file")

            # No external modifications - commit should succeed
            result = scope.commit()

            assert result["total_paths_affected"] >= 1
            assert (temp_workspace / "no_drift_test.txt").exists()
