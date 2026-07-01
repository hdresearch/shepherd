"""Contract tests: MockProvider should behave like real providers.

These tests verify that MockProvider (used for testing) has the
same interface and behavior as real provider implementations.
"""

import pytest
from shepherd_core.types import ProviderBinding, ProviderCapabilities
from shepherd_providers.claude import ClaudeProvider
from shepherd_providers.openai import OpenAIProvider
from shepherd_tests import MockProvider


class TestProviderContract:
    """Verify all providers have consistent interface."""

    @pytest.fixture(params=["mock", "claude", "openai"])
    def provider(self, request: pytest.FixtureRequest):
        """Parameterized fixture for all providers."""
        if request.param == "mock":
            return MockProvider(name="test-mock")
        if request.param == "claude":
            return ClaudeProvider(name="test-claude")
        if request.param == "openai":
            return OpenAIProvider(name="test-openai")
        raise ValueError(f"Unknown provider: {request.param}")

    def test_has_name(self, provider) -> None:
        """All providers have a name."""
        assert provider.name is not None
        assert isinstance(provider.name, str)
        assert len(provider.name) > 0

    def test_has_provider_id(self, provider) -> None:
        """All providers have a unique provider_id."""
        assert provider.provider_id is not None
        assert isinstance(provider.provider_id, str)
        assert len(provider.provider_id) > 0

    def test_has_capabilities(self, provider) -> None:
        """All providers declare capabilities."""
        caps = provider.capabilities
        assert isinstance(caps, ProviderCapabilities)

    def test_validate_binding_accepts_minimal(self, provider) -> None:
        """All providers accept minimal bindings."""
        binding = ProviderBinding(context_id="test")
        # Should not raise
        provider.validate_binding(binding)

    def test_validate_binding_accepts_with_tools(self, provider) -> None:
        """All providers accept bindings with custom_tools."""
        binding = ProviderBinding(
            context_id="test",
            custom_tools=(),
        )
        # Should not raise
        provider.validate_binding(binding)


class TestMockProviderBehavior:
    """Test MockProvider specific behavior."""

    def test_default_output(self) -> None:
        """MockProvider returns configured default output text."""
        provider = MockProvider(
            name="test",
            default_output="Hello, World!",
        )
        assert provider.default_output == "Hello, World!"

    def test_structured_output(self) -> None:
        """MockProvider can configure structured output."""
        provider = MockProvider(
            name="test",
            structured_output={"greeting": "Hello, World!"},
        )
        assert provider.structured_output == {"greeting": "Hello, World!"}

    def test_mock_responses_queue(self) -> None:
        """MockProvider can use a queue of responses."""
        provider = MockProvider(
            name="test",
            mock_responses=[
                {"text": "First", "structured": {"step": 1}},
                {"text": "Second", "structured": {"step": 2}},
            ],
        )
        assert len(provider.mock_responses) == 2

    def test_call_tracking(self) -> None:
        """MockProvider tracks calls for assertions."""
        provider = MockProvider(name="test")
        assert provider.calls == []
        provider.reset()  # Should clear calls
        assert provider.calls == []
