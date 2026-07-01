"""Tests for provider registry functionality."""

from dataclasses import dataclass
from typing import Any

import pytest
from shepherd_runtime.device.container.provider_registry import (
    _PROVIDER_FACTORIES,
    ProviderCreationError,
    create_provider,
    get_provider_factory,
    list_registered_provider_types,
    register_provider_factory,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@dataclass
class MockProvider:
    """Mock provider for testing."""

    name: str
    model: str = "mock-model"

    @property
    def provider_id(self) -> str:
        return f"provider:mock:{self.name}"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MockProvider":
        return cls(
            name=config.get("name", "default"),
            model=config.get("model", "mock-model"),
        )


@pytest.fixture(autouse=True)
def clean_registry():
    """Clean the provider registry before and after each test."""
    # Save current state
    original = dict(_PROVIDER_FACTORIES)

    # Clear for test
    _PROVIDER_FACTORIES.clear()

    yield

    # Restore original state
    _PROVIDER_FACTORIES.clear()
    _PROVIDER_FACTORIES.update(original)


# =============================================================================
# Registration Tests
# =============================================================================


class TestProviderRegistration:
    """Tests for provider factory registration."""

    def test_register_provider_factory(self):
        """Test registering a provider factory."""
        register_provider_factory("mock", MockProvider.from_config)

        assert "mock" in list_registered_provider_types()
        factory = get_provider_factory("mock")
        assert factory is not None
        # Test factory works correctly
        provider = factory({"name": "test"})
        assert isinstance(provider, MockProvider)

    def test_register_multiple_factories(self):
        """Test registering multiple provider factories."""
        register_provider_factory("mock1", MockProvider.from_config)
        register_provider_factory("mock2", lambda c: MockProvider("mock2"))

        types = list_registered_provider_types()
        assert "mock1" in types
        assert "mock2" in types

    def test_register_overwrites_existing(self):
        """Test that re-registering overwrites the existing factory."""

        def factory1(c):
            return MockProvider("factory1")

        def factory2(c):
            return MockProvider("factory2")

        register_provider_factory("mock", factory1)
        register_provider_factory("mock", factory2)

        factory = get_provider_factory("mock")
        assert factory is factory2

    def test_get_unregistered_factory(self):
        """Test getting an unregistered factory returns None."""
        assert get_provider_factory("nonexistent") is None


# =============================================================================
# Creation Tests
# =============================================================================


class TestProviderCreation:
    """Tests for provider creation from config."""

    def test_create_provider_success(self):
        """Test successful provider creation."""
        register_provider_factory("mock", MockProvider.from_config)

        provider = create_provider(
            {
                "provider_type": "mock",
                "name": "test-provider",
                "model": "test-model",
            }
        )

        assert isinstance(provider, MockProvider)
        assert provider.name == "test-provider"
        assert provider.model == "test-model"

    def test_create_provider_missing_type(self):
        """Test error when provider_type is missing."""
        with pytest.raises(ProviderCreationError) as exc_info:
            create_provider({"name": "test"})

        assert "missing 'provider_type'" in str(exc_info.value)

    def test_create_provider_unknown_type(self):
        """Test error when provider_type is not registered."""
        with pytest.raises(ProviderCreationError) as exc_info:
            create_provider({"provider_type": "unknown"})

        assert "no factory registered" in str(exc_info.value)
        assert "unknown" in str(exc_info.value)

    def test_create_provider_factory_error(self):
        """Test error when factory raises an exception."""

        def failing_factory(config):
            raise ValueError("Factory failed")

        register_provider_factory("failing", failing_factory)

        with pytest.raises(ProviderCreationError) as exc_info:
            create_provider({"provider_type": "failing"})

        assert "factory raised" in str(exc_info.value)


# =============================================================================
# Listing Tests
# =============================================================================


class TestProviderListing:
    """Tests for listing registered providers."""

    def test_list_empty_registry(self):
        """Test listing with no registered providers."""
        types = list_registered_provider_types()
        assert types == []

    def test_list_registered_types(self):
        """Test listing registered provider types."""
        register_provider_factory("claude", MockProvider.from_config)
        register_provider_factory("openai", MockProvider.from_config)

        types = list_registered_provider_types()
        assert sorted(types) == ["claude", "openai"]


# =============================================================================
# Error Tests
# =============================================================================


class TestProviderCreationError:
    """Tests for ProviderCreationError."""

    def test_error_message_format(self):
        """Test error message includes provider type."""
        error = ProviderCreationError("claude", "SDK not installed")

        assert "claude" in str(error)
        assert "SDK not installed" in str(error)

    def test_error_attributes(self):
        """Test error has provider_type attribute."""
        error = ProviderCreationError("openai", "API key missing")

        assert error.provider_type == "openai"
