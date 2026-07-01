"""Tests for MCP tool name prefixing in MCPServerContext.

Verifies that blocked_tools, require_confirmation, and allowed_tools
are correctly prefixed/propagated so they match the SDK's MCP tool
name pattern: mcp__{server_name}__{tool_name}.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from shepherd_contexts.mcp.server import MCPServerContext
from shepherd_core.types import ExecutionResult


class TestBlockedToolsPrefixing:
    """blocked_tools entries are prefixed with mcp__{name}__ in configure()."""

    def test_single_blocked_tool_is_prefixed(self):
        ctx = MCPServerContext(
            name="github",
            command="gh-mcp",
            blocked_tools=frozenset({"delete_repo"}),
        )
        binding = ctx.configure()
        assert binding.blocked_tools == frozenset({"mcp__github__delete_repo"})

    def test_multiple_blocked_tools_are_prefixed(self):
        ctx = MCPServerContext(
            name="github",
            command="gh-mcp",
            blocked_tools=frozenset({"delete_repo", "transfer_repo"}),
        )
        binding = ctx.configure()
        assert binding.blocked_tools == frozenset(
            {
                "mcp__github__delete_repo",
                "mcp__github__transfer_repo",
            }
        )

    def test_empty_blocked_tools_stays_empty(self):
        ctx = MCPServerContext(name="github", command="gh-mcp")
        binding = ctx.configure()
        assert binding.blocked_tools == frozenset()


class TestRequireConfirmationPrefixing:
    """require_confirmation entries are prefixed with mcp__{name}__ in configure()."""

    def test_single_confirmation_tool_is_prefixed(self):
        ctx = MCPServerContext(
            name="github",
            command="gh-mcp",
            require_confirmation=frozenset({"create_pull_request"}),
        )
        binding = ctx.configure()
        assert binding.require_confirmation == frozenset({"mcp__github__create_pull_request"})

    def test_multiple_confirmation_tools_are_prefixed(self):
        ctx = MCPServerContext(
            name="slack",
            command="slack-mcp",
            require_confirmation=frozenset({"send_message", "delete_message"}),
        )
        binding = ctx.configure()
        assert binding.require_confirmation == frozenset(
            {
                "mcp__slack__send_message",
                "mcp__slack__delete_message",
            }
        )

    def test_empty_confirmation_stays_empty(self):
        ctx = MCPServerContext(name="github", command="gh-mcp")
        binding = ctx.configure()
        assert binding.require_confirmation == frozenset()


class TestAllowedToolsInTransportConfig:
    """allowed_tools is propagated into the transport config dict."""

    def test_allowed_tools_included_in_sse_config(self):
        ctx = MCPServerContext(
            name="github",
            url="https://example.com",
            allowed_tools=frozenset({"read_file", "list_repos"}),
        )
        config = ctx._build_transport_config()
        assert "allowed_tools" in config
        assert config["allowed_tools"] == ["list_repos", "read_file"]  # sorted

    def test_allowed_tools_included_in_stdio_config(self):
        ctx = MCPServerContext(
            name="fs",
            command="fs-mcp",
            allowed_tools=frozenset({"read_file"}),
        )
        config = ctx._build_transport_config()
        assert config["allowed_tools"] == ["read_file"]

    def test_no_allowed_tools_omitted_from_config(self):
        ctx = MCPServerContext(
            name="github",
            url="https://example.com",
        )
        config = ctx._build_transport_config()
        assert "allowed_tools" not in config


class TestExtractEffectsWithPrefixedNames:
    """extract_effects() works correctly with prefixed tool names."""

    def test_extract_effects_matches_prefixed_tool_calls(self):
        ctx = MCPServerContext(
            name="github",
            command="gh-mcp",
            blocked_tools=frozenset({"delete_repo"}),
        )

        call = MagicMock()
        call.name = "mcp__github__list_repos"
        call.params = {"org": "acme"}

        res = MagicMock()
        res.success = True

        result = MagicMock(spec=ExecutionResult)
        result.tool_calls = [call]
        result.tool_results = [res]

        effects = ctx.extract_effects(sandbox=None, result=result)
        assert len(effects) == 1
        assert effects[0].server_name == "github"
        assert effects[0].tool_name == "list_repos"

    def test_extract_effects_ignores_other_server_tools(self):
        ctx = MCPServerContext(name="github", command="gh-mcp")

        call = MagicMock()
        call.name = "mcp__slack__send_message"
        call.params = {}

        res = MagicMock()
        res.success = True

        result = MagicMock(spec=ExecutionResult)
        result.tool_calls = [call]
        result.tool_results = [res]

        effects = ctx.extract_effects(sandbox=None, result=result)
        assert len(effects) == 0
