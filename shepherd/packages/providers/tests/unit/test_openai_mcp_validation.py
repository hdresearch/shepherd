"""Unit tests for MCP server classification, validation, and translation.

Tests cover Spike 3 Steps 1-4:
- _classify_mcp_server() transport classification
- validate_binding() transport-specific MCP validation
- _translate_mcp_servers() Responses API format translation
"""

from __future__ import annotations

import pytest
from shepherd_core.errors import BindingValidationError
from shepherd_core.types import ProviderBinding
from shepherd_providers.openai.provider import (
    OpenAIProvider,
    _classify_mcp_server,
    _extract_bearer_token,
    _translate_mcp_servers,
)

try:
    import mcp  # noqa: F401

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    return OpenAIProvider(name="test", model="gpt-4o", max_turns=5)


# ---------------------------------------------------------------------------
# _classify_mcp_server
# ---------------------------------------------------------------------------


class TestClassifyMcpServer:
    def test_stdio_with_command(self):
        config = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}
        assert _classify_mcp_server(config) == "stdio"

    def test_stdio_bare_config(self):
        config = {"command": "python", "args": ["server.py"], "env": {"KEY": "val"}}
        assert _classify_mcp_server(config) == "stdio"

    def test_remote_with_url(self):
        config = {"url": "https://mcp.example.com/sse"}
        assert _classify_mcp_server(config) == "remote"

    def test_remote_with_type_sse(self):
        config = {"type": "sse", "url": "https://mcp.example.com/sse"}
        assert _classify_mcp_server(config) == "remote"

    def test_remote_with_type_http(self):
        config = {"type": "http", "url": "https://mcp.example.com/mcp"}
        assert _classify_mcp_server(config) == "remote"

    def test_remote_url_without_type(self):
        """A config with url but no explicit type is still remote."""
        config = {"url": "https://mcp.example.com/api"}
        assert _classify_mcp_server(config) == "remote"

    def test_empty_config_is_stdio(self):
        """Edge case: empty config defaults to stdio."""
        assert _classify_mcp_server({}) == "stdio"

    def test_type_sse_without_url_is_remote(self):
        """type=sse is enough to classify as remote even without url."""
        config = {"type": "sse"}
        assert _classify_mcp_server(config) == "remote"


# ---------------------------------------------------------------------------
# _extract_bearer_token
# ---------------------------------------------------------------------------


class TestExtractBearerToken:
    def test_bearer_token(self):
        assert _extract_bearer_token({"Authorization": "Bearer sk-abc123"}) == "sk-abc123"

    def test_bearer_case_insensitive_key(self):
        assert _extract_bearer_token({"authorization": "Bearer tok"}) == "tok"

    def test_bearer_case_insensitive_prefix(self):
        assert _extract_bearer_token({"Authorization": "bearer tok"}) == "tok"

    def test_no_headers_returns_empty(self):
        assert _extract_bearer_token({}) == ""

    def test_none_headers_returns_empty(self):
        assert _extract_bearer_token(None) == ""

    def test_no_auth_header_returns_empty(self):
        assert _extract_bearer_token({"Content-Type": "application/json"}) == ""

    def test_basic_auth_returns_empty(self):
        assert _extract_bearer_token({"Authorization": "Basic dXNlcjpwYXNz"}) == ""

    def test_api_key_auth_returns_empty(self):
        assert _extract_bearer_token({"Authorization": "ApiKey my-key"}) == ""


# ---------------------------------------------------------------------------
# validate_binding — MCP server transport-specific checks
# ---------------------------------------------------------------------------


class TestValidateBindingMcp:
    def test_remote_bearer_auth_accepted(self, provider):
        """Remote MCP server with Bearer auth should pass validation."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/sse",
                    "headers": {"Authorization": "Bearer ghp_abc123"},
                }
            },
        )
        # Should not raise
        provider.validate_binding(binding)

    def test_remote_no_auth_accepted(self, provider):
        """Remote MCP server with no auth should pass validation."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "public": {
                    "url": "https://mcp.example.com/api",
                }
            },
        )
        provider.validate_binding(binding)

    def test_remote_non_bearer_auth_accepted(self, provider):
        """Remote MCP server with non-Bearer auth should pass validation.

        The Responses API supports arbitrary headers on MCP tools.
        Non-Bearer auth is passed through via the ``headers`` field.
        """
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "private": {
                    "type": "http",
                    "url": "https://mcp.example.com/api",
                    "headers": {"Authorization": "Basic dXNlcjpwYXNz"},
                }
            },
        )
        # Should not raise — headers are passed through to the API
        provider.validate_binding(binding)

    @pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not installed")
    def test_stdio_accepted_when_mcp_installed(self, provider):
        """stdio MCP server should be accepted when the mcp SDK is importable."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "local": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                }
            },
        )
        # Should not raise — mcp is installed in the test environment
        provider.validate_binding(binding)

    def test_stdio_rejected_without_mcp_sdk(self, provider):
        """stdio MCP server should be rejected when mcp SDK is missing."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "local": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                }
            },
        )
        import unittest.mock

        with unittest.mock.patch.dict("sys.modules", {"mcp": None}), pytest.raises(BindingValidationError) as exc_info:
            provider.validate_binding(binding)
        msg = str(exc_info.value)
        assert "local" in msg
        assert "mcp" in msg.lower()

    def test_remote_blocked_tools_no_allowed_tools_rejected(self, provider):
        """Remote MCP + blocked_tools targeting server + no allowed_tools = rejected."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/sse",
                    "headers": {"Authorization": "Bearer ghp_abc123"},
                }
            },
            blocked_tools=frozenset({"mcp__github__create_issue", "mcp__github__delete_repo"}),
        )
        with pytest.raises(BindingValidationError) as exc_info:
            provider.validate_binding(binding)
        msg = str(exc_info.value)
        assert "github" in msg
        assert "blocked_tools" in msg
        assert "allowed_tools" in msg
        # Should mention the blocked tool names (unprefixed)
        assert "create_issue" in msg
        assert "delete_repo" in msg

    def test_remote_blocked_tools_with_allowed_tools_accepted(self, provider):
        """Remote MCP + blocked_tools + allowed_tools configured = accepted."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/sse",
                    "headers": {"Authorization": "Bearer ghp_abc123"},
                    "allowed_tools": ["list_repos", "create_issue", "get_pr"],
                }
            },
            blocked_tools=frozenset({"mcp__github__create_issue"}),
        )
        # Should not raise
        provider.validate_binding(binding)

    def test_remote_no_blocked_tools_accepted(self, provider):
        """Remote MCP with no blocked_tools should pass validation."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/sse",
                    "headers": {"Authorization": "Bearer ghp_abc123"},
                }
            },
        )
        provider.validate_binding(binding)

    @pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not installed")
    def test_multiple_servers_mixed_errors(self, provider):
        """Multiple servers: each validated independently, all issues collected."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            blocked_tools=frozenset({"mcp__blocked_server__dangerous_tool"}),
            mcp_servers={
                "remote_ok": {
                    "url": "https://mcp.example.com/api",
                    "headers": {"Authorization": "Bearer tok"},
                },
                "local_ok": {
                    "command": "python",
                    "args": ["server.py"],
                },
                "blocked_server": {
                    "type": "http",
                    "url": "https://mcp.example.com/api2",
                    # blocked_tools targets this server but no allowed_tools
                },
            },
        )
        with pytest.raises(BindingValidationError) as exc_info:
            provider.validate_binding(binding)
        # Only blocked_server should be rejected (blocked without allowed_tools)
        assert len(exc_info.value.unsatisfied_requirements) == 1
        msgs = " ".join(exc_info.value.unsatisfied_requirements)
        assert "blocked_server" in msgs
        assert "remote_ok" not in msgs
        assert "local_ok" not in msgs

    def test_blocked_tools_for_other_server_not_affect_validation(self, provider):
        """blocked_tools prefixed for a different server don't affect this one."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/sse",
                    "headers": {"Authorization": "Bearer tok"},
                }
            },
            blocked_tools=frozenset({"mcp__slack__post_message"}),
        )
        # Should not raise — blocked tool targets 'slack', not 'github'
        provider.validate_binding(binding)


# ---------------------------------------------------------------------------
# _translate_mcp_servers
# ---------------------------------------------------------------------------


class TestTranslateMcpServers:
    def test_basic_remote_with_bearer(self):
        servers = {
            "github": {
                "type": "sse",
                "url": "https://mcp.github.com/sse",
                "headers": {"Authorization": "Bearer ghp_abc123"},
            }
        }
        result = _translate_mcp_servers(servers, frozenset())
        assert len(result) == 1
        entry = result[0]
        assert entry["type"] == "mcp"
        assert entry["server_label"] == "github"
        assert entry["server_url"] == "https://mcp.github.com/sse"
        assert entry["authorization"] == "ghp_abc123"
        assert entry["headers"] == {"Authorization": "Bearer ghp_abc123"}
        assert entry["require_approval"] == "never"
        assert "allowed_tools" not in entry

    def test_remote_no_auth(self):
        servers = {
            "public": {
                "url": "https://mcp.example.com/api",
            }
        }
        result = _translate_mcp_servers(servers, frozenset())
        assert len(result) == 1
        entry = result[0]
        assert "authorization" not in entry
        assert "headers" not in entry
        assert entry["server_url"] == "https://mcp.example.com/api"

    def test_remote_non_bearer_headers_passed_through(self):
        """Non-Bearer auth and custom headers are passed through to the API."""
        servers = {
            "private": {
                "type": "http",
                "url": "https://mcp.example.com/api",
                "headers": {
                    "Authorization": "Basic dXNlcjpwYXNz",
                    "X-Api-Key": "sk-custom",
                },
            }
        }
        result = _translate_mcp_servers(servers, frozenset())
        entry = result[0]
        # No Bearer token -> no "authorization" field
        assert "authorization" not in entry
        # All headers passed through
        assert entry["headers"] == {
            "Authorization": "Basic dXNlcjpwYXNz",
            "X-Api-Key": "sk-custom",
        }

    def test_allowed_tools_minus_blocked(self):
        servers = {
            "github": {
                "type": "sse",
                "url": "https://mcp.github.com/sse",
                "headers": {"Authorization": "Bearer tok"},
                "allowed_tools": ["list_repos", "create_issue", "get_pr", "delete_repo"],
            }
        }
        blocked = frozenset({"mcp__github__create_issue", "mcp__github__delete_repo"})
        result = _translate_mcp_servers(servers, blocked)
        assert len(result) == 1
        entry = result[0]
        assert entry["allowed_tools"] == ["list_repos", "get_pr"]

    def test_allowed_tools_no_blocked(self):
        servers = {
            "github": {
                "url": "https://mcp.github.com/sse",
                "allowed_tools": ["list_repos", "create_issue"],
            }
        }
        result = _translate_mcp_servers(servers, frozenset())
        entry = result[0]
        assert entry["allowed_tools"] == ["list_repos", "create_issue"]

    def test_no_allowed_tools_no_blocked(self):
        """No allowed_tools and no blocked_tools -> no allowed_tools in output."""
        servers = {
            "github": {
                "url": "https://mcp.github.com/sse",
            }
        }
        result = _translate_mcp_servers(servers, frozenset())
        assert "allowed_tools" not in result[0]

    def test_blocked_for_different_server_ignored(self):
        """blocked_tools for server 'slack' don't affect server 'github'."""
        servers = {
            "github": {
                "url": "https://mcp.github.com/sse",
                "allowed_tools": ["list_repos", "create_issue"],
            }
        }
        blocked = frozenset({"mcp__slack__post_message"})
        result = _translate_mcp_servers(servers, blocked)
        # github's allowed_tools should be unchanged
        assert result[0]["allowed_tools"] == ["list_repos", "create_issue"]

    def test_multiple_servers(self):
        servers = {
            "github": {
                "url": "https://mcp.github.com/sse",
                "headers": {"Authorization": "Bearer gh_tok"},
            },
            "slack": {
                "url": "https://mcp.slack.com/api",
                "headers": {"Authorization": "Bearer sl_tok"},
                "allowed_tools": ["post_message", "list_channels", "delete_message"],
            },
        }
        blocked = frozenset({"mcp__slack__delete_message"})
        result = _translate_mcp_servers(servers, blocked)
        assert len(result) == 2

        github = next(e for e in result if e["server_label"] == "github")
        slack = next(e for e in result if e["server_label"] == "slack")

        assert github["authorization"] == "gh_tok"
        assert "allowed_tools" not in github

        assert slack["authorization"] == "sl_tok"
        assert slack["allowed_tools"] == ["post_message", "list_channels"]

    def test_empty_servers(self):
        result = _translate_mcp_servers({}, frozenset())
        assert result == []
