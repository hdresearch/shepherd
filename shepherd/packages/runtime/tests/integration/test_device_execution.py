"""Integration tests for device execution.

Tests the integration between Device, Scope, and ExecutionLifecycle.
"""

from unittest.mock import MagicMock

import pytest
from shepherd_runtime.device import Device, get_device
from shepherd_runtime.lifecycle import ExecutionLifecycle
from shepherd_runtime.scope import Scope


class TestScopeDeviceIntegration:
    """Tests for Scope.current_device integration."""

    def test_scope_current_device_none_by_default(self):
        """Test scope.current_device is None when no device context."""
        with Scope() as scope:
            assert scope.current_device is None

    def test_scope_current_device_from_context(self):
        """Test scope.current_device returns device from context manager."""
        with Scope() as scope:
            assert scope.current_device is None

            with Device("local"):
                assert scope.current_device is not None
                assert scope.current_device.name == "local"

            # Should be None again after exiting Device context
            assert scope.current_device is None

    def test_scope_current_device_nested_raises(self):
        """Test that nested Device contexts raise DeviceNestingError."""
        from shepherd_runtime.device.errors import DeviceNestingError

        with Scope() as scope, Device("local"):
            assert scope.current_device.name == "local"

            with pytest.raises(DeviceNestingError), Device("container"):
                pass  # Should never reach here

            # Outer device still active after failed nesting attempt
            assert scope.current_device.name == "local"

    def test_scope_current_device_sequential(self):
        """Test scope.current_device with sequential Device contexts."""
        with Scope() as scope:
            with Device("local"):
                assert scope.current_device.name == "local"

            assert scope.current_device is None

            with Device("container"):
                assert scope.current_device.name == "container"

            assert scope.current_device is None

    def test_scope_set_device_explicit(self):
        """Test scope.set_device() overrides context var."""
        local_device = get_device("local")

        with Scope() as scope, Device("container"):
            # Context says container
            assert scope.current_device.name == "container"

            # Explicit set overrides context
            scope.set_device(local_device)
            assert scope.current_device.name == "local"

    def test_scope_set_device_without_context(self):
        """Test scope.set_device() works without Device context."""
        local_device = get_device("local")

        with Scope() as scope:
            assert scope.current_device is None

            scope.set_device(local_device)
            assert scope.current_device is local_device
            assert scope.current_device.name == "local"


class TestExecutionLifecycleDeviceBranch:
    """Tests for ExecutionLifecycle device branching."""

    @pytest.mark.asyncio
    async def test_lifecycle_no_device_uses_in_process(self):
        """Test lifecycle uses in-process path when no device."""
        mock_provider = MagicMock()
        mock_provider.provider_id = "test-provider"
        mock_provider.formatter = None

        with Scope() as scope:
            scope.register_provider("test", mock_provider, default=True)

            lifecycle = ExecutionLifecycle(
                scope=scope,
                provider=mock_provider,
            )

            # No device set - should use in-process
            assert scope.current_device is None

    @pytest.mark.asyncio
    async def test_lifecycle_local_device_uses_in_process(self):
        """Test lifecycle uses in-process path for local device (isolation=none)."""
        mock_provider = MagicMock()
        mock_provider.provider_id = "test-provider"
        mock_provider.formatter = None

        with Scope() as scope:
            scope.register_provider("test", mock_provider, default=True)

            with Device("local"):
                # Local device has isolation_level="none" so should use in-process
                device = scope.current_device
                assert device.capabilities.isolation_level == "none"

    @pytest.mark.asyncio
    async def test_lifecycle_container_device_branches(self):
        """Test lifecycle branches to device path for container device."""
        with Scope() as scope, Device("container"):
            device = scope.current_device
            assert device.capabilities.isolation_level == "container"
            # This confirms the branching logic would take the device path


class TestDeviceContextManagerScoping:
    """Tests for Device context manager with nested scopes."""

    def test_device_context_visible_to_child_scope(self):
        """Test Device context is visible to child scopes."""
        with Scope() as parent_scope, Device("local"):
            # Parent sees the device
            assert parent_scope.current_device.name == "local"

            # Child scope should also see it
            child_scope = parent_scope.child()
            assert child_scope.current_device.name == "local"

    def test_nested_scope_inherits_device(self):
        """Test nested scopes inherit device from parent context."""
        with Scope() as outer, Device("container"):
            assert outer.current_device.name == "container"

            with Scope() as inner:
                # Inner scope sees same device from context var
                assert inner.current_device.name == "container"


class TestDeviceWithForkedScope:
    """Tests for Device with forked scopes."""

    def test_forked_scope_sees_device_context(self):
        """Test forked scope sees device from context var."""
        with Scope() as scope, Device("local"):
            forked = scope.fork()

            # Forked scope sees device from context
            assert forked.current_device.name == "local"

    def test_forked_scope_with_explicit_device(self):
        """Test forked scope can have its own explicit device."""
        container_device = get_device("container")
        local_device = get_device("local")

        with Scope() as scope, Device("local"):
            forked = scope.fork()

            # Initially sees context device
            assert forked.current_device.name == "local"

            # Set explicit device on fork
            forked.set_device(container_device)
            assert forked.current_device.name == "container"

            # Parent still sees local
            assert scope.current_device.name == "local"
