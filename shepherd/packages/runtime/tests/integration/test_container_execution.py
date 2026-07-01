"""Integration tests for container device execution.

Tests the complete flow of executing tasks in containers:
- Sandbox creation with overlays
- Task input/output serialization
- Effect extraction from overlays
- Causality linking

These tests require Podman to be installed and running.
Use `pytest -m container` to run only container tests.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from shepherd_core.foundation.protocols.device import (
    ExecutionResult,
    SandboxConfig,
)
from shepherd_runtime.device.container.device import ContainerDevice
from shepherd_runtime.device.container.effect_collector import EffectCollector
from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
from shepherd_runtime.device.container.podman import (
    ContainerSandbox,
    OverlayMount,
    PodmanSandboxManager,
)

# =============================================================================
# Fixtures
# =============================================================================


# Skip marker for tests requiring Podman
requires_podman = pytest.mark.usefixtures("_requires_podman")

# Custom marker for container tests
pytestmark = pytest.mark.container


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="shepherd-test-workspace-")
    yield Path(workspace)
    shutil.rmtree(workspace, ignore_errors=True)


_OVERLAY_TMPFS = Path("/tmp/shepherd-test-overlays")


@pytest.fixture
def temp_overlays():
    """Create a temporary overlays directory."""
    base = _OVERLAY_TMPFS if _OVERLAY_TMPFS.is_mount() else Path(tempfile.gettempdir())
    overlays = tempfile.mkdtemp(prefix="shepherd-test-overlays-", dir=base)
    yield Path(overlays)
    shutil.rmtree(overlays, ignore_errors=True)


@pytest.fixture
def mock_scope():
    """Create a mock scope for testing."""
    scope = MagicMock()
    scope.id = "test-scope"
    return scope


# =============================================================================
# Unit Tests for Container Components
# =============================================================================


class TestContainerSandbox:
    """Tests for ContainerSandbox handle."""

    def test_create_sandbox(self):
        """Test creating a sandbox handle."""
        sandbox = ContainerSandbox.create("task-123")

        assert sandbox.sandbox_id == "task-123"
        assert sandbox.device_name == "container"
        assert sandbox.container_id is None
        assert sandbox.overlays == {}

    def test_sandbox_with_overlays(self):
        """Test sandbox stores overlay configuration."""
        sandbox = ContainerSandbox.create()

        overlay = OverlayMount(
            task_id=sandbox.sandbox_id,
            context_name="workspace",
            lower=Path("/lower"),
            upper=Path("/upper"),
            work=Path("/work"),
            merged=Path("/merged"),
        )
        sandbox.overlays["workspace"] = overlay

        assert "workspace" in sandbox.overlays
        assert sandbox.overlays["workspace"].context_name == "workspace"


class TestOverlayMount:
    """Tests for OverlayMount configuration."""

    def test_mount_options(self):
        """Test mount options string generation."""
        overlay = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=Path("/a/lower"),
            upper=Path("/a/upper"),
            work=Path("/a/work"),
            merged=Path("/a/merged"),
        )

        opts = overlay.mount_options
        assert "lowerdir=/a/lower" in opts
        assert "upperdir=/a/upper" in opts
        assert "workdir=/a/work" in opts


class TestEffectCollector:
    """Tests for EffectCollector functionality."""

    def test_emit_and_collect(self):
        """Test emitting and collecting effects."""
        collector = EffectCollector(_id="test-collector")

        # Create mock effects
        effect1 = MagicMock()
        effect1.effect_type = "task_started"
        effect1.model_dump = lambda: {"effect_type": "task_started"}

        effect2 = MagicMock()
        effect2.effect_type = "tool_call_completed"
        effect2.tool_call_id = "tc-123"
        effect2.model_dump = lambda: {"effect_type": "tool_call_completed", "tool_call_id": "tc-123"}

        collector.emit(effect1)
        collector.emit(effect2)

        assert len(collector) == 2
        assert collector.get_last_completed_intent_id() == "tc-123"

    def test_lifecycle_vs_intent_effects(self):
        """Test effect classification."""
        collector = EffectCollector()

        # Intent effect
        intent = MagicMock()
        intent.effect_type = "tool_call_completed"
        intent.tool_call_id = "tc-1"
        intent.model_dump = lambda: {"effect_type": "tool_call_completed"}

        # Lifecycle effect
        lifecycle = MagicMock()
        lifecycle.effect_type = "task_started"
        lifecycle.model_dump = lambda: {"effect_type": "task_started"}

        collector.emit(intent)
        collector.emit(lifecycle)

        assert len(collector.get_intent_effects()) == 1
        assert len(collector.get_lifecycle_effects()) == 1

    def test_serialize_deserialize(self):
        """Test serialization roundtrip."""
        collector = EffectCollector(_id="test")
        collector._last_completed_intent_id = "tc-abc"

        # Mock effect that can be serialized
        effect = MagicMock()
        effect.effect_type = "task_started"
        effect.model_dump = lambda: {"effect_type": "task_started"}
        collector._collected_effects.append(effect)

        data = collector.serialize_for_transport()

        assert data["collector_id"] == "test"
        assert data["last_completed_intent_id"] == "tc-abc"
        assert len(data["effects"]) == 1


class TestOverlayEffectExtractor:
    """Tests for OverlayEffectExtractor."""

    def test_extract_from_empty_upper(self, temp_overlays):
        """Test extraction from empty overlay."""
        overlay = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=temp_overlays / "lower",
            upper=temp_overlays / "upper",
            work=temp_overlays / "work",
            merged=temp_overlays / "merged",
        )

        # Create empty directories
        overlay.lower.mkdir(parents=True)
        overlay.upper.mkdir(parents=True)
        overlay.merged.mkdir(parents=True)

        extractor = OverlayEffectExtractor()
        collector = EffectCollector()

        effects = extractor.extract(overlay, collector)
        assert effects == []

    def test_extract_new_file(self, temp_overlays):
        """Test extraction of new file creation."""
        overlay = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=temp_overlays / "lower",
            upper=temp_overlays / "upper",
            work=temp_overlays / "work",
            merged=temp_overlays / "merged",
        )

        # Create directories
        overlay.lower.mkdir(parents=True)
        overlay.upper.mkdir(parents=True)
        overlay.merged.mkdir(parents=True)

        # Create a new file in upper layer
        new_file = overlay.upper / "hello.py"
        new_file.write_text("print('hello')")

        extractor = OverlayEffectExtractor()
        collector = EffectCollector()
        collector._last_completed_intent_id = "tc-123"

        effects = extractor.extract(overlay, collector)

        assert len(effects) == 1
        assert effects[0].path == "hello.py"
        assert effects[0].caused_by == "tc-123"

    def test_extract_modified_file(self, temp_overlays):
        """Test extraction of file modification."""
        overlay = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=temp_overlays / "lower",
            upper=temp_overlays / "upper",
            work=temp_overlays / "work",
            merged=temp_overlays / "merged",
        )

        # Create directories
        overlay.lower.mkdir(parents=True)
        overlay.upper.mkdir(parents=True)
        overlay.merged.mkdir(parents=True)

        # Create file in lower layer (original)
        (overlay.lower / "file.txt").write_text("original")

        # Create modified version in upper layer
        (overlay.upper / "file.txt").write_text("modified")

        extractor = OverlayEffectExtractor()
        collector = EffectCollector()

        effects = extractor.extract(overlay, collector)

        assert len(effects) == 1
        # Should be a FilePatch since file existed in lower
        from shepherd_core.effects import FilePatch

        assert isinstance(effects[0], FilePatch)


class TestPodmanSandboxManager:
    """Tests for PodmanSandboxManager."""

    def test_create_overlay_structure(self, temp_overlays):
        """Test overlay directory structure creation."""
        manager = PodmanSandboxManager(overlays_root=temp_overlays)

        # Use temp_overlays as base_path since it's under VirtioFS (via /var/folders)
        base_path = temp_overlays / "base"
        base_path.mkdir(parents=True, exist_ok=True)

        overlay = manager.create_overlay(
            task_id="task-123",
            context_name="workspace",
            base_path=base_path,
        )

        assert overlay.task_id == "task-123"
        assert overlay.context_name == "workspace"

        # On macOS with VM overlay, directories are in VM, not on host
        if overlay.is_vm_path:
            # Verify via VM runner
            assert manager._vm_runner is not None
            assert manager._vm_runner.exists(overlay.upper)
            assert manager._vm_runner.exists(overlay.work)
            assert manager._vm_runner.exists(overlay.merged)
        else:
            # Linux: directories are on host
            assert overlay.upper.exists()
            assert overlay.work.exists()
            assert overlay.merged.exists()

    @requires_podman
    def test_is_podman_available(self, temp_overlays):
        """Test Podman availability check."""
        manager = PodmanSandboxManager(overlays_root=temp_overlays)
        assert manager.is_podman_available() is True

    def test_is_podman_available_when_missing(self, temp_overlays):
        """Test Podman check when not installed."""
        manager = PodmanSandboxManager(overlays_root=temp_overlays)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            assert manager.is_podman_available() is False


# =============================================================================
# Integration Tests for ContainerDevice
# =============================================================================


class TestContainerDeviceIntegration:
    """Integration tests for ContainerDevice."""

    @pytest.mark.asyncio
    async def test_create_sandbox_creates_overlays(self, mock_scope, temp_overlays):
        """Test sandbox creation sets up overlay structure."""
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create a real workspace path under temp_overlays (which is under VirtioFS on macOS)
        workspace_path = temp_overlays / "test_workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Mock validation to avoid needing Podman
        with patch.object(device.manager, "validate_environment"):
            config = SandboxConfig(
                context_states={
                    "workspace": MagicMock(path=str(workspace_path)),
                },
            )

            sandbox = await device.create_sandbox(mock_scope, config)

            assert sandbox.sandbox_id is not None
            assert sandbox.task_dir is not None
            assert sandbox.task_dir.exists()

    @pytest.mark.asyncio
    async def test_cleanup_removes_resources(self, mock_scope, temp_overlays):
        """Test cleanup removes sandbox resources."""
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create sandbox manually
        sandbox = ContainerSandbox.create("cleanup-test")
        task_dir = temp_overlays / "cleanup-test" / "task"
        task_dir.mkdir(parents=True)
        sandbox.task_dir = task_dir

        await device.cleanup(sandbox)

        # Note: cleanup sets sandbox.task_dir to None, so check original path
        assert not task_dir.exists()

    @pytest.mark.asyncio
    async def test_extract_effects_from_empty_sandbox(self, temp_overlays):
        """Test effect extraction from sandbox with no changes."""
        device = ContainerDevice(overlays_root=temp_overlays)

        sandbox = ContainerSandbox.create("extract-test")
        result = ExecutionResult(
            success=True,
            metadata={"_collector": None},
        )

        bundle = await device.extract_effects(sandbox, result)

        assert bundle.context_effects == {}
        # ContainerExecutionCompleted is always emitted for parent tracking
        assert len(bundle.lifecycle_effects) == 1
        from shepherd_core.effects import ContainerExecutionCompleted

        assert isinstance(bundle.lifecycle_effects[0], ContainerExecutionCompleted)

    @pytest.mark.asyncio
    async def test_extract_effects_with_collector(self, temp_overlays):
        """Test effect extraction includes collector effects."""
        import time

        device = ContainerDevice(overlays_root=temp_overlays)

        sandbox = ContainerSandbox.create("extract-collector-test")

        # Create mock collector data with proper timestamp format (numeric)
        collector_data = {
            "collector_id": "test",
            "last_completed_intent_id": "tc-1",
            "effects": [
                {"effect_type": "task_started", "timestamp": time.time()},
            ],
        }

        result = ExecutionResult(
            success=True,
            metadata={"_collector": collector_data},
        )

        bundle = await device.extract_effects(sandbox, result)

        # Should have lifecycle effects from collector + ContainerExecutionCompleted
        assert len(bundle.lifecycle_effects) == 2
        from shepherd_core.effects import ContainerExecutionCompleted

        # Last effect should be ContainerExecutionCompleted (added by extract_effects)
        assert isinstance(bundle.lifecycle_effects[-1], ContainerExecutionCompleted)


# =============================================================================
# End-to-End Tests (Require Podman)
# =============================================================================


@requires_podman
class TestContainerDeviceE2E:
    """End-to-end tests requiring Podman."""

    @pytest.mark.asyncio
    async def test_full_sandbox_lifecycle(self, mock_scope, temp_workspace, temp_overlays):
        """Test complete sandbox lifecycle: create, execute, extract, cleanup."""
        # Create a file in workspace
        (temp_workspace / "existing.txt").write_text("existing content")

        device = ContainerDevice(
            overlays_root=temp_overlays,
            image="python:3.12-slim",
        )

        # Create sandbox
        config = SandboxConfig(
            context_states={
                "workspace": MagicMock(path=str(temp_workspace)),
            },
        )

        try:
            sandbox = await device.create_sandbox(mock_scope, config)
            assert sandbox.sandbox_id is not None
            assert sandbox.task_dir.exists()

            # Verify overlay was created for workspace
            assert "workspace" in sandbox.overlays
            overlay = sandbox.overlays["workspace"]

            # On macOS with VM overlay, merged is a VM path
            if overlay.is_vm_path:
                # Verify via VM runner
                assert device.manager._vm_runner is not None
                assert device.manager._vm_runner.exists(overlay.merged)
            else:
                # Linux: merged is on host
                assert overlay.merged.exists()

        finally:
            await device.cleanup(sandbox)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_container_execution_with_mock_provider(self, mock_scope, temp_workspace, temp_overlays):
        """Test container execution with mock provider output.

        This test:
        1. Creates a sandbox
        2. Manually writes task input
        3. Simulates container creating a file (if possible)
        4. Extracts effects

        Note: Does not actually run a container - tests the orchestration.
        On macOS with VM overlay, we can't write to the upper layer from the host,
        so we use VM runner to create the file.
        """
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create sandbox with mocked validation
        with patch.object(device.manager, "validate_environment"):
            config = SandboxConfig(
                context_states={
                    "workspace": MagicMock(path=str(temp_workspace)),
                },
            )
            sandbox = await device.create_sandbox(mock_scope, config)

        try:
            # Simulate what would happen after container execution:
            # - New file created in upper layer
            if "workspace" in sandbox.overlays:
                overlay = sandbox.overlays["workspace"]
                upper = overlay.upper

                if overlay.is_vm_path:
                    # On macOS with VM overlay, write via VM runner
                    assert device.manager._vm_runner is not None
                    device.manager._vm_runner.run(f'echo "# Created by agent" > "{upper}/created_by_agent.py"')
                else:
                    # Linux: write directly on host
                    (upper / "created_by_agent.py").write_text("# Created by agent")

            # Create mock execution result with collector data
            collector = EffectCollector(_id="container-test")
            # Simulate a tool call completion
            from shepherd_core.effects import ToolCallCompleted

            tool_effect = ToolCallCompleted(
                tool_call_id="tc-write-file",
                tool_name="write",
                output="File created",
            )
            collector.emit(tool_effect)

            result = ExecutionResult(
                success=True,
                output_text="Created file",
                metadata={"_collector": collector.serialize_for_transport()},
            )

            # Extract effects
            bundle = await device.extract_effects(sandbox, result)

            # Should have the file creation effect
            if "workspace" in bundle.context_effects:
                workspace_effects = bundle.context_effects["workspace"]
                file_effects = [e for e in workspace_effects if hasattr(e, "path")]
                assert any("created_by_agent.py" in e.path for e in file_effects)

        finally:
            await device.cleanup(sandbox)


# =============================================================================
# Task Runner Tests
# =============================================================================


class TestTaskRunnerIO:
    """Tests for task runner I/O protocol."""

    def test_input_json_format(self, temp_overlays):
        """Test input.json format matches task runner expectations."""
        input_data = {
            "prompt": "Create a hello.py file",
            "provider_config": {
                "provider_type": "claude",
                "model": "claude-sonnet-4-20250514",
            },
            "context_states": {
                "workspace": {
                    "context_type": "workspace",
                    "path": "/container/workspace",
                },
            },
            "tools": None,
            "task_name": "test-task",
        }

        # Write and read back
        input_path = temp_overlays / "input.json"
        input_path.write_text(json.dumps(input_data))

        loaded = json.loads(input_path.read_text())
        assert loaded["prompt"] == "Create a hello.py file"
        assert loaded["provider_config"]["provider_type"] == "claude"

    def test_output_json_format(self, temp_overlays):
        """Test output.json format matches expected structure."""
        output_data = {
            "success": True,
            "result": {
                "success": True,
                "output_text": "File created successfully",
                "metadata": {},
            },
            "collected_effects": {
                "collector_id": "container-123",
                "last_completed_intent_id": "tc-456",
                "effects": [],
            },
            "error": None,
        }

        output_path = temp_overlays / "output.json"
        output_path.write_text(json.dumps(output_data))

        loaded = json.loads(output_path.read_text())
        assert loaded["success"] is True
        assert loaded["result"]["output_text"] == "File created successfully"

    def test_rebind_env_format(self, temp_overlays):
        """Test rebind.env format for path translation."""
        rebind_content = """WORKSPACE_PATH=/container/workspace
SESSION_PATH=/container/.claude
"""
        rebind_path = temp_overlays / "rebind.env"
        rebind_path.write_text(rebind_content)

        # Parse like task_runner does
        env = {}
        for line in rebind_path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                env[key] = value

        assert env["WORKSPACE_PATH"] == "/container/workspace"
        assert env["SESSION_PATH"] == "/container/.claude"
