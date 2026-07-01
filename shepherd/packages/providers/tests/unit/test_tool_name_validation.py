"""Spike 1: Tool-Name / Validator Compatibility.

Decision: Option A — Extend TOOL_CAPABILITY_REQUIREMENTS in shepherd-core.

Rationale:
  1. No precedent for per-provider name mapping exists in the codebase.
     ClaudeProvider receives PascalCase names from the SDK; LiteLLMProvider
     uses lowercase names but never validates (it dispatches directly without
     calling capability_for_tool()). Option B would introduce a new pattern
     with no existing precedent.
  2. Scales to N providers without code duplication. The provider registry
     is designed for pluggable providers; Option A lets each use its native
     tool names with zero per-provider validation code.
  3. Phase 2 tools (search_files, search_content, edit_file) extend naturally
     as single-line additions to TOOL_CAPABILITY_REQUIREMENTS.
  4. The "coupling" concern is overstated — the map is pure data (name →
     capability string), not logic. It grows linearly with the number of
     distinct tool names across all providers, which is bounded and small.

  Read-capability tools (search_files, search_content) are intentionally
  omitted from TOOL_CAPABILITY_REQUIREMENTS, matching the existing pattern
  for Read, Glob, and Grep — they are ungated by design. Only bash,
  write_file, and edit_file need gating entries.

See: design/SPIKES-openai-provider.md, Spike 1
See: design/PROPOSAL-openai-provider-responses-api.md, §3.4
"""

from unittest.mock import MagicMock

import pytest
from shepherd_core.provider import DefaultProviderRuntime
from shepherd_core.provider.provider import Provider
from shepherd_core.types import (
    ProviderBinding,
    ToolCall,
    capability_for_tool,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def openai_provider():
    """Create an OpenAIProvider for validation testing."""
    from shepherd_providers.openai.provider import OpenAIProvider

    return OpenAIProvider(name="test", model="gpt-4o")


@pytest.fixture
def claude_provider():
    """Create a ClaudeProvider for validation testing."""
    from shepherd_providers.claude.provider import ClaudeProvider

    return ClaudeProvider(name="test", model="claude-sonnet-4-20250514")


def _build_validator(provider: Provider, capabilities: frozenset[str]):
    """Build a composite validator with given capabilities."""
    binding = ProviderBinding(
        context_id="test",
        capabilities=capabilities,
    )
    scope = MagicMock()
    return provider._build_composite_validator(
        binding,
        DefaultProviderRuntime.from_emitter(scope, task_name="test"),
    )


# ---------------------------------------------------------------------------
# capability_for_tool() coverage — lowercase aliases
# ---------------------------------------------------------------------------


class TestCapabilityForToolLowercase:
    """Verify that capability_for_tool() resolves lowercase tool names.

    These tests validate that the TOOL_CAPABILITY_REQUIREMENTS map includes
    lowercase aliases for tools used by non-Claude providers (OpenAI,
    LiteLLM). Without these aliases, the composite validator's capability
    check silently passes for lowercase tool names, defeating the purpose
    of capability-based tool restriction.
    """

    def test_bash_lowercase_resolves(self):
        """'bash' should resolve to 'bash' capability."""
        assert capability_for_tool("bash") == "bash"

    def test_bash_pascalcase_resolves(self):
        """'Bash' should still resolve (existing entry)."""
        assert capability_for_tool("Bash") == "bash"

    def test_write_file_resolves(self):
        """'write_file' should resolve to 'write' capability."""
        assert capability_for_tool("write_file") == "write"

    def test_edit_file_resolves(self):
        """'edit_file' should resolve to 'write' capability."""
        assert capability_for_tool("edit_file") == "write"

    def test_read_file_ungated(self):
        """'read_file' should return None — read tools are ungated by design.

        Matches the existing pattern: Read, Glob, Grep are in
        CAPABILITY_TOOL_MAP but not TOOL_CAPABILITY_REQUIREMENTS.
        """
        assert capability_for_tool("read_file") is None

    def test_search_files_ungated(self):
        """'search_files' should return None — read tools are ungated."""
        assert capability_for_tool("search_files") is None

    def test_search_content_ungated(self):
        """'search_content' should return None — read tools are ungated."""
        assert capability_for_tool("search_content") is None

    def test_unknown_tool_returns_none(self):
        """Unknown tool names should return None (no capability required)."""
        assert capability_for_tool("nonexistent_tool") is None


# ---------------------------------------------------------------------------
# Composite validator — negative cases (rejection)
# ---------------------------------------------------------------------------


class TestValidatorRejectsLowercaseTools:
    """Verify the composite validator rejects lowercase tools without capability.

    Before the fix, capability_for_tool('bash') returned None, so the
    capability check silently passed — a binding with capabilities={'read'}
    would NOT block a 'bash' tool call. After the fix, 'bash' correctly
    maps to the 'bash' capability and is rejected.
    """

    def test_bash_rejected_without_capability(self, openai_provider):
        """'bash' tool call should be rejected when binding lacks 'bash' cap."""
        validator = _build_validator(openai_provider, frozenset({"read"}))
        tool_call = ToolCall(id="1", name="bash", params={"command": "ls"})
        result = validator(tool_call)
        assert not result.allowed
        assert "bash" in (result.rejection_reason or "").lower()

    def test_Bash_rejected_without_capability(self, openai_provider):
        """'Bash' (PascalCase) should also be rejected — existing behaviour."""
        validator = _build_validator(openai_provider, frozenset({"read"}))
        tool_call = ToolCall(id="2", name="Bash", params={"command": "ls"})
        result = validator(tool_call)
        assert not result.allowed

    def test_write_file_rejected_without_capability(self, openai_provider):
        """'write_file' should be rejected when binding lacks 'write' cap."""
        validator = _build_validator(openai_provider, frozenset({"read", "bash"}))
        tool_call = ToolCall(id="3", name="write_file", params={"path": "/tmp/x", "content": "y"})
        result = validator(tool_call)
        assert not result.allowed
        assert "write" in (result.rejection_reason or "").lower()

    def test_edit_file_rejected_without_capability(self, openai_provider):
        """'edit_file' should be rejected when binding lacks 'write' cap."""
        validator = _build_validator(openai_provider, frozenset({"read", "bash"}))
        tool_call = ToolCall(id="4", name="edit_file", params={"path": "/tmp/x", "old_text": "a", "new_text": "b"})
        result = validator(tool_call)
        assert not result.allowed


# ---------------------------------------------------------------------------
# Composite validator — positive cases (acceptance)
# ---------------------------------------------------------------------------


class TestValidatorAcceptsWithCapability:
    """Verify the composite validator accepts tools when capability is present."""

    def test_bash_accepted_with_capability(self, openai_provider):
        """'bash' should pass when binding has 'bash' capability."""
        validator = _build_validator(openai_provider, frozenset({"bash"}))
        tool_call = ToolCall(id="5", name="bash", params={"command": "ls"})
        result = validator(tool_call)
        assert result.allowed

    def test_write_file_accepted_with_capability(self, openai_provider):
        """'write_file' should pass when binding has 'write' capability."""
        validator = _build_validator(openai_provider, frozenset({"write"}))
        tool_call = ToolCall(id="6", name="write_file", params={"path": "/tmp/x", "content": "y"})
        result = validator(tool_call)
        assert result.allowed

    def test_read_file_accepted_without_read(self, openai_provider):
        """'read_file' should pass even without 'read' cap — ungated by design."""
        validator = _build_validator(openai_provider, frozenset())
        tool_call = ToolCall(id="7", name="read_file", params={"path": "/tmp/x"})
        result = validator(tool_call)
        assert result.allowed


# ---------------------------------------------------------------------------
# Cross-provider consistency
# ---------------------------------------------------------------------------


class TestCrossProviderConsistency:
    """Verify that both ClaudeProvider and OpenAIProvider produce consistent
    validation results for the same tool names and capabilities."""

    def test_bash_rejection_consistent(self, openai_provider, claude_provider):
        """Both providers should reject 'bash' without 'bash' capability."""
        for provider in (openai_provider, claude_provider):
            validator = _build_validator(provider, frozenset({"read"}))
            # Use the name each provider's models would emit
            for name in ("bash", "Bash"):
                result = validator(ToolCall(id="x", name=name, params={"command": "ls"}))
                assert not result.allowed, f"{provider.__class__.__name__} should reject '{name}' without bash cap"

    def test_write_rejection_consistent(self, openai_provider, claude_provider):
        """Both providers should reject write tools without 'write' capability."""
        for provider in (openai_provider, claude_provider):
            validator = _build_validator(provider, frozenset({"read"}))
            for name in ("write_file", "Write"):
                result = validator(ToolCall(id="x", name=name, params={}))
                assert not result.allowed, f"{provider.__class__.__name__} should reject '{name}' without write cap"
