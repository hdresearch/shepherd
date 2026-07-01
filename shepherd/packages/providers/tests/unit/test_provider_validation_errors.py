"""Tests for provider validation rejection paths.

This module tests error conditions in provider binding validation:
- OpenAI-specific rejections (non-Bearer auth, missing allowed_tools)
- BindingValidationError attributes and messages
- Cross-provider consistency

Note: trust_level validation is handled by Pydantic at ProviderBinding
construction time (Literal type), so providers don't need to re-validate it.
These tests focus on provider-specific constraints beyond Pydantic validation.

These tests address coverage gap HIGH-T4: provider validation rejection paths.
"""

from typing import Any

import pytest
from shepherd_core.errors import BindingValidationError
from shepherd_core.types import ProviderBinding, ProviderCapabilities

try:
    import mcp  # noqa: F401

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

# =============================================================================
# Claude Provider Validation Tests
# =============================================================================


class TestClaudeProviderValidation:
    """Tests for ClaudeProvider.validate_binding() behavior."""

    @pytest.fixture
    def claude_provider(self):
        """Create a ClaudeProvider instance for testing."""
        from shepherd_providers.claude.provider import ClaudeProvider

        return ClaudeProvider(name="test-claude", model="claude-sonnet-4-20250514")

    def test_valid_trust_levels_accepted(self, claude_provider) -> None:
        """Valid trust_level values should not raise."""
        valid_levels = ["sandbox", "restricted", "standard", "elevated"]

        for level in valid_levels:
            binding = ProviderBinding(
                context_id=f"test:{level}",
                trust_level=level,
            )
            # Should not raise
            claude_provider.validate_binding(binding)

    def test_claude_supports_mcp_servers(self, claude_provider) -> None:
        """Claude should accept mcp_servers without error."""
        binding = ProviderBinding(
            context_id="test:mcp",
            trust_level="standard",
            mcp_servers={"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}},
        )
        # Should not raise - Claude supports MCP
        claude_provider.validate_binding(binding)

    def test_claude_supports_session_isolation_forked(self, claude_provider) -> None:
        """Claude should accept session_isolation='forked' without error."""
        binding = ProviderBinding(
            context_id="test:forked",
            trust_level="standard",
            session_isolation="forked",
        )
        # Should not raise
        claude_provider.validate_binding(binding)


# =============================================================================
# OpenAI Provider Validation Tests
# =============================================================================

# A binding that triggers a rejection: remote MCP with blocked_tools but no
# allowed_tools — the provider cannot compute the allowlist.
_REJECTABLE_MCP_CONFIG = {
    "github": {
        "type": "sse",
        "url": "https://mcp.github.com/sse",
    }
}
_REJECTABLE_BLOCKED = frozenset({"mcp__github__delete_repo"})


def _make_rejectable_binding(**overrides: Any) -> ProviderBinding:
    """Build a binding that will be rejected by validate_binding()."""
    defaults: dict[str, Any] = {
        "context_id": "test",
        "trust_level": "standard",
        "mcp_servers": _REJECTABLE_MCP_CONFIG,
        "blocked_tools": _REJECTABLE_BLOCKED,
    }
    defaults.update(overrides)
    return ProviderBinding(**defaults)


class TestOpenAIProviderValidation:
    """Tests for OpenAIProvider.validate_binding() behavior."""

    @pytest.fixture
    def openai_provider(self):
        from shepherd_providers.openai.provider import OpenAIProvider

        return OpenAIProvider(name="test-openai", model="gpt-4o")

    @pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not installed")
    def test_stdio_mcp_accepted_with_sdk(self, openai_provider) -> None:
        """stdio MCP servers should be accepted when the mcp SDK is installed."""
        binding = ProviderBinding(
            context_id="test:mcp",
            trust_level="standard",
            mcp_servers={"filesystem": {"command": "npx", "args": ["-y", "mcp-server"]}},
        )
        # Should not raise — mcp SDK is available
        openai_provider.validate_binding(binding)

    def test_remote_mcp_with_bearer_auth_accepted(self, openai_provider) -> None:
        """Remote MCP with Bearer auth should be accepted."""
        binding = ProviderBinding(
            context_id="test:mcp",
            trust_level="standard",
            mcp_servers={
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/v1",
                    "headers": {"Authorization": "Bearer ghp_token"},
                }
            },
        )
        openai_provider.validate_binding(binding)

    def test_remote_mcp_non_bearer_auth_accepted(self, openai_provider) -> None:
        """Remote MCP with non-Bearer auth should be accepted (headers passed through)."""
        binding = ProviderBinding(
            context_id="test:mcp",
            trust_level="standard",
            mcp_servers={
                "private": {
                    "url": "https://mcp.example.com",
                    "headers": {"X-Api-Key": "sk-key"},
                }
            },
        )
        openai_provider.validate_binding(binding)

    def test_session_isolation_forked_accepted(self, openai_provider) -> None:
        """session_isolation='forked' should be accepted by OpenAI.

        The Responses API's previous_response_id naturally supports forked
        semantics, so this should not be rejected.
        """
        binding = ProviderBinding(
            context_id="test:forked",
            trust_level="standard",
            session_isolation="forked",
        )
        # Should not raise
        openai_provider.validate_binding(binding)

    def test_session_isolation_shared_accepted(self, openai_provider) -> None:
        """session_isolation='shared' should be accepted by OpenAI."""
        binding = ProviderBinding(
            context_id="test:shared",
            trust_level="standard",
            session_isolation="shared",
        )
        openai_provider.validate_binding(binding)

    def test_session_isolation_isolated_accepted(self, openai_provider) -> None:
        """session_isolation='isolated' should be accepted by OpenAI."""
        binding = ProviderBinding(
            context_id="test:isolated",
            trust_level="standard",
            session_isolation="isolated",
        )
        openai_provider.validate_binding(binding)

    def test_binding_validation_error_includes_context(self, openai_provider) -> None:
        """BindingValidationError should include context_id."""
        binding = _make_rejectable_binding(context_id="workspace:main")

        with pytest.raises(BindingValidationError) as exc_info:
            openai_provider.validate_binding(binding)

        assert exc_info.value.context_id == "workspace:main"

    def test_binding_validation_error_includes_capabilities(self, openai_provider) -> None:
        """BindingValidationError should include provider capabilities."""
        binding = _make_rejectable_binding(context_id="test:caps")

        with pytest.raises(BindingValidationError) as exc_info:
            openai_provider.validate_binding(binding)

        assert exc_info.value.provider_capabilities is not None
        assert isinstance(exc_info.value.provider_capabilities, ProviderCapabilities)

    def test_binding_validation_error_message_format(self, openai_provider) -> None:
        """BindingValidationError message should be human-readable."""
        binding = _make_rejectable_binding(context_id="workspace:main")

        with pytest.raises(BindingValidationError) as exc_info:
            openai_provider.validate_binding(binding)

        error_message = str(exc_info.value)
        assert "Provider cannot satisfy binding requirements" in error_message
        assert "workspace:main" in error_message
        assert "mcp_servers" in error_message


# =============================================================================
# Cross-Provider Validation Tests
# =============================================================================


class TestCrossProviderValidation:
    """Tests for consistent validation behavior across providers."""

    @pytest.fixture
    def claude_provider(self):
        from shepherd_providers.claude.provider import ClaudeProvider

        return ClaudeProvider(name="test-claude", model="claude-sonnet-4-20250514")

    @pytest.fixture
    def openai_provider(self):
        from shepherd_providers.openai.provider import OpenAIProvider

        return OpenAIProvider(name="test-openai", model="gpt-4o")

    def test_same_trust_levels_supported(self, claude_provider, openai_provider) -> None:
        """Both providers should support the same trust_level values."""
        valid_levels = ["sandbox", "restricted", "standard", "elevated"]

        for level in valid_levels:
            binding = ProviderBinding(context_id="test", trust_level=level)

            # Both should accept
            claude_provider.validate_binding(binding)
            openai_provider.validate_binding(binding)

    def test_binding_validation_error_is_shepherd_error(self, openai_provider) -> None:
        """BindingValidationError should inherit from ShepherdError."""
        from shepherd_core.errors import ShepherdError

        binding = _make_rejectable_binding()

        with pytest.raises(ShepherdError):
            openai_provider.validate_binding(binding)

    def test_binding_validation_error_has_debug_hint(self, openai_provider) -> None:
        """BindingValidationError should have debug_hint property."""
        binding = _make_rejectable_binding()

        with pytest.raises(BindingValidationError) as exc_info:
            openai_provider.validate_binding(binding)

        assert hasattr(exc_info.value, "debug_hint")
        assert "debug" in exc_info.value.debug_hint.lower()
