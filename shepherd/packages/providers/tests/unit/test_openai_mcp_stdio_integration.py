"""Integration test for stdio MCP bridge wired into OpenAIProvider.

Tests that execute_sdk() correctly starts stdio MCP servers, discovers tools,
creates augmented bindings, dispatches tool calls through the bridge, and
records results in ExecutionResult.

Uses real FastMCP servers over stdio but mocks the OpenAI API client.
"""

from __future__ import annotations

import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.types import ProviderBinding
from shepherd_providers.openai.provider import OpenAIProvider

try:
    import mcp  # noqa: F401

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not installed")

# ---------------------------------------------------------------------------
# Helpers: mock OpenAI stream that calls a stdio-bridged MCP tool
# ---------------------------------------------------------------------------


def _make_function_call(name: str, call_id: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, call_id=call_id, arguments=arguments)


def _make_message(text: str) -> SimpleNamespace:
    content = [SimpleNamespace(text=text)]
    return SimpleNamespace(type="message", content=content)


def _make_response(response_id: str, output: list) -> SimpleNamespace:
    return SimpleNamespace(id=response_id, output=output, output_text="")


async def _mock_stream(response: SimpleNamespace):
    for idx, item in enumerate(response.output):
        yield SimpleNamespace(type="response.output_item.done", item=item, output_index=idx, sequence_number=idx)
    yield SimpleNamespace(type="response.completed", response=response, sequence_number=len(response.output))


def _make_stream_client(*responses: SimpleNamespace) -> MagicMock:
    streams = iter([_mock_stream(r) for r in responses])
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=lambda **kw: next(streams))
    return mock_client


# ---------------------------------------------------------------------------
# The test MCP server script (runs as a subprocess)
# ---------------------------------------------------------------------------

_MCP_SERVER_SCRIPT = textwrap.dedent("""\
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-stdio-integration")

    @mcp.tool()
    def greet(name: str) -> str:
        "Greet someone by name."
        return f"Hello, {name}!"

    @mcp.tool()
    def add(a: int, b: int) -> str:
        "Add two numbers."
        return str(a + b)

    mcp.run(transport="stdio")
""")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    return OpenAIProvider(name="test-stdio", model="gpt-4o", max_turns=5)


@pytest.fixture
def runtime():
    rt = MagicMock()
    rt.task_name = "test"
    rt.effects = MagicMock()
    rt.effects.emit = MagicMock()
    return rt


class TestStdioMcpIntegration:
    """Tests for stdio MCP server integration in execute_sdk()."""

    async def test_stdio_servers_augment_binding(self, provider, runtime):
        """Stdio MCP servers should produce ToolDefinition objects appended to custom_tools."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "greeter": {
                    "command": sys.executable,
                    "args": ["-c", _MCP_SERVER_SCRIPT],
                },
            },
        )

        augmented, has_stdio = await provider._start_stdio_mcp_servers(binding)

        assert has_stdio is True
        # Original binding had no custom_tools
        assert len(binding.custom_tools) == 0
        # Augmented binding has the discovered MCP tools
        assert len(augmented.custom_tools) >= 2
        tool_names = {t.name for t in augmented.custom_tools}
        assert "mcp__greeter__greet" in tool_names
        assert "mcp__greeter__add" in tool_names

        # Each tool has a callable handler
        for td in augmented.custom_tools:
            assert td.handler is not None
            assert td.parameters_schema is not None

        # Cleanup
        if provider._mcp_pool:
            await provider._mcp_pool.close_all()

    async def test_stdio_tool_handler_calls_bridge(self, provider, runtime):
        """The generated handler should route to the MCP bridge and return results."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "greeter": {
                    "command": sys.executable,
                    "args": ["-c", _MCP_SERVER_SCRIPT],
                },
            },
        )

        augmented, _ = await provider._start_stdio_mcp_servers(binding)

        # Find the greet tool — use async_handler (the real dispatch path)
        greet_td = next(t for t in augmented.custom_tools if t.name == "mcp__greeter__greet")
        assert greet_td.async_handler is not None
        result = await greet_td.async_handler({"name": "World"})
        assert "Hello, World!" in result

        # Find the add tool — use async_handler
        add_td = next(t for t in augmented.custom_tools if t.name == "mcp__greeter__add")
        result = await add_td.async_handler({"a": 3, "b": 7})
        assert "10" in result

        if provider._mcp_pool:
            await provider._mcp_pool.close_all()

    async def test_no_stdio_servers_returns_unchanged(self, provider, runtime):
        """Bindings with only remote MCP servers should pass through unchanged."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/v1",
                },
            },
        )

        augmented, has_stdio = await provider._start_stdio_mcp_servers(binding)
        assert has_stdio is False
        assert augmented is binding  # Same object, not a copy

    async def test_no_mcp_servers_returns_unchanged(self, provider, runtime):
        """Bindings with no MCP servers should pass through unchanged."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
        )

        augmented, has_stdio = await provider._start_stdio_mcp_servers(binding)
        assert has_stdio is False
        assert augmented is binding

    async def test_mixed_servers_only_starts_stdio(self, provider, runtime):
        """With both remote and stdio servers, only stdio tools get ToolDefinitions."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "greeter": {
                    "command": sys.executable,
                    "args": ["-c", _MCP_SERVER_SCRIPT],
                },
                "github": {
                    "type": "sse",
                    "url": "https://mcp.github.com/v1",
                },
            },
        )

        augmented, has_stdio = await provider._start_stdio_mcp_servers(binding)
        assert has_stdio is True
        tool_names = {t.name for t in augmented.custom_tools}
        # Has stdio tools
        assert "mcp__greeter__greet" in tool_names
        # Does NOT have remote tools as ToolDefinitions (those go through _translate_mcp_servers)
        assert not any("github" in name for name in tool_names)

        if provider._mcp_pool:
            await provider._mcp_pool.close_all()

    async def test_full_execute_sdk_with_stdio_mcp(self, provider, runtime):
        """Full round-trip: execute_sdk with a stdio MCP server, mocked API."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "greeter": {
                    "command": sys.executable,
                    "args": ["-c", _MCP_SERVER_SCRIPT],
                },
            },
        )

        # Mock API: first response calls the MCP tool, second is just text
        resp1 = _make_response(
            "resp_1",
            [
                _make_function_call("mcp__greeter__greet", "call_1", '{"name": "Spike"}'),
            ],
        )
        resp2 = _make_response(
            "resp_2",
            [
                _make_message("The greeting is: Hello, Spike!"),
            ],
        )
        mock_client = _make_stream_client(resp1, resp2)

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(prompt="Greet Spike", binding=binding, runtime=runtime)

        assert result.success is True
        assert "Hello, Spike!" in result.output_text
        # The tool call should be recorded
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "mcp__greeter__greet"
        assert result.tool_results[0].success is True
        assert "Hello, Spike!" in result.tool_results[0].output

        # The function_call_output should have been sent back
        create_calls = mock_client.responses.create.call_args_list
        assert len(create_calls) == 2
        second_call_input = create_calls[1].kwargs.get("input", [])
        assert any(
            item.get("type") == "function_call_output" and "Hello, Spike!" in item.get("output", "")
            for item in second_call_input
        )

        if provider._mcp_pool:
            await provider._mcp_pool.close_all()

    async def test_session_pool_reuses_sessions(self, provider, runtime):
        """Multiple calls to _start_stdio_mcp_servers should reuse the same session."""
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            mcp_servers={
                "greeter": {
                    "command": sys.executable,
                    "args": ["-c", _MCP_SERVER_SCRIPT],
                },
            },
        )

        _aug1, _ = await provider._start_stdio_mcp_servers(binding)
        _aug2, _ = await provider._start_stdio_mcp_servers(binding)

        # Same pool, same bridge instance
        pool = provider._mcp_pool
        assert pool is not None
        assert len(pool.active_sessions) == 1
        assert "greeter" in pool.active_sessions

        if provider._mcp_pool:
            await provider._mcp_pool.close_all()
