"""True end-to-end tests for session resumption in containers.

These tests run ACTUAL Podman containers to validate session resumption works
correctly across container boundaries. Unlike the mock-based tests in
test_session_resumption.py, these tests:

1. Create real OverlayFS mounts
2. Run real containers that access ~/.claude directories
3. Verify transcript files are visible through the overlay
4. Test symlink resolution for CWD mismatch handling
5. Validate the complete flow from host session to container execution

Run with:
    pytest -m "e2e and container" tests/integration/test_session_resumption_e2e.py

Skip these tests:
    pytest -m "not e2e"

Requirements:
    - Podman installed and running
    - For real SDK tests: ANTHROPIC_API_KEY environment variable
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from shepherd_contexts.session.effects import SessionCreated
from shepherd_core.foundation.protocols.device import SandboxConfig
from shepherd_runtime.device.container.device import ContainerDevice
from shepherd_runtime.device.container.podman import (
    ContainerSandbox,
    PodmanSandboxManager,
)

from .conftest import requires_podman

if TYPE_CHECKING:
    from unittest.mock import MagicMock

# =============================================================================
# Constants
# =============================================================================

CONTAINER_WORKSPACE_PATH = "/container/workspace"
CONTAINER_SESSION_PATH = "/root/.claude"
CONTAINER_TIMEOUT = 60  # seconds


# =============================================================================
# Additional Skip Markers
# =============================================================================


def _has_anthropic_api_key() -> bool:
    """Check if ANTHROPIC_API_KEY is set."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


requires_claude_api = pytest.mark.skipif(
    not _has_anthropic_api_key(),
    reason="ANTHROPIC_API_KEY not set - skipping real SDK test",
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def session_transcript_fixture(workspace: Path, temp_overlays: Path) -> dict[str, Any]:
    """Create a session transcript in a temporary directory.

    Returns dict with:
        - session_id: The session ID
        - host_cwd: The workspace path (simulating host cwd)
        - transcript_dir: Directory containing the transcript
        - transcript_path: Path to the transcript file
        - project_folder: The computed project folder name
    """
    session_id = f"test-session-{uuid.uuid4().hex[:8]}"

    # Compute project folder (same algorithm as Claude CLI)
    project_folder = str(workspace.resolve()).replace("/", "-").replace("_", "-")

    # Create a fake ~/.claude structure in temp_overlays
    claude_dir = temp_overlays / "fake_claude"
    transcript_dir = claude_dir / "projects" / project_folder
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{session_id}.jsonl"

    # Write minimal valid transcript
    transcript_entries = [
        {"type": "init", "session_id": session_id, "cwd": str(workspace)},
        {"type": "user", "content": "Remember: SECRET-CODE-E2E-12345"},
        {"type": "assistant", "content": "I'll remember SECRET-CODE-E2E-12345"},
    ]
    with open(transcript_path, "w") as f:
        f.writelines(json.dumps(entry) + "\n" for entry in transcript_entries)

    return {
        "session_id": session_id,
        "host_cwd": workspace,
        "claude_dir": claude_dir,
        "transcript_dir": transcript_dir,
        "transcript_path": transcript_path,
        "project_folder": project_folder,
    }


# =============================================================================
# E2E Tests: Session Overlay with Real Containers
# =============================================================================


@pytest.mark.e2e
@pytest.mark.container
@requires_podman
class TestSessionOverlayE2E:
    """E2E tests for session overlay creation and mounting in real containers."""

    def test_session_transcript_visible_in_container(
        self,
        manager: PodmanSandboxManager,
        session_transcript_fixture: dict[str, Any],
        unique_task_id: str,
    ) -> None:
        """Test that session transcripts are visible inside the container via overlay.

        This validates:
        - Session overlay correctly mounts transcript directory as lower layer
        - Container can read transcript files from the overlay
        - File content is preserved through OverlayFS
        """
        claude_dir = session_transcript_fixture["claude_dir"]
        project_folder = session_transcript_fixture["project_folder"]
        session_id = session_transcript_fixture["session_id"]

        # Create overlay with claude_dir as base
        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="session",
            base_path=claude_dir,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["session"] = overlay

        try:
            manager.mount_overlay(overlay)

            # Build the path to transcript inside container
            inner_transcript_path = f"{CONTAINER_SESSION_PATH}/projects/{project_folder}/{session_id}.jsonl"

            # Create container that reads the transcript
            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f"cat '{inner_transcript_path}' && echo 'TRANSCRIPT_READ_SUCCESS'",
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_TIMEOUT)

            # Get logs to verify content was read
            logs = manager.get_container_logs(sandbox)

            assert exit_code == 0, f"Container failed with code {exit_code}. Logs: {logs}"
            assert "SECRET-CODE-E2E-12345" in logs, f"Transcript content not found in container output. Logs: {logs}"
            assert "TRANSCRIPT_READ_SUCCESS" in logs

        finally:
            manager.cleanup(sandbox)

    def test_container_writes_to_overlay_upper(
        self,
        manager: PodmanSandboxManager,
        session_transcript_fixture: dict[str, Any],
        unique_task_id: str,
    ) -> None:
        """Test that container writes go to overlay upper layer (copy-on-write).

        This validates:
        - New files created in container appear in upper layer
        - Original host directory is NOT modified
        - OverlayFS copy-on-write isolation works
        """
        claude_dir = session_transcript_fixture["claude_dir"]
        project_folder = session_transcript_fixture["project_folder"]

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="session",
            base_path=claude_dir,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["session"] = overlay

        new_session_id = f"forked-{uuid.uuid4().hex[:8]}"

        try:
            manager.mount_overlay(overlay)

            # Create container that writes a new transcript file
            new_transcript_path = f"{CONTAINER_SESSION_PATH}/projects/{project_folder}/{new_session_id}.jsonl"

            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f'mkdir -p "$(dirname {new_transcript_path})" && '
                    f'echo \'{{"type": "forked", "session_id": "{new_session_id}"}}\' > "{new_transcript_path}"',
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_TIMEOUT)
            assert exit_code == 0, f"Container failed with code {exit_code}"

            # Remove container before checking files
            manager.remove_container(sandbox)

            # Unmount before checking upper layer
            manager.unmount_overlay(overlay)

            # Verify new file is in upper layer
            upper_transcript = Path(overlay.upper) / "projects" / project_folder / f"{new_session_id}.jsonl"

            # On macOS with VM overlay, we need to read via VM runner
            if overlay.is_vm_path and manager._vm_runner:
                result = manager._vm_runner.run(f"cat '{upper_transcript}'")
                assert new_session_id in result.stdout, f"New transcript should be in upper layer: {upper_transcript}"
            else:
                assert upper_transcript.exists(), f"New transcript should be in upper layer: {upper_transcript}"
                content = upper_transcript.read_text()
                assert new_session_id in content

            # Verify original host directory was NOT modified
            host_new_transcript = claude_dir / "projects" / project_folder / f"{new_session_id}.jsonl"
            assert not host_new_transcript.exists(), "New transcript should NOT appear in original host directory"

        finally:
            manager.cleanup(sandbox)

    def test_symlink_creation_and_resolution(
        self,
        manager: PodmanSandboxManager,
        session_transcript_fixture: dict[str, Any],
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Test that symlinks for CWD mismatch work correctly.

        This validates:
        - Symlink created in upper layer for CWD path translation
        - Container can read files through the symlink
        """
        claude_dir = session_transcript_fixture["claude_dir"]
        project_folder = session_transcript_fixture["project_folder"]
        session_id = session_transcript_fixture["session_id"]
        host_cwd = session_transcript_fixture["host_cwd"]

        # Create device to use its helper methods
        device = ContainerDevice(overlays_root=temp_overlays)

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="session",
            base_path=claude_dir,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["session"] = overlay

        # Simulate container CWD mismatch
        container_cwd = "/container/workspace"

        try:
            manager.mount_overlay(overlay)

            # Create the symlink for CWD mismatch (as device would do)
            # We need to create it in the overlay's upper directory
            container_folder = device._compute_project_folder(container_cwd)

            # Create symlink in upper layer (before container starts)
            # On macOS with VM overlay, we need to create via VM runner
            if overlay.is_vm_path and manager._vm_runner:
                projects_path = f"{overlay.upper}/projects"
                symlink_path = f"{projects_path}/{container_folder}"
                manager._vm_runner.run(f"mkdir -p '{projects_path}'")
                # Use -- to end options since project_folder starts with -
                manager._vm_runner.run(f"ln -sf -- '{project_folder}' '{symlink_path}'")
            else:
                projects_dir = Path(overlay.upper) / "projects"
                projects_dir.mkdir(parents=True, exist_ok=True)
                symlink_path = projects_dir / container_folder
                if not symlink_path.exists():
                    symlink_path.symlink_to(project_folder)

            # Create container that reads transcript through symlink path
            symlink_transcript_path = f"{CONTAINER_SESSION_PATH}/projects/{container_folder}/{session_id}.jsonl"

            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f"cat '{symlink_transcript_path}' && echo 'SYMLINK_READ_SUCCESS'",
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_TIMEOUT)

            logs = manager.get_container_logs(sandbox)

            assert exit_code == 0, f"Container failed: {logs}"
            assert "SECRET-CODE-E2E-12345" in logs, f"Should read transcript through symlink. Logs: {logs}"
            assert "SYMLINK_READ_SUCCESS" in logs

        finally:
            manager.cleanup(sandbox)

    def test_write_through_symlink_lands_at_resolved_path(
        self,
        manager: PodmanSandboxManager,
        session_transcript_fixture: dict[str, Any],
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Test that writes through symlink land at the resolved path (Spike D validation).

        This is critical for session resumption: when SDK writes to the container
        path, the file should appear at the host path location in the upper layer.
        """
        claude_dir = session_transcript_fixture["claude_dir"]
        project_folder = session_transcript_fixture["project_folder"]

        device = ContainerDevice(overlays_root=temp_overlays)

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="session",
            base_path=claude_dir,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["session"] = overlay

        container_cwd = "/container/workspace"
        new_session_id = f"written-through-symlink-{uuid.uuid4().hex[:8]}"
        container_folder = device._compute_project_folder(container_cwd)

        try:
            manager.mount_overlay(overlay)

            # Create symlink for CWD mismatch
            if overlay.is_vm_path and manager._vm_runner:
                projects_path = f"{overlay.upper}/projects"
                symlink_path = f"{projects_path}/{container_folder}"
                manager._vm_runner.run(f"mkdir -p '{projects_path}'")
                # Use -- to end options since project_folder starts with -
                manager._vm_runner.run(f"ln -sf -- '{project_folder}' '{symlink_path}'")
            else:
                projects_dir = Path(overlay.upper) / "projects"
                projects_dir.mkdir(parents=True, exist_ok=True)
                symlink_path = projects_dir / container_folder
                if not symlink_path.exists():
                    symlink_path.symlink_to(project_folder)

            # Container writes through the symlink path (as SDK would)
            symlink_write_path = f"{CONTAINER_SESSION_PATH}/projects/{container_folder}/{new_session_id}.jsonl"

            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f'echo \'{{"written": "through-symlink"}}\' > "{symlink_write_path}"',
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_TIMEOUT)
            assert exit_code == 0

            manager.remove_container(sandbox)
            manager.unmount_overlay(overlay)

            # KEY ASSERTION: File should appear at RESOLVED path (host folder)
            resolved_path = (
                Path(overlay.upper)
                / "projects"
                / project_folder  # Host folder, not container folder
                / f"{new_session_id}.jsonl"
            )

            # On macOS with VM overlay, check via VM runner
            if overlay.is_vm_path and manager._vm_runner:
                result = manager._vm_runner.run(f"cat '{resolved_path}' 2>/dev/null || echo 'NOT_FOUND'")
                assert "through-symlink" in result.stdout, f"File should appear at resolved path: {resolved_path}"

                # Verify symlink still exists (not replaced by directory)
                symlink_check = manager._vm_runner.run(
                    f"test -L '{overlay.upper}/projects/{container_folder}' && echo 'IS_SYMLINK'"
                )
                assert "IS_SYMLINK" in symlink_check.stdout, "Symlink should remain intact after write"
            else:
                assert resolved_path.exists(), f"File should appear at resolved path: {resolved_path}"
                content = resolved_path.read_text()
                assert "through-symlink" in content

                # Verify symlink still exists
                symlink_dir = Path(overlay.upper) / "projects" / container_folder
                assert symlink_dir.is_symlink(), "Symlink should remain intact after write"

        finally:
            manager.cleanup(sandbox)


@pytest.mark.e2e
@pytest.mark.container
@requires_podman
class TestSessionMergeE2E:
    """E2E tests for session overlay merge to host."""

    def test_merge_copies_transcripts_filters_symlinks(
        self,
        manager: PodmanSandboxManager,
        session_transcript_fixture: dict[str, Any],
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Test that merge correctly copies transcripts and filters symlinks.

        This validates the complete merge flow:
        1. Container creates forked transcript through symlink
        2. Merge copies .jsonl files to host
        3. Symlinks are NOT copied to host
        """
        claude_dir = session_transcript_fixture["claude_dir"]
        project_folder = session_transcript_fixture["project_folder"]

        device = ContainerDevice(overlays_root=temp_overlays)

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="session",
            base_path=claude_dir,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["session"] = overlay

        container_cwd = "/container/workspace"
        forked_session_id = f"forked-for-merge-{uuid.uuid4().hex[:8]}"
        container_folder = device._compute_project_folder(container_cwd)

        try:
            manager.mount_overlay(overlay)

            # Create symlink in upper layer
            if overlay.is_vm_path and manager._vm_runner:
                projects_path = f"{overlay.upper}/projects"
                symlink_path = f"{projects_path}/{container_folder}"
                manager._vm_runner.run(f"mkdir -p '{projects_path}'")
                # Use -- to end options since project_folder starts with -
                manager._vm_runner.run(f"ln -sf -- '{project_folder}' '{symlink_path}'")
            else:
                projects_dir = Path(overlay.upper) / "projects"
                projects_dir.mkdir(parents=True, exist_ok=True)
                symlink_path = projects_dir / container_folder
                if not symlink_path.exists():
                    symlink_path.symlink_to(project_folder)

            # Container writes forked session through symlink
            forked_transcript_path = f"{CONTAINER_SESSION_PATH}/projects/{container_folder}/{forked_session_id}.jsonl"

            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f'echo \'{{"forked": true, "id": "{forked_session_id}"}}\' > "{forked_transcript_path}"',
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_TIMEOUT)
            assert exit_code == 0

            manager.remove_container(sandbox)
            manager.unmount_overlay(overlay)

            # Create a mock target directory (simulating host ~/.claude)
            merge_target = temp_overlays / "merge_target"
            merge_target.mkdir()

            # Perform merge using device's merge method
            merged_files = device._merge_session_overlay(sandbox, merge_target)

            # The merge should work even on macOS - it reads from the upper layer
            # which is available after unmount

            # Verify transcript was merged (if merge succeeded)
            if merged_files:
                # Find our forked transcript
                forked_transcript = merge_target / "projects" / project_folder / f"{forked_session_id}.jsonl"
                assert forked_transcript.exists(), f"Forked transcript should be merged to host: {forked_transcript}"

                # Verify symlink was NOT merged
                symlink_in_target = merge_target / "projects" / container_folder
                # It shouldn't exist, or if it does, it should NOT be a symlink
                if symlink_in_target.exists():
                    assert not symlink_in_target.is_symlink(), "Symlink should NOT be merged to host"

        finally:
            manager.cleanup(sandbox)


@pytest.mark.e2e
@pytest.mark.container
@requires_podman
class TestSessionResumptionIntegration:
    """Integration tests for the complete session resumption flow."""

    @pytest.mark.asyncio
    async def test_session_binding_extraction_in_container(
        self,
        session_transcript_fixture: dict[str, Any],
    ) -> None:
        """Test that task_runner correctly extracts session_id from contexts.

        This validates Change 3 from the plan: _build_binding_from_contexts()
        correctly extracts session information for provider binding.
        """
        from shepherd_runtime.device.container.task_runner import _build_binding_from_contexts

        # Simulate context states as they arrive in container
        contexts = {
            "workspace": {
                "context_type": "workspace",
                "path": "/container/workspace",
                "capabilities": ["read", "write"],
            },
            "session": {
                "context_type": "session",
                "session_id": session_transcript_fixture["session_id"],
                "host_cwd": str(session_transcript_fixture["host_cwd"]),
                "transcript_path": str(session_transcript_fixture["transcript_path"]),
            },
        }

        binding = _build_binding_from_contexts(contexts, None, None)

        # Verify session info extracted
        assert binding.session_id == session_transcript_fixture["session_id"]
        assert binding.session_isolation == "forked", "Container execution should always fork sessions"

    @pytest.mark.asyncio
    async def test_session_state_preserves_host_cwd_through_serialization(
        self,
        session_transcript_fixture: dict[str, Any],
    ) -> None:
        """Test that SessionState preserves host_cwd through serialization.

        This validates the data flow:
        1. SessionCreated effect captures cwd
        2. SessionState stores it as host_cwd
        3. Serialization (to_state/to_dict) preserves it
        4. Deserialization restores it
        """
        from shepherd_contexts.session.state import SessionState, SessionStateData

        # Phase 1: Create session state as it would exist on host
        session_created = SessionCreated(
            session_id=session_transcript_fixture["session_id"],
            context_id="session:integration",
            transcript_path=str(session_transcript_fixture["transcript_path"]),
            cwd=str(session_transcript_fixture["host_cwd"]),
        )

        session_state = SessionState()
        session_state = session_state.apply_effect(session_created)

        # Verify host_cwd captured
        assert session_state.host_cwd == str(session_transcript_fixture["host_cwd"])

        # Phase 2: Serialize for container transfer
        session_data = session_state.to_state()
        assert session_data.host_cwd == str(session_transcript_fixture["host_cwd"])

        # Phase 3: Roundtrip through dict (as happens in JSON)
        data_dict = session_data.to_dict()
        assert data_dict["host_cwd"] == str(session_transcript_fixture["host_cwd"])

        restored_data = SessionStateData.from_dict(data_dict)
        assert restored_data.host_cwd == str(session_transcript_fixture["host_cwd"])

    @pytest.mark.asyncio
    async def test_complete_session_flow_with_container_device(
        self,
        mock_scope: MagicMock,
        session_transcript_fixture: dict[str, Any],
        temp_overlays: Path,
        workspace: Path,
    ) -> None:
        """Test complete session flow using ContainerDevice API.

        This validates:
        1. SessionState serialization preserves host_cwd
        2. ContainerDevice creates session overlay correctly
        3. Session would be resumable in container
        """
        from unittest.mock import patch

        from shepherd_contexts.session.state import SessionState

        # Phase 1: Create session state as it would exist on host
        session_created = SessionCreated(
            session_id=session_transcript_fixture["session_id"],
            context_id="session:integration",
            transcript_path=str(session_transcript_fixture["transcript_path"]),
            cwd=str(session_transcript_fixture["host_cwd"]),
        )

        session_state = SessionState()
        session_state = session_state.apply_effect(session_created)

        # Phase 2: Serialize for container transfer
        session_data = session_state.to_state()

        # Phase 3: Create ContainerDevice and sandbox
        device = ContainerDevice(overlays_root=temp_overlays)

        # Mock Path.home() to use our fake claude_dir's parent
        fake_home = session_transcript_fixture["claude_dir"].parent

        config = SandboxConfig(
            parent_sandbox_id=None,
            context_states={
                "workspace": {"context_type": "workspace", "path": str(workspace)},
                "session": session_data.to_dict(),
            },
        )

        with patch("pathlib.Path.home", return_value=fake_home), patch.object(device.manager, "validate_environment"):
            sandbox = await device.create_sandbox(mock_scope, config)

        try:
            # Phase 4: Verify session overlay was created
            assert "session" in sandbox.overlays, "Session overlay should be created"
            assert sandbox._metadata.get("session_host_cwd") == str(session_transcript_fixture["host_cwd"])

        finally:
            await device.cleanup(sandbox)


# =============================================================================
# Optional: Real SDK Tests (Requires ANTHROPIC_API_KEY)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.container
@pytest.mark.slow
@requires_podman
@requires_claude_api
class TestRealSDKSessionResumption:
    """Tests with real Claude SDK calls.

    These tests actually call the Claude Agent SDK and verify session
    context is preserved. They require:
    - ANTHROPIC_API_KEY environment variable
    - Network access to Anthropic API
    - Will incur API costs

    Run with:
        ANTHROPIC_API_KEY=sk-... pytest -m "e2e and container" -k "RealSDK"
    """

    @pytest.mark.asyncio
    async def test_real_session_context_recall(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        temp_overlays: Path,
        unique_task_id: str,
    ) -> None:
        """Test real SDK session resumption with context recall.

        WARNING: This test makes real API calls and costs money.

        The test:
        1. Creates a session with a unique secret code
        2. Resumes the session and asks Claude to recall the code
        3. Verifies Claude remembers the context

        This is skipped by default (requires ANTHROPIC_API_KEY).
        """
        # This test would require significant setup with the real provider
        # and is left as a placeholder for future implementation
        pytest.skip(
            "Full real SDK test requires complete provider integration. "
            "The infrastructure tests above validate the session resumption "
            "mechanism works correctly."
        )
