"""Tests for MockContainerDevice.

Verifies that the mock container device correctly simulates
container execution for testing without Podman.
"""

from unittest.mock import MagicMock

import pytest
from shepherd_tests import MockContainerDevice, MockOverlay, MockSandbox


class TestMockSandbox:
    """Tests for MockSandbox."""

    def test_create_generates_uuid(self):
        """Test sandbox creation generates unique IDs."""
        sandbox1 = MockSandbox.create()
        sandbox2 = MockSandbox.create()

        assert sandbox1.sandbox_id != sandbox2.sandbox_id
        assert len(sandbox1.sandbox_id) == 36  # UUID format

    def test_create_with_custom_id(self):
        """Test sandbox creation with custom ID."""
        sandbox = MockSandbox.create("custom-id-123")
        assert sandbox.sandbox_id == "custom-id-123"

    def test_device_name(self):
        """Test sandbox has correct device name."""
        sandbox = MockSandbox.create()
        assert sandbox.device_name == "mock-container"

    def test_simulate_file_create(self):
        """Test simulating file creation."""
        sandbox = MockSandbox.create()
        sandbox.simulate_file_create("new_file.py", "print('hello')")

        assert "new_file.py" in sandbox.files_created
        assert sandbox.files_created["new_file.py"] == "print('hello')"

    def test_simulate_file_modify(self):
        """Test simulating file modification."""
        sandbox = MockSandbox.create()
        sandbox.simulate_file_modify("existing.py", "modified content")

        assert "existing.py" in sandbox.files_modified
        assert sandbox.files_modified["existing.py"] == "modified content"

    def test_get_workspace_layers_empty(self):
        """Test workspace layers when no overlays."""
        sandbox = MockSandbox.create()
        assert sandbox.get_workspace_layers() == []

    def test_get_workspace_layers_with_overlay(self, tmp_path):
        """Test workspace layers with overlay configured."""
        sandbox = MockSandbox.create()

        upper_path = tmp_path / "upper"
        upper_path.mkdir()

        sandbox.overlays["workspace"] = MockOverlay(
            task_id=sandbox.sandbox_id,
            context_name="workspace",
            lower=tmp_path / "lower",
            upper=upper_path,
            work=tmp_path / "work",
            merged=tmp_path / "merged",
        )

        layers = sandbox.get_workspace_layers()
        assert len(layers) == 1
        assert layers[0] == upper_path


class TestMockOverlay:
    """Tests for MockOverlay."""

    def test_overlay_creation(self, tmp_path):
        """Test overlay creation with all paths."""
        overlay = MockOverlay(
            task_id="task-123",
            context_name="workspace",
            lower=tmp_path / "lower",
            upper=tmp_path / "upper",
            work=tmp_path / "work",
            merged=tmp_path / "merged",
        )

        assert overlay.task_id == "task-123"
        assert overlay.context_name == "workspace"
        assert overlay.is_vm_path is False

    def test_overlay_with_host_path(self, tmp_path):
        """Test overlay stores original host path."""
        original = tmp_path / "original"
        overlay = MockOverlay(
            task_id="task-123",
            context_name="workspace",
            lower=tmp_path / "lower",
            upper=tmp_path / "upper",
            work=tmp_path / "work",
            merged=tmp_path / "merged",
            original_host_path=original,
        )

        assert overlay.original_host_path == original


class TestMockContainerDevice:
    """Tests for MockContainerDevice."""

    @pytest.fixture
    def device(self):
        """Create a fresh mock device."""
        return MockContainerDevice()

    @pytest.fixture
    def mock_scope(self):
        """Create a mock scope."""
        scope = MagicMock()
        scope.id = "test-scope"
        scope.get_latest_sandbox_for_context.return_value = None
        return scope

    @pytest.fixture
    def mock_config(self, tmp_path):
        """Create a mock sandbox config."""
        from shepherd_core.foundation.protocols.device import SandboxConfig

        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir()

        return SandboxConfig(
            context_states={
                "workspace": MagicMock(path=str(workspace_path)),
            },
        )

    @pytest.mark.asyncio
    async def test_create_sandbox(self, device, mock_scope, mock_config):
        """Test sandbox creation."""
        sandbox = await device.create_sandbox(mock_scope, mock_config)

        assert sandbox is not None
        assert sandbox.sandbox_id is not None
        assert sandbox.task_dir is not None
        assert sandbox.task_dir.exists()

    @pytest.mark.asyncio
    async def test_create_sandbox_creates_overlays(self, device, mock_scope, mock_config):
        """Test sandbox creation creates overlay directories."""
        sandbox = await device.create_sandbox(mock_scope, mock_config)

        assert "workspace" in sandbox.overlays
        overlay = sandbox.overlays["workspace"]
        assert overlay.upper.exists()
        assert overlay.work.exists()
        assert overlay.merged.exists()

    @pytest.mark.asyncio
    async def test_create_sandbox_tracks_calls(self, device, mock_scope, mock_config):
        """Test sandbox creation is tracked."""
        sandbox = await device.create_sandbox(mock_scope, mock_config)

        assert len(device.sandboxes_created) == 1
        assert device.sandboxes_created[0] is sandbox

    @pytest.mark.asyncio
    async def test_execute_returns_default_output(self, device, mock_scope, mock_config):
        """Test execute returns configured default output."""
        from shepherd_core.foundation.protocols.device import ExecutionResult, ExecutionSpec

        device.default_output = ExecutionResult(
            success=True,
            output_text="Custom output",
        )

        sandbox = await device.create_sandbox(mock_scope, mock_config)
        spec = ExecutionSpec(
            prompt="Test prompt",
            provider_config={"model": "test"},
        )

        result = await device.execute(sandbox, spec)

        assert result.success is True
        assert result.output_text == "Custom output"

    @pytest.mark.asyncio
    async def test_execute_tracks_calls(self, device, mock_scope, mock_config):
        """Test execute tracks calls for assertions."""
        from shepherd_core.foundation.protocols.device import ExecutionSpec

        sandbox = await device.create_sandbox(mock_scope, mock_config)
        spec = ExecutionSpec(
            prompt="Test prompt",
            provider_config={"model": "test"},
        )

        await device.execute(sandbox, spec)

        assert len(device.execute_calls) == 1
        assert device.execute_calls[0][0] is sandbox
        assert device.execute_calls[0][1].prompt == "Test prompt"

    @pytest.mark.asyncio
    async def test_extract_effects_empty(self, device, mock_scope, mock_config):
        """Test effect extraction with no simulated changes."""
        from shepherd_core.foundation.protocols.device import ExecutionResult

        sandbox = await device.create_sandbox(mock_scope, mock_config)
        result = ExecutionResult(success=True)

        bundle = await device.extract_effects(sandbox, result)

        assert bundle.context_effects == {}
        assert bundle.lifecycle_effects == []
        assert bundle.execution_metadata["mock"] is True

    @pytest.mark.asyncio
    async def test_extract_effects_with_created_files(self, device, mock_scope, mock_config):
        """Test effect extraction generates FileCreate effects."""
        from shepherd_core.effects import FileCreate
        from shepherd_core.foundation.protocols.device import ExecutionResult

        sandbox = await device.create_sandbox(mock_scope, mock_config)
        sandbox.simulate_file_create("new_file.py", "print('hello')")

        result = ExecutionResult(success=True)
        bundle = await device.extract_effects(sandbox, result)

        assert "workspace" in bundle.context_effects
        workspace_effects = bundle.context_effects["workspace"]
        assert len(workspace_effects) == 1
        assert isinstance(workspace_effects[0], FileCreate)
        assert workspace_effects[0].path == "new_file.py"
        assert workspace_effects[0].content == "print('hello')"

    @pytest.mark.asyncio
    async def test_extract_effects_with_modified_files(self, device, mock_scope, mock_config):
        """Test effect extraction generates FilePatch effects."""
        from shepherd_core.effects import FilePatch
        from shepherd_core.foundation.protocols.device import ExecutionResult

        sandbox = await device.create_sandbox(mock_scope, mock_config)
        sandbox.simulate_file_modify("existing.py", "new content")

        result = ExecutionResult(success=True)
        bundle = await device.extract_effects(sandbox, result)

        assert "workspace" in bundle.context_effects
        workspace_effects = bundle.context_effects["workspace"]
        assert len(workspace_effects) == 1
        assert isinstance(workspace_effects[0], FilePatch)
        assert workspace_effects[0].path == "existing.py"

    @pytest.mark.asyncio
    async def test_extract_effects_with_default_effects(self, device, mock_scope, mock_config):
        """Test effect extraction includes configured default effects."""
        from shepherd_core.effects import TaskStarted
        from shepherd_core.foundation.protocols.device import ExecutionResult

        effect = TaskStarted(task_name="test-task")
        device.default_effects = [effect]

        sandbox = await device.create_sandbox(mock_scope, mock_config)
        result = ExecutionResult(success=True)
        bundle = await device.extract_effects(sandbox, result)

        assert len(bundle.lifecycle_effects) == 1
        assert isinstance(bundle.lifecycle_effects[0], TaskStarted)

    @pytest.mark.asyncio
    async def test_cleanup_removes_directories(self, device, mock_scope, mock_config):
        """Test cleanup removes temporary directories."""
        sandbox = await device.create_sandbox(mock_scope, mock_config)
        task_dir = sandbox.task_dir

        assert task_dir.exists()

        await device.cleanup(sandbox)

        assert not task_dir.exists()
        assert sandbox in device.sandboxes_cleaned

    @pytest.mark.asyncio
    async def test_cleanup_preserves_with_flag(self, device, mock_scope, mock_config):
        """Test cleanup preserves directories when preserve=True."""
        sandbox = await device.create_sandbox(mock_scope, mock_config)
        task_dir = sandbox.task_dir

        await device.cleanup(sandbox, preserve=True)

        assert task_dir.exists()  # Still exists

    @pytest.mark.asyncio
    async def test_cleanup_force_overrides_preserve(self, device, mock_scope, mock_config):
        """Test cleanup force flag overrides preserve."""
        sandbox = await device.create_sandbox(mock_scope, mock_config)
        task_dir = sandbox.task_dir

        await device.cleanup(sandbox, preserve=True, force=True)

        assert not task_dir.exists()  # Removed despite preserve

    def test_reset_clears_tracking(self, device):
        """Test reset clears all tracking state."""
        device.sandboxes_created.append(MockSandbox.create())
        device.sandboxes_cleaned.append(MockSandbox.create())
        device.execute_calls.append((MockSandbox.create(), None))

        device.reset()

        assert device.sandboxes_created == []
        assert device.sandboxes_cleaned == []
        assert device.execute_calls == []

    def test_capabilities(self, device):
        """Test device capabilities are correctly configured."""
        caps = device.capabilities

        assert caps.isolation_level == "container"
        assert caps.effect_capture == "overlay"
        assert caps.supports_parallel is True

    @pytest.mark.asyncio
    async def test_multiple_sandboxes(self, device, mock_scope, mock_config):
        """Test creating multiple sandboxes."""
        sandbox1 = await device.create_sandbox(mock_scope, mock_config)
        sandbox2 = await device.create_sandbox(mock_scope, mock_config)

        assert sandbox1.sandbox_id != sandbox2.sandbox_id
        assert len(device.sandboxes_created) == 2
        assert sandbox1.task_dir != sandbox2.task_dir


class TestMockContainerDeviceIntegration:
    """Integration tests showing MockContainerDevice usage patterns."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tmp_path):
        """Test complete sandbox lifecycle."""
        from shepherd_core.foundation.protocols.device import (
            ExecutionResult,
            ExecutionSpec,
            SandboxConfig,
        )

        device = MockContainerDevice()
        device.default_output = ExecutionResult(
            success=True,
            output_text="Created hello.py",
        )

        scope = MagicMock()
        scope.id = "test"
        scope.get_latest_sandbox_for_context.return_value = None

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = SandboxConfig(
            context_states={"workspace": MagicMock(path=str(workspace))},
        )

        # Create
        sandbox = await device.create_sandbox(scope, config)
        assert sandbox.task_dir.exists()

        # Execute
        spec = ExecutionSpec(prompt="Create hello.py", provider_config={})
        result = await device.execute(sandbox, spec)
        assert result.success

        # Simulate file creation (what container would do)
        sandbox.simulate_file_create("hello.py", "print('hello')")

        # Extract effects
        bundle = await device.extract_effects(sandbox, result)
        assert "workspace" in bundle.context_effects
        assert bundle.context_effects["workspace"][0].path == "hello.py"

        # Cleanup
        await device.cleanup(sandbox)
        assert not sandbox.task_dir.exists()

    @pytest.mark.asyncio
    async def test_as_podman_fallback(self, tmp_path):
        """Test using MockContainerDevice as fallback when Podman unavailable.

        This pattern can be used in tests to run container logic tests
        even when Podman isn't available (e.g., in CI).
        """
        import subprocess

        # Check if Podman is available
        def is_podman_available():
            try:
                result = subprocess.run(
                    ["podman", "version"],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
                return result.returncode == 0
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False

        # Select device based on availability
        if is_podman_available():
            # Would use real ContainerDevice
            # device = ContainerDevice(overlays_root=tmp_path)
            pass

        # Use mock when Podman unavailable
        device = MockContainerDevice()

        # Test still runs with mock device
        scope = MagicMock()
        scope.id = "test"
        scope.get_latest_sandbox_for_context.return_value = None

        from shepherd_core.foundation.protocols.device import SandboxConfig

        config = SandboxConfig(
            context_states={"workspace": MagicMock(path=str(tmp_path))},
        )

        sandbox = await device.create_sandbox(scope, config)
        assert sandbox is not None
        assert device.name == "mock-container"

        await device.cleanup(sandbox)
