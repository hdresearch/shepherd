"""Integration tests for session resumption in containers.

These tests validate the session resumption implementation from
PLAN-session-resumption-containers.md:

1. Session overlay creation (Change 4)
2. CWD symlink creation for path mismatch (Change 6)
3. Session merge filters symlinks (Change 10)
4. Transcript validation with graceful fallback (Change 9)

Unit tests cover individual components; these tests verify end-to-end behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shepherd_runtime.device.container.podman import OverlayMount

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from shepherd_contexts.session.effects import SessionCreated, SessionForked
from shepherd_core.foundation.protocols.device import SandboxConfig
from shepherd_core.types import compute_transcript_path
from shepherd_runtime.device.container.device import ContainerDevice
from shepherd_runtime.device.container.podman import ContainerSandbox
from shepherd_runtime.device.container.task_runner import (
    _build_binding_from_contexts,
    _validate_session_resumable,
)
from shepherd_runtime.lifecycle import ExecutionLifecycle

from .conftest import requires_podman

# =============================================================================
# Unit Tests for Session Components
# =============================================================================


class TestComputeProjectFolder:
    """Tests for _compute_project_folder() method."""

    def test_replaces_slashes_with_dashes(self, temp_overlays: Path) -> None:
        """Forward slashes are replaced with dashes."""
        device = ContainerDevice(overlays_root=temp_overlays)
        result = device._compute_project_folder("/Users/alice/project")
        assert result == "-Users-alice-project"

    def test_replaces_underscores_with_dashes(self, temp_overlays: Path) -> None:
        """Underscores are replaced with dashes (matches Claude CLI behavior)."""
        device = ContainerDevice(overlays_root=temp_overlays)
        result = device._compute_project_folder("/Users/alice/my_project")
        assert result == "-Users-alice-my-project"

    def test_replaces_both_slashes_and_underscores(
        self,
        temp_overlays: Path,
        tmp_path: Path,
    ) -> None:
        """Both slashes and underscores are replaced."""
        device = ContainerDevice(overlays_root=temp_overlays)
        # Use tmp_path to avoid macOS path resolution issues
        test_path = tmp_path / "user_name" / "my_project" / "src"
        test_path.mkdir(parents=True)
        result = device._compute_project_folder(test_path)
        # Check that underscores are converted to dashes
        assert "-user-name-" in result
        assert "-my-project-" in result
        assert "_" not in result

    def test_handles_path_objects(self, temp_overlays: Path) -> None:
        """Accepts Path objects, not just strings."""
        device = ContainerDevice(overlays_root=temp_overlays)
        result = device._compute_project_folder(Path("/Users/alice/project"))
        assert result == "-Users-alice-project"


class TestCreateSessionSymlink:
    """Tests for _create_session_symlink() method."""

    def test_creates_symlink_for_cwd_mismatch(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Symlink is created when host and container CWD differ."""
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create a sandbox with session overlay
        sandbox = ContainerSandbox.create(unique_task_id)

        # Create overlay directory structure manually (no actual mount needed)
        overlay_dir = temp_overlays / unique_task_id / "session"
        upper_dir = overlay_dir / "upper"
        upper_dir.mkdir(parents=True)

        # Create a mock overlay mount object
        from shepherd_runtime.device.container.podman import OverlayMount

        overlay = OverlayMount(
            task_id=unique_task_id,
            context_name="session",
            lower=temp_overlays,
            upper=upper_dir,
            work=overlay_dir / "work",
            merged=overlay_dir / "merged",
        )
        sandbox.overlays["session"] = overlay

        # Create symlink for CWD mismatch
        host_cwd = "/Users/alice/project"
        container_cwd = "/container/workspace"

        device._create_session_symlink(sandbox, host_cwd, container_cwd)

        # Verify symlink was created
        # Note: exists() follows symlinks; use is_symlink() + lexists() for broken symlinks
        projects_dir = upper_dir / "projects"
        symlink_path = projects_dir / "-container-workspace"

        assert symlink_path.is_symlink(), "Symlink should be created"
        assert str(symlink_path.readlink()) == "-Users-alice-project"

    def test_no_symlink_when_cwd_matches(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """No symlink created when host and container CWD are the same."""
        device = ContainerDevice(overlays_root=temp_overlays)

        sandbox = ContainerSandbox.create(unique_task_id)

        # Create overlay directory structure manually
        overlay_dir = temp_overlays / unique_task_id / "session"
        upper_dir = overlay_dir / "upper"
        upper_dir.mkdir(parents=True)

        from shepherd_runtime.device.container.podman import OverlayMount

        overlay = OverlayMount(
            task_id=unique_task_id,
            context_name="session",
            lower=temp_overlays,
            upper=upper_dir,
            work=overlay_dir / "work",
            merged=overlay_dir / "merged",
        )
        sandbox.overlays["session"] = overlay

        # Same CWD (after transformation)
        cwd = "/container/workspace"
        device._create_session_symlink(sandbox, cwd, cwd)

        # Verify no symlink created
        projects_dir = upper_dir / "projects"
        assert not projects_dir.exists() or not list(projects_dir.glob("*"))

    def test_no_symlink_without_session_overlay(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """No symlink creation attempted without session overlay."""
        device = ContainerDevice(overlays_root=temp_overlays)
        sandbox = ContainerSandbox.create(unique_task_id)
        # No session overlay added

        # Should not raise, just return early
        device._create_session_symlink(
            sandbox,
            "/Users/alice/project",
            "/container/workspace",
        )


class TestMergeSessionOverlay:
    """Tests for _merge_session_overlay() method."""

    def _create_mock_overlay(self, temp_overlays: Path, unique_task_id: str) -> OverlayMount:
        """Helper to create mock overlay with directory structure."""
        from shepherd_runtime.device.container.podman import OverlayMount

        overlay_dir = temp_overlays / unique_task_id / "session"
        upper_dir = overlay_dir / "upper"
        upper_dir.mkdir(parents=True)

        return OverlayMount(
            task_id=unique_task_id,
            context_name="session",
            lower=temp_overlays,
            upper=upper_dir,
            work=overlay_dir / "work",
            merged=overlay_dir / "merged",
        )

    def test_merges_jsonl_files(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """JSONL transcript files are merged to host."""
        device = ContainerDevice(overlays_root=temp_overlays)

        sandbox = ContainerSandbox.create(unique_task_id)
        overlay = self._create_mock_overlay(temp_overlays, unique_task_id)
        sandbox.overlays["session"] = overlay

        # Create a transcript file in upper layer
        projects_dir = Path(overlay.upper) / "projects" / "-Users-alice-project"
        projects_dir.mkdir(parents=True)
        transcript = projects_dir / "session123.jsonl"
        transcript.write_text('{"message": "test"}')

        # Merge to a temp target
        target_dir = temp_overlays / "merge_target"
        target_dir.mkdir()

        merged = device._merge_session_overlay(sandbox, target_dir)

        # Verify transcript was merged
        assert len(merged) == 1
        expected_path = target_dir / "projects" / "-Users-alice-project" / "session123.jsonl"
        assert expected_path.exists()
        assert expected_path.read_text() == '{"message": "test"}'

    def test_excludes_symlinks_from_merge(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Symlinks are excluded from merge (infrastructure, not user data)."""
        device = ContainerDevice(overlays_root=temp_overlays)

        sandbox = ContainerSandbox.create(unique_task_id)
        overlay = self._create_mock_overlay(temp_overlays, unique_task_id)
        sandbox.overlays["session"] = overlay

        # Create both a symlink and a transcript file
        projects_dir = Path(overlay.upper) / "projects"
        projects_dir.mkdir(parents=True)

        # Create symlink
        symlink_path = projects_dir / "-container-workspace"
        symlink_path.symlink_to("-Users-alice-project")

        # Create transcript in the actual project folder
        actual_project = projects_dir / "-Users-alice-project"
        actual_project.mkdir()
        transcript = actual_project / "session123.jsonl"
        transcript.write_text('{"message": "test"}')

        # Merge to target
        target_dir = temp_overlays / "merge_target"
        target_dir.mkdir()

        merged = device._merge_session_overlay(sandbox, target_dir)

        # Verify transcript merged but NOT symlink
        assert len(merged) == 1
        assert merged[0].name == "session123.jsonl"

        # Symlink should NOT exist in target
        target_symlink = target_dir / "projects" / "-container-workspace"
        assert not target_symlink.exists()

    def test_excludes_non_transcript_files(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Only .jsonl and .json files are merged."""
        device = ContainerDevice(overlays_root=temp_overlays)

        sandbox = ContainerSandbox.create(unique_task_id)
        overlay = self._create_mock_overlay(temp_overlays, unique_task_id)
        sandbox.overlays["session"] = overlay

        # Create various files
        projects_dir = Path(overlay.upper) / "projects" / "-Users-alice-project"
        projects_dir.mkdir(parents=True)
        (projects_dir / "session.jsonl").write_text("{}")
        (projects_dir / "config.json").write_text("{}")
        (projects_dir / "notes.txt").write_text("notes")
        (projects_dir / "script.py").write_text("# python")

        target_dir = temp_overlays / "merge_target"
        target_dir.mkdir()

        merged = device._merge_session_overlay(sandbox, target_dir)

        # Only .jsonl and .json files should be merged
        assert len(merged) == 2
        merged_names = {f.name for f in merged}
        assert merged_names == {"session.jsonl", "config.json"}

    def test_empty_overlay_returns_empty_list(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Empty overlay returns empty list without error."""
        device = ContainerDevice(overlays_root=temp_overlays)

        sandbox = ContainerSandbox.create(unique_task_id)
        overlay = self._create_mock_overlay(temp_overlays, unique_task_id)
        sandbox.overlays["session"] = overlay

        target_dir = temp_overlays / "merge_target"
        target_dir.mkdir()

        merged = device._merge_session_overlay(sandbox, target_dir)
        assert merged == []


class TestValidateSessionResumable:
    """Tests for _validate_session_resumable() function."""

    def test_returns_none_for_none_session(self) -> None:
        """None session_id returns None."""
        result = _validate_session_resumable(None, "/some/cwd")
        assert result is None

    def test_returns_session_id_when_transcript_exists(self, tmp_path: Path) -> None:
        """Returns session_id when transcript file exists."""
        session_id = "test-session-123"

        # Create the expected transcript file
        transcript_path = compute_transcript_path(tmp_path, session_id)
        Path(transcript_path).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript_path).write_text('{"test": true}')

        result = _validate_session_resumable(session_id, str(tmp_path))
        assert result == session_id

    def test_returns_none_when_transcript_missing(self, tmp_path: Path) -> None:
        """Returns None when transcript file doesn't exist (graceful fallback)."""
        session_id = "nonexistent-session"

        # Don't create the transcript file
        result = _validate_session_resumable(session_id, str(tmp_path))
        assert result is None


class TestBuildBindingFromContextsSession:
    """Tests for session extraction in _build_binding_from_contexts()."""

    def test_extracts_session_id_from_object(self) -> None:
        """Session ID is extracted from session context object."""
        session_state = MagicMock(spec=["context_type", "session_id"])
        session_state.context_type = "session"
        session_state.session_id = "test-session-456"

        contexts = {"session": session_state}
        binding = _build_binding_from_contexts(contexts, None, None)

        assert binding.session_id == "test-session-456"
        assert binding.session_isolation == "forked"

    def test_extracts_session_id_from_dict(self) -> None:
        """Session ID is extracted from dict-based session context."""
        contexts = {
            "session": {
                "context_type": "session",
                "session_id": "dict-session-789",
            }
        }
        binding = _build_binding_from_contexts(contexts, None, None)

        assert binding.session_id == "dict-session-789"
        assert binding.session_isolation == "forked"

    def test_isolated_when_no_session_id(self) -> None:
        """Session isolation is 'isolated' when no session_id present."""
        contexts = {
            "session": {
                "context_type": "session",
                "session_id": None,
            }
        }
        binding = _build_binding_from_contexts(contexts, None, None)

        assert binding.session_id is None
        assert binding.session_isolation == "isolated"

    def test_forked_only_when_session_id_exists(self) -> None:
        """Session isolation is 'forked' only when session_id is present."""
        # With session_id
        contexts_with = {"session": {"context_type": "session", "session_id": "abc"}}
        binding_with = _build_binding_from_contexts(contexts_with, None, None)
        assert binding_with.session_isolation == "forked"

        # Without session_id
        contexts_without = {"session": {"context_type": "session"}}
        binding_without = _build_binding_from_contexts(contexts_without, None, None)
        assert binding_without.session_isolation == "isolated"


class TestComputeTranscriptPath:
    """Tests for compute_transcript_path() with underscore handling."""

    def test_replaces_underscores_in_path(self) -> None:
        """Underscores in cwd are replaced with dashes."""
        path = compute_transcript_path("/Users/alice/my_project", "session123")
        assert "-my-project" in path
        assert "_" not in path.split("/")[-2]  # Project folder shouldn't have underscore

    def test_replaces_slashes_in_path(self) -> None:
        """Forward slashes in cwd are replaced with dashes."""
        path = compute_transcript_path("/Users/alice/project", "session123")
        assert "-Users-alice-project" in path

    def test_returns_full_path_with_session_id(self) -> None:
        """Returns complete path including session_id.jsonl."""
        path = compute_transcript_path("/Users/alice/project", "abc123")
        assert path.endswith("abc123.jsonl")


# =============================================================================
# Integration Tests (Require Podman)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.container
@requires_podman
class TestSessionOverlayCreation:
    """Integration tests for session overlay creation in containers."""

    @pytest.mark.asyncio
    async def test_session_context_creates_overlay(
        self,
        mock_scope: MagicMock,
        temp_overlays: Path,
    ) -> None:
        """Session context state triggers overlay creation for ~/.claude."""
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create a config with session context state
        config = SandboxConfig(
            parent_sandbox_id=None,
            context_states={
                "session": {
                    "context_type": "session",
                    "session_id": "test-session",
                    "host_cwd": "/Users/alice/project",
                }
            },
        )

        # Patch home() to use temp directory
        with patch("pathlib.Path.home", return_value=temp_overlays):
            # Ensure the .claude dir exists for overlay
            claude_dir = temp_overlays / ".claude"
            claude_dir.mkdir(exist_ok=True)

            with patch.object(device.manager, "validate_environment"):
                sandbox = await device.create_sandbox(mock_scope, config)

        # Verify session overlay was created
        assert "session" in sandbox.overlays
        assert sandbox._metadata.get("session_host_cwd") == "/Users/alice/project"

        # Cleanup
        await device.cleanup(sandbox)

    def test_session_symlink_created_with_underscore_replacement(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Session symlink correctly handles underscores in host CWD."""
        from shepherd_runtime.device.container.podman import OverlayMount

        device = ContainerDevice(overlays_root=temp_overlays)

        # Create sandbox with mock overlay
        sandbox = ContainerSandbox.create(unique_task_id)

        overlay_dir = temp_overlays / unique_task_id / "session"
        upper_dir = overlay_dir / "upper"
        upper_dir.mkdir(parents=True)

        overlay = OverlayMount(
            task_id=unique_task_id,
            context_name="session",
            lower=temp_overlays,
            upper=upper_dir,
            work=overlay_dir / "work",
            merged=overlay_dir / "merged",
        )
        sandbox.overlays["session"] = overlay

        # Test with underscore in path
        host_cwd = "/Users/alice/original_project"  # Note: underscore
        container_cwd = "/container/workspace"

        device._create_session_symlink(sandbox, host_cwd, container_cwd)

        # Verify symlink exists
        # Note: exists() follows symlinks; use is_symlink() for broken symlinks
        projects_dir = upper_dir / "projects"
        symlink_path = projects_dir / "-container-workspace"

        assert symlink_path.is_symlink(), "Symlink should be created"
        # Target should use dashes (underscore replaced)
        assert str(symlink_path.readlink()) == "-Users-alice-original-project"


# =============================================================================
# Lifecycle Integration Tests
# =============================================================================


class _NullPipeline:
    """Minimal pipeline stub for testing _execute_on_device without full lifecycle init."""

    current_context = None
    _phase_index = 0
    _completed_phases: list = []
    phases: list = []

    def _get_phase_index(self, name: str) -> None:
        return None

    def update_context(self, ctx: object) -> None:
        pass


class TestLifecycleSessionMergeIntegration:
    """Tests that lifecycle calls merge_session_to_host() during device execution."""

    @pytest.mark.asyncio
    async def test_lifecycle_calls_merge_session_to_host(
        self,
        temp_overlays: Path,
    ) -> None:
        """Lifecycle should call merge_session_to_host after applying effects."""
        from unittest.mock import AsyncMock, MagicMock

        # Create mock device with merge_session_to_host method
        mock_device = MagicMock()
        mock_device.merge_session_to_host = MagicMock(return_value=[])

        # Mock sandbox
        mock_sandbox = MagicMock()
        mock_sandbox.context_states = {}
        mock_sandbox.overlays = {}

        # Mock execution result
        mock_result = MagicMock()
        mock_result.metadata = {}

        # Mock effect bundle
        mock_bundle = MagicMock()
        mock_bundle.lifecycle_effects = []
        mock_bundle.context_effects = {}

        # Set up async mocks
        mock_device.create_sandbox = AsyncMock(return_value=mock_sandbox)
        mock_device.execute = AsyncMock(return_value=mock_result)
        mock_device.extract_effects = AsyncMock(return_value=mock_bundle)
        mock_device.cleanup = AsyncMock()

        # Create a mock lifecycle with the device
        # Create mock scope and provider
        mock_scope = MagicMock()
        mock_scope.emit = MagicMock()

        mock_provider = MagicMock()
        mock_provider.provider_id = "test-provider"

        lifecycle = ExecutionLifecycle(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test_task",
        )
        lifecycle._bindings = []
        # _execute_on_device accesses _pipeline for cache checks; provide a
        # minimal stub so the cache path is skipped (current_context=None).
        lifecycle._pipeline = _NullPipeline()

        # Call _execute_on_device directly with mock device
        await lifecycle._execute_on_device(mock_device, "test prompt")

        # Verify merge_session_to_host was called
        mock_device.merge_session_to_host.assert_called_once_with(mock_sandbox)

    @pytest.mark.asyncio
    async def test_lifecycle_handles_merge_failure_gracefully(
        self,
        temp_overlays: Path,
    ) -> None:
        """Lifecycle should not fail if merge_session_to_host raises an exception."""
        from unittest.mock import AsyncMock, MagicMock

        # Create mock device where merge_session_to_host raises
        mock_device = MagicMock()
        mock_device.merge_session_to_host = MagicMock(side_effect=Exception("Merge failed"))

        mock_sandbox = MagicMock()
        mock_sandbox.context_states = {}
        mock_sandbox.overlays = {}

        mock_result = MagicMock()
        mock_result.metadata = {}

        mock_bundle = MagicMock()
        mock_bundle.lifecycle_effects = []
        mock_bundle.context_effects = {}

        mock_device.create_sandbox = AsyncMock(return_value=mock_sandbox)
        mock_device.execute = AsyncMock(return_value=mock_result)
        mock_device.extract_effects = AsyncMock(return_value=mock_bundle)
        mock_device.cleanup = AsyncMock()

        mock_scope = MagicMock()
        mock_scope.emit = MagicMock()

        mock_provider = MagicMock()
        mock_provider.provider_id = "test-provider"

        lifecycle = ExecutionLifecycle(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test_task",
        )
        lifecycle._bindings = []
        lifecycle._pipeline = _NullPipeline()

        # Should not raise despite merge failure
        result = await lifecycle._execute_on_device(mock_device, "test prompt")

        # Verify merge was attempted
        mock_device.merge_session_to_host.assert_called_once()
        # Verify cleanup still happened
        mock_device.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifecycle_skips_merge_for_devices_without_method(
        self,
        temp_overlays: Path,
    ) -> None:
        """Lifecycle should skip merge for devices without merge_session_to_host."""
        from unittest.mock import AsyncMock, MagicMock

        # Create mock device WITHOUT merge_session_to_host
        mock_device = MagicMock(spec=["create_sandbox", "execute", "extract_effects", "cleanup"])

        mock_sandbox = MagicMock()
        mock_sandbox.context_states = {}
        mock_sandbox.overlays = {}

        mock_result = MagicMock()
        mock_result.metadata = {}

        mock_bundle = MagicMock()
        mock_bundle.lifecycle_effects = []
        mock_bundle.context_effects = {}

        mock_device.create_sandbox = AsyncMock(return_value=mock_sandbox)
        mock_device.execute = AsyncMock(return_value=mock_result)
        mock_device.extract_effects = AsyncMock(return_value=mock_bundle)
        mock_device.cleanup = AsyncMock()

        mock_scope = MagicMock()
        mock_scope.emit = MagicMock()

        mock_provider = MagicMock()
        mock_provider.provider_id = "test-provider"

        lifecycle = ExecutionLifecycle(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test_task",
        )
        lifecycle._bindings = []
        lifecycle._pipeline = _NullPipeline()

        # Should not raise - merge is skipped for devices without the method
        result = await lifecycle._execute_on_device(mock_device, "test prompt")

        # Verify cleanup still happened
        mock_device.cleanup.assert_called_once()


class TestSessionMergeSemanticsDocumentation:
    """Tests documenting session merge semantics for containment operations.

    These tests document the CURRENT behavior, not necessarily the ideal behavior.
    See PLAN-session-resumption-containers.md "Remaining Open Questions" #3.
    """

    @pytest.mark.asyncio
    async def test_session_merge_happens_before_scope_decision(self) -> None:
        """Document: Session merge happens during device execution, not scope.merge().

        This means that when a gated task is REJECTED via scope.discard(),
        the session transcript was ALREADY merged to host. This is acceptable
        because:
        1. Forked sessions are NEW IDs (not overwriting parent)
        2. An orphan transcript file is harmless (just disk space)
        3. Alternative would make session resumption never work on success path

        To implement true containment (discard transcripts on rejection) would
        require significant architectural changes - see plan document.
        """
        from unittest.mock import AsyncMock, MagicMock

        # Track when merge is called
        merge_call_order: list[str] = []

        mock_device = MagicMock()

        def track_merge(sandbox):
            merge_call_order.append("merge_session_to_host")
            return []

        mock_device.merge_session_to_host = MagicMock(side_effect=track_merge)

        mock_sandbox = MagicMock()
        mock_sandbox.context_states = {}
        mock_sandbox.overlays = {}

        mock_result = MagicMock()
        mock_result.metadata = {}

        mock_bundle = MagicMock()
        mock_bundle.lifecycle_effects = []
        mock_bundle.context_effects = {}

        mock_device.create_sandbox = AsyncMock(return_value=mock_sandbox)
        mock_device.execute = AsyncMock(return_value=mock_result)
        mock_device.extract_effects = AsyncMock(return_value=mock_bundle)
        mock_device.cleanup = AsyncMock()

        mock_scope = MagicMock()
        mock_scope.emit = MagicMock()

        mock_provider = MagicMock()
        mock_provider.provider_id = "test-provider"

        lifecycle = ExecutionLifecycle(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test_task",
        )
        lifecycle._bindings = []
        lifecycle._pipeline = _NullPipeline()

        # Execute - this is what happens INSIDE the forked scope
        await lifecycle._execute_on_device(mock_device, "test prompt")

        # Document: merge happens DURING device execution
        assert "merge_session_to_host" in merge_call_order

        # If a combinator now calls scope.discard(), the transcript was ALREADY merged
        # This is the documented behavior - see plan "Remaining Open Questions" #3


# =============================================================================
# End-to-End Flow Test
# =============================================================================


class TestSessionResumptionEndToEndFlow:
    """End-to-end test for complete session resumption flow.

    This test validates the entire session resumption lifecycle:
    1. Session created on host → SessionState has session_id and host_cwd
    2. Task executed in container with session context → resumes parent session
    3. Container forks session → new transcript created in overlay
    4. Session overlay merged to host → forked transcript accessible
    5. Subsequent task can resume forked session → conversation continuity

    Note: This test uses mocks to avoid actual API calls while validating
    the complete data flow through all components.
    """

    @pytest.mark.asyncio
    async def test_complete_session_resumption_flow(
        self,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Validate complete session resumption flow from host to container and back."""
        from shepherd_contexts.session.state import SessionState, SessionStateData
        from shepherd_core.types import compute_transcript_path
        from shepherd_runtime.device.container.device import ContainerDevice
        from shepherd_runtime.device.container.podman import ContainerSandbox, OverlayMount

        # === Phase 1: Simulate session created on host ===
        host_cwd = "/Users/alice/my_project"  # Note: underscore to test replacement
        original_session_id = "original-session-abc123"

        # Create SessionCreated effect (as would happen from first execution)
        session_created = SessionCreated(
            session_id=original_session_id,
            context_id="session:new",
            transcript_path=compute_transcript_path(host_cwd, original_session_id),
            cwd=host_cwd,
        )

        # Apply effect to create SessionState (as lifecycle would do)
        session_state = SessionState()
        session_state = session_state.apply_effect(session_created)

        # Verify Phase 1: SessionState has correct fields
        assert session_state.session_id == original_session_id
        assert session_state.host_cwd == host_cwd
        assert session_state.transcript_path is not None
        assert "-my-project" in session_state.transcript_path  # Underscore replaced

        # === Phase 2: Serialize session for container transfer ===
        session_data = session_state.to_state()

        # Verify serialization preserves host_cwd
        assert session_data.session_id == original_session_id
        assert session_data.host_cwd == host_cwd
        assert session_data.transcript_path == session_state.transcript_path

        # Test roundtrip through dict (as happens in JSON serialization)
        data_dict = session_data.to_dict()
        assert data_dict["host_cwd"] == host_cwd
        assert data_dict["session_id"] == original_session_id

        restored_data = SessionStateData.from_dict(data_dict)
        assert restored_data.host_cwd == host_cwd
        assert restored_data.session_id == original_session_id

        # === Phase 3: Container creates session overlay and symlink ===
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create sandbox with mock overlay
        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.context_states = {"session": data_dict}

        # Create session overlay structure
        overlay_dir = temp_overlays / unique_task_id / "session"
        upper_dir = overlay_dir / "upper"
        upper_dir.mkdir(parents=True)

        overlay = OverlayMount(
            task_id=unique_task_id,
            context_name="session",
            lower=temp_overlays,
            upper=upper_dir,
            work=overlay_dir / "work",
            merged=overlay_dir / "merged",
        )
        sandbox.overlays["session"] = overlay
        sandbox._metadata["session_host_cwd"] = host_cwd

        # Simulate container CWD (different from host)
        container_cwd = "/container/workspace"

        # Create symlink for CWD mismatch
        device._create_session_symlink(sandbox, host_cwd, container_cwd)

        # Verify symlink created correctly
        projects_dir = upper_dir / "projects"
        symlink_path = projects_dir / "-container-workspace"
        assert symlink_path.is_symlink()
        # Target should have underscore replaced with dash
        assert str(symlink_path.readlink()) == "-Users-alice-my-project"

        # === Phase 4: Simulate container execution returning forked session ===
        forked_session_id = "forked-session-xyz789"

        # Create forked transcript in overlay (as SDK would do)
        # The SDK writes through the symlink, which resolves to host project folder
        host_project_folder = device._compute_project_folder(host_cwd)
        forked_transcript_dir = upper_dir / "projects" / host_project_folder
        forked_transcript_dir.mkdir(parents=True, exist_ok=True)
        forked_transcript_path = forked_transcript_dir / f"{forked_session_id}.jsonl"
        forked_transcript_path.write_text('{"role": "user", "content": "Continue..."}\n')

        # === Phase 5: Merge session overlay to host ===
        # Create a mock host ~/.claude directory
        mock_host_claude = temp_overlays / "host_claude"
        mock_host_claude.mkdir()

        merged_files = device._merge_session_overlay(sandbox, mock_host_claude)

        # Verify transcript merged (symlink NOT merged)
        assert len(merged_files) == 1
        assert merged_files[0].name == f"{forked_session_id}.jsonl"

        # Symlink should NOT be in host
        host_symlink = mock_host_claude / "projects" / "-container-workspace"
        assert not host_symlink.exists()

        # Transcript SHOULD be in host at correct location
        host_transcript = mock_host_claude / "projects" / host_project_folder / f"{forked_session_id}.jsonl"
        assert host_transcript.exists()
        assert '{"role": "user"' in host_transcript.read_text()

        # === Phase 6: Apply SessionForked effect to update state ===
        session_forked = SessionForked(
            parent_session_id=original_session_id,
            new_session_id=forked_session_id,
            context_id=session_state.context_id,
            transcript_path=str(host_transcript),
        )

        updated_session = session_state.apply_effect(session_forked)

        # Verify state updated correctly
        assert updated_session.session_id == forked_session_id
        assert updated_session.host_cwd == host_cwd  # Preserved from parent!
        assert updated_session.transcript_path == str(host_transcript)

        # === Phase 7: Verify subsequent task can resume forked session ===
        # Serialize updated state for next execution
        next_session_data = updated_session.to_state()

        assert next_session_data.session_id == forked_session_id
        assert next_session_data.host_cwd == host_cwd

        # Build binding for next execution (simulates configure())
        binding = updated_session.configure()

        assert binding.session_id == forked_session_id
        assert binding.session_isolation == "forked"  # Will fork again

        # === Success: Complete flow validated ===
        # The forked session transcript is now on host and can be resumed
        # by any subsequent task (host or container)

    @pytest.mark.asyncio
    async def test_session_flow_with_binding_construction(
        self,
        temp_overlays: Path,
    ) -> None:
        """Validate that _build_binding_from_contexts correctly extracts session info."""
        from shepherd_runtime.device.container.task_runner import _build_binding_from_contexts

        # Simulate context states as they would arrive in container
        contexts = {
            "workspace": {
                "context_type": "workspace",
                "path": "/container/workspace",
                "capabilities": ["read", "write"],
            },
            "session": {
                "context_type": "session",
                "session_id": "parent-session-123",
                "host_cwd": "/Users/alice/project",
                "transcript_path": "/root/.claude/projects/-Users-alice-project/parent-session-123.jsonl",
            },
        }

        binding = _build_binding_from_contexts(contexts, None, None)

        # Verify session info extracted correctly
        assert binding.session_id == "parent-session-123"
        assert binding.session_isolation == "forked"  # ALWAYS fork in container
        assert binding.cwd == "/container/workspace"

    @pytest.mark.asyncio
    async def test_session_validation_in_container_flow(
        self,
        temp_overlays: Path,
    ) -> None:
        """Validate transcript validation provides graceful fallback."""
        from shepherd_core.types import compute_transcript_path
        from shepherd_runtime.device.container.task_runner import _validate_session_resumable

        # Case 1: Transcript exists - should return session_id
        existing_session = "existing-session"
        transcript_path = compute_transcript_path(temp_overlays, existing_session)
        Path(transcript_path).parent.mkdir(parents=True, exist_ok=True)
        Path(transcript_path).write_text('{"test": true}')

        result = _validate_session_resumable(existing_session, str(temp_overlays))
        assert result == existing_session

        # Case 2: Transcript missing - should return None (graceful fallback)
        missing_session = "missing-session"
        result = _validate_session_resumable(missing_session, str(temp_overlays))
        assert result is None

        # Case 3: None session - should return None
        result = _validate_session_resumable(None, str(temp_overlays))
        assert result is None


# These tests drive a real ContainerDevice/PodmanSandboxManager (only
# validate_environment is mocked), so create_sandbox contends for the single
# shared Podman VM and flakes under pytest-xdist. Run them serially via the
# container bucket (`make test_e2e`) rather than in the parallel default loop.
@pytest.mark.container
class TestPreflightValidation:
    """Tests for preflight validation of session configuration."""

    @pytest.mark.asyncio
    async def test_warns_when_session_id_present_but_no_host_cwd(
        self,
        temp_overlays: Path,
        mock_scope: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Preflight warning when session_id exists but host_cwd is missing."""
        import logging

        from shepherd_core.foundation.protocols.device import SandboxConfig
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice(overlays_root=temp_overlays)

        # Create config with session_id but NO host_cwd (legacy session state)
        config = SandboxConfig(
            parent_sandbox_id=None,
            context_states={
                "session": {
                    "context_type": "session",
                    "session_id": "legacy-session-without-host-cwd",
                    # Note: host_cwd is intentionally missing
                }
            },
        )

        # Patch home() and manager to avoid actual overlay creation
        with patch("pathlib.Path.home", return_value=temp_overlays):
            claude_dir = temp_overlays / ".claude"
            claude_dir.mkdir(exist_ok=True)

            with patch.object(device.manager, "validate_environment"), caplog.at_level(logging.WARNING):
                sandbox = await device.create_sandbox(mock_scope, config)

        # Verify warning was logged
        assert any("session_id but no host_cwd" in record.message for record in caplog.records), (
            "Expected warning about missing host_cwd"
        )

        # Cleanup
        await device.cleanup(sandbox)

    @pytest.mark.asyncio
    async def test_no_warning_when_host_cwd_present(
        self,
        temp_overlays: Path,
        mock_scope: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No warning when session has both session_id and host_cwd."""
        import logging

        from shepherd_core.foundation.protocols.device import SandboxConfig
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice(overlays_root=temp_overlays)

        # Create config with both session_id AND host_cwd
        config = SandboxConfig(
            parent_sandbox_id=None,
            context_states={
                "session": {
                    "context_type": "session",
                    "session_id": "session-with-host-cwd",
                    "host_cwd": "/Users/alice/project",
                }
            },
        )

        with patch("pathlib.Path.home", return_value=temp_overlays):
            claude_dir = temp_overlays / ".claude"
            claude_dir.mkdir(exist_ok=True)

            with patch.object(device.manager, "validate_environment"), caplog.at_level(logging.WARNING):
                sandbox = await device.create_sandbox(mock_scope, config)

        # Verify NO warning about host_cwd
        assert not any("session_id but no host_cwd" in record.message for record in caplog.records), (
            "Should not warn when host_cwd is present"
        )

        # Verify host_cwd was stored
        assert sandbox._metadata.get("session_host_cwd") == "/Users/alice/project"

        # Cleanup
        await device.cleanup(sandbox)

    @pytest.mark.asyncio
    async def test_no_warning_when_no_session_id(
        self,
        temp_overlays: Path,
        mock_scope: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No warning when session has no session_id (new session)."""
        import logging

        from shepherd_core.foundation.protocols.device import SandboxConfig
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice(overlays_root=temp_overlays)

        # Create config with NO session_id (will create new session)
        config = SandboxConfig(
            parent_sandbox_id=None,
            context_states={
                "session": {
                    "context_type": "session",
                    # No session_id - this is a fresh session
                }
            },
        )

        with patch("pathlib.Path.home", return_value=temp_overlays):
            claude_dir = temp_overlays / ".claude"
            claude_dir.mkdir(exist_ok=True)

            with patch.object(device.manager, "validate_environment"), caplog.at_level(logging.WARNING):
                sandbox = await device.create_sandbox(mock_scope, config)

        # Verify NO warning (no session_id means nothing to resume)
        assert not any("session_id but no host_cwd" in record.message for record in caplog.records), (
            "Should not warn for new sessions without session_id"
        )

        # Cleanup
        await device.cleanup(sandbox)
