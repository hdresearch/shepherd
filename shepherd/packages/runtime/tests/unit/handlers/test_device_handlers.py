"""Tests for device-specific handler registration and lookup.

These tests verify that HandlerRegistry properly supports device-specific
handlers that override defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.handlers import CompositeHandler, HandlerRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# =============================================================================
# Test Effect Types
# =============================================================================


class DeviceTestEffect(Effect):
    """Test effect type for device handler tests."""

    effect_type: Literal["device_test_effect"] = "device_test_effect"


class DeviceOtherEffect(Effect):
    """Another test effect type for device handler tests."""

    effect_type: Literal["device_other_effect"] = "device_other_effect"


# =============================================================================
# Mock Handler
# =============================================================================


class MockHandler:
    """Mock handler for testing."""

    def __init__(self, name: str, effect_cls: type = DeviceTestEffect):
        self.name = name
        self._effect_cls = effect_cls
        self.handled_effects: list[Effect] = []

    @property
    def effect_type(self) -> type:
        return self._effect_cls

    async def handle(
        self,
        effect: Effect,
        context: Any,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        self.handled_effects.append(effect)
        return await resume({"handler": self.name})

    def can_handle(self, effect: Effect) -> bool:
        return isinstance(effect, self._effect_cls)


# =============================================================================
# Tests
# =============================================================================


class TestDeviceHandlerRegistration:
    """Tests for device-specific handler registration."""

    def test_register_default_handler(self):
        """Registering without device creates a default handler."""
        registry = HandlerRegistry()
        handler = MockHandler("default")

        registry.register(handler)

        assert DeviceTestEffect in registry
        assert len(registry) == 1

    def test_register_device_handler(self):
        """Registering with device creates a device-specific handler."""
        registry = HandlerRegistry()
        handler = MockHandler("container")

        registry.register(handler, device="container")

        # Not in default handlers
        assert len(registry) == 0
        # But available via device lookup
        effect = DeviceTestEffect()
        assert registry.get(effect, device="container") is handler

    def test_device_handler_precedence(self):
        """Device-specific handler takes precedence over default."""
        registry = HandlerRegistry()
        default_handler = MockHandler("default")
        container_handler = MockHandler("container")

        registry.register(default_handler)
        registry.register(container_handler, device="container")

        effect = DeviceTestEffect()

        # Without device, get default
        assert registry.get(effect) is default_handler

        # With device, get device-specific
        assert registry.get(effect, device="container") is container_handler

    def test_fallback_to_default_when_no_device_handler(self):
        """Falls back to default when device doesn't have handler for type."""
        registry = HandlerRegistry()
        default_handler = MockHandler("default")

        registry.register(default_handler)

        effect = DeviceTestEffect()

        # Device "container" doesn't have this type, falls back to default
        assert registry.get(effect, device="container") is default_handler

    def test_unregister_device_handler(self):
        """Can unregister device-specific handler."""
        registry = HandlerRegistry()
        handler = MockHandler("container")

        registry.register(handler, device="container")
        removed = registry.unregister(DeviceTestEffect, device="container")

        assert removed is handler
        assert registry.get(DeviceTestEffect(), device="container") is None

    def test_unregister_device_handler_not_found(self):
        """Unregistering non-existent device handler returns None."""
        registry = HandlerRegistry()

        removed = registry.unregister(DeviceTestEffect, device="container")

        assert removed is None


class TestHandlersForDevice:
    """Tests for handlers_for_device composite building."""

    def test_handlers_for_device_returns_composite(self):
        """handlers_for_device returns a CompositeHandler."""
        registry = HandlerRegistry()
        handler = MockHandler("default")
        registry.register(handler)

        composite = registry.handlers_for_device("container")

        assert isinstance(composite, CompositeHandler)

    def test_handlers_for_device_includes_defaults(self):
        """handlers_for_device includes default handlers not overridden."""
        registry = HandlerRegistry()
        test_handler = MockHandler("test", DeviceTestEffect)
        other_handler = MockHandler("other", DeviceOtherEffect)
        container_test = MockHandler("container-test", DeviceTestEffect)

        registry.register(test_handler)
        registry.register(other_handler)
        registry.register(container_test, device="container")

        composite = registry.handlers_for_device("container")

        # Should have container-test (override) and other (default)
        assert len(composite) == 2

        # Test the correct handler is used for each effect type
        test_effect = DeviceTestEffect()
        other_effect = DeviceOtherEffect()

        assert composite.get_handler(test_effect) is container_test
        assert composite.get_handler(other_effect) is other_handler

    def test_handlers_for_device_overrides(self):
        """Device-specific handlers override defaults for same type."""
        registry = HandlerRegistry()
        default_handler = MockHandler("default")
        container_handler = MockHandler("container")

        registry.register(default_handler)
        registry.register(container_handler, device="container")

        composite = registry.handlers_for_device("container")

        # Only 1 handler - container overrides default
        assert len(composite) == 1

        effect = DeviceTestEffect()
        assert composite.get_handler(effect) is container_handler

    def test_handlers_for_device_empty_registry(self):
        """handlers_for_device with empty registry returns empty composite."""
        registry = HandlerRegistry()

        composite = registry.handlers_for_device("container")

        assert len(composite) == 0


class TestRegisteredTypes:
    """Tests for registered_types with device parameter."""

    def test_registered_types_default(self):
        """registered_types() returns default handler types."""
        registry = HandlerRegistry()
        handler = MockHandler("default")
        registry.register(handler)

        types = registry.registered_types()

        assert DeviceTestEffect in types

    def test_registered_types_for_device(self):
        """registered_types(device) returns device-specific types."""
        registry = HandlerRegistry()
        default_handler = MockHandler("default")
        container_handler = MockHandler("container")

        registry.register(default_handler)
        registry.register(container_handler, device="container")

        default_types = registry.registered_types()
        container_types = registry.registered_types(device="container")

        assert DeviceTestEffect in default_types
        assert DeviceTestEffect in container_types

    def test_registered_types_unknown_device(self):
        """registered_types for unknown device returns empty list."""
        registry = HandlerRegistry()

        types = registry.registered_types(device="unknown")

        assert types == []


class TestHasHandlerWithDevice:
    """Tests for has_handler with device parameter."""

    def test_has_handler_with_device(self):
        """has_handler respects device parameter."""
        registry = HandlerRegistry()
        container_handler = MockHandler("container")
        registry.register(container_handler, device="container")

        effect = DeviceTestEffect()

        assert not registry.has_handler(effect)  # No default
        assert registry.has_handler(effect, device="container")
        assert not registry.has_handler(effect, device="local")


@pytest.mark.asyncio
class TestHandleWithDevice:
    """Tests for handle() with device parameter."""

    async def test_handle_uses_device_handler(self):
        """handle() uses device-specific handler when available."""
        registry = HandlerRegistry()
        default_handler = MockHandler("default")
        container_handler = MockHandler("container")

        registry.register(default_handler)
        registry.register(container_handler, device="container")

        effect = DeviceTestEffect()

        # Handle without device - uses default
        result = await registry.handle(effect, None, device=None)
        assert result == {"handler": "default"}

        # Handle with device - uses container
        result = await registry.handle(effect, None, device="container")
        assert result == {"handler": "container"}
