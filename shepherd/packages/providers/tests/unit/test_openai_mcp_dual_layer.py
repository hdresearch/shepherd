"""Spike 6: Dual-layer MCP integration tests.

Tests that a binding with BOTH a remote MCP server and a stdio-bridged MCP
server are processed correctly in the same execute_sdk() call.  All tests
are fully mocked -- no real API calls or MCP servers.

Scenarios:
1. Tool name normalization across layers
2. Effect emission for both layers
3. No tool name collisions between remote and stdio servers
4. blocked_tools enforcement across both transports
5. Mixed-transport loop termination semantics
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.types import (
    ProviderBinding,
    ToolDefinition,
)
from shepherd_providers.openai.provider import (
    OpenAIProvider,
    _translate_mcp_servers,
)

# ---------------------------------------------------------------------------
# Mock helpers (reused from test_openai_provider.py patterns)
# ---------------------------------------------------------------------------


def _make_response(
    response_id: str = "resp_test_123",
    output: list | None = None,
    output_text: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        output=output or [],
        output_text=output_text,
    )


def _make_function_call(
    name: str = "bash",
    call_id: str = "call_1",
    arguments: str = '{"command": "echo hi"}',
) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, call_id=call_id, arguments=arguments)


def _make_mcp_tool_call(
    server_label: str = "github",
    name: str = "list_repos",
    call_id: str = "mcp_call_1",
    arguments: str = "{}",
    output: str = '["repo1", "repo2"]',
) -> SimpleNamespace:
    return SimpleNamespace(
        type="mcp_call",
        server_label=server_label,
        name=name,
        id=call_id,
        arguments=arguments,
        output=output,
        error=None,
    )


def _make_message(text: str = "Hello") -> SimpleNamespace:
    content = [SimpleNamespace(text=text)]
    return SimpleNamespace(type="message", content=content)


async def _mock_stream_from_response(response: SimpleNamespace):
    for idx, item in enumerate(response.output):
        yield SimpleNamespace(type="response.output_item.done", item=item, output_index=idx, sequence_number=idx)
    yield SimpleNamespace(type="response.completed", response=response, sequence_number=len(response.output))


def _make_stream_client(*responses: SimpleNamespace) -> MagicMock:
    streams = iter([_mock_stream_from_response(r) for r in responses])
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=lambda **kw: next(streams))
    return mock_client


def _effect_types(runtime_mock: MagicMock) -> list[str]:
    return [c.args[0].__class__.__name__ for c in runtime_mock.effects.emit.call_args_list]


def _effects_of_type(runtime_mock: MagicMock, type_name: str) -> list:
    return [c.args[0] for c in runtime_mock.effects.emit.call_args_list if c.args[0].__class__.__name__ == type_name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    return OpenAIProvider(name="test", model="gpt-4o", max_turns=5)


@pytest.fixture
def runtime():
    rt = MagicMock()
    rt.task_name = "test"
    rt.effects = MagicMock()
    rt.effects.emit = MagicMock()
    return rt


# ---------------------------------------------------------------------------
# 1. Tool name normalization across layers
# ---------------------------------------------------------------------------


class TestToolNameNormalization:
    """Verify both remote mcp_tool_call items and stdio-bridged function_call
    items produce correctly normalized names in ExecutionResult.tool_calls."""

    @pytest.mark.asyncio
    async def test_both_layers_normalized_in_tool_calls(self, provider, runtime):
        """A response containing both an mcp_tool_call (remote) and a function_call
        (stdio-bridged tool) should produce two tool_calls with correct names."""
        # Turn 1: both an MCP tool call and a function call for stdio-bridged tool
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                _make_mcp_tool_call(
                    server_label="github",
                    name="list_repos",
                    call_id="mcp_c1",
                    arguments='{"org": "acme"}',
                    output='["repo1"]',
                ),
                _make_function_call(
                    name="mcp__filesystem__read_file",
                    call_id="fc_c1",
                    arguments='{"path": "/tmp/test.txt"}',
                ),
            ],
        )
        # Turn 2: final text response after function_call_output is fed back
        turn2 = _make_response(
            response_id="resp_2",
            output=[_make_message("Done processing both tools")],
        )

        # Register a handler for the stdio-bridged tool so dispatch succeeds
        stdio_tool = ToolDefinition(
            name="mcp__filesystem__read_file",
            description="Read a file via stdio MCP bridge",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=lambda args: "file contents here",
        )
        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset({"bash", "read", "write"}),
            trust_level="standard",
            custom_tools=(stdio_tool,),
            mcp_servers={
                "github": {"type": "http", "url": "https://mcp.github.com/sse"},
            },
        )

        mock_client = _make_stream_client(turn1, turn2)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Do stuff", binding, runtime)

        assert result.success
        names = [tc.name for tc in result.tool_calls]
        # Remote MCP tool should be normalized to mcp__<server_label>__<tool_name>
        assert "mcp__github__list_repos" in names
        # Stdio-bridged tool keeps its already-prefixed name
        assert "mcp__filesystem__read_file" in names
        assert len(result.tool_calls) == 2

    @pytest.mark.asyncio
    async def test_remote_mcp_tool_name_normalization(self, provider, runtime):
        """A remote mcp_tool_call with server_label and name should normalize to
        mcp__<server_label>__<name>."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                _make_mcp_tool_call(
                    server_label="slack",
                    name="post_message",
                    call_id="mcp_c1",
                    output="ok",
                ),
                _make_message("Posted!"),
            ],
        )

        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset(),
            trust_level="standard",
            mcp_servers={"slack": {"type": "http", "url": "https://mcp.slack.com/sse"}},
        )

        mock_client = _make_stream_client(turn1)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Post to slack", binding, runtime)

        assert result.success
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "mcp__slack__post_message"


# ---------------------------------------------------------------------------
# 2. Effect emission for both layers
# ---------------------------------------------------------------------------


class TestEffectEmission:
    """Verify ToolCallStarted and ToolCallCompleted effects are emitted for
    both remote MCP tool calls and function calls."""

    @pytest.mark.asyncio
    async def test_effects_emitted_for_both_layers(self, provider, runtime):
        """Both mcp_tool_call and function_call items should emit
        ToolCallStarted and ToolCallCompleted effects."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                _make_mcp_tool_call(
                    server_label="github",
                    name="list_repos",
                    call_id="mcp_c1",
                    output='["repo1"]',
                ),
                _make_function_call(
                    name="mcp__filesystem__read_file",
                    call_id="fc_c1",
                    arguments='{"path": "/tmp/x"}',
                ),
            ],
        )
        turn2 = _make_response(
            response_id="resp_2",
            output=[_make_message("All done")],
        )

        stdio_tool = ToolDefinition(
            name="mcp__filesystem__read_file",
            description="Read file",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=lambda args: "contents",
        )
        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset({"bash"}),
            trust_level="standard",
            custom_tools=(stdio_tool,),
            mcp_servers={"github": {"type": "http", "url": "https://mcp.github.com/sse"}},
        )

        mock_client = _make_stream_client(turn1, turn2)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            await provider.execute_sdk("Do stuff", binding, runtime)

        started = _effects_of_type(runtime, "ToolCallStarted")
        completed = _effects_of_type(runtime, "ToolCallCompleted")

        # Should have 2 started and 2 completed (one per tool call)
        assert len(started) == 2
        assert len(completed) == 2

        started_names = {e.tool_name for e in started}
        completed_names = {e.tool_name for e in completed}
        assert "mcp__github__list_repos" in started_names
        assert "mcp__filesystem__read_file" in started_names
        assert "mcp__github__list_repos" in completed_names
        assert "mcp__filesystem__read_file" in completed_names


# ---------------------------------------------------------------------------
# 3. No tool name collisions
# ---------------------------------------------------------------------------


class TestNoToolNameCollisions:
    """Both a remote server "alpha" and a stdio server "beta" expose a tool
    named "read". They should get distinct normalized names."""

    @pytest.mark.asyncio
    async def test_distinct_names_for_same_tool_across_servers(self, provider, runtime):
        """mcp__alpha__read vs mcp__beta__read should be distinct."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                # Remote MCP server "alpha" calls "read"
                _make_mcp_tool_call(
                    server_label="alpha",
                    name="read",
                    call_id="mcp_c1",
                    output="remote data",
                ),
                # Stdio-bridged function call for "beta" server's "read"
                _make_function_call(
                    name="mcp__beta__read",
                    call_id="fc_c1",
                    arguments='{"path": "/etc/hosts"}',
                ),
            ],
        )
        turn2 = _make_response(
            response_id="resp_2",
            output=[_make_message("Compared both reads")],
        )

        stdio_tool = ToolDefinition(
            name="mcp__beta__read",
            description="Read via stdio bridge beta",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=lambda args: "local data",
        )
        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset(),
            trust_level="standard",
            custom_tools=(stdio_tool,),
            mcp_servers={"alpha": {"type": "http", "url": "https://alpha.example.com/mcp"}},
        )

        mock_client = _make_stream_client(turn1, turn2)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Read from both", binding, runtime)

        assert result.success
        names = [tc.name for tc in result.tool_calls]
        assert "mcp__alpha__read" in names
        assert "mcp__beta__read" in names
        assert names[0] != names[1]  # They are distinct


# ---------------------------------------------------------------------------
# 4. blocked_tools enforcement
# ---------------------------------------------------------------------------


class TestBlockedToolsEnforcement:
    """blocked_tools should work for both remote (via allowed_tools translation)
    and stdio-bridged (via composite validator rejection)."""

    def test_translate_mcp_servers_removes_blocked_from_allowed(self):
        """_translate_mcp_servers should compute allowed_tools by subtracting
        blocked tools from the config's allowed_tools list."""
        mcp_servers = {
            "github": {
                "type": "http",
                "url": "https://mcp.github.com/sse",
                "allowed_tools": ["list_repos", "delete_repo", "create_repo"],
            },
        }
        blocked = frozenset({"mcp__github__delete_repo"})

        result = _translate_mcp_servers(mcp_servers, blocked)

        assert len(result) == 1
        entry = result[0]
        assert entry["server_label"] == "github"
        assert entry["server_url"] == "https://mcp.github.com/sse"
        assert "delete_repo" not in entry["allowed_tools"]
        assert "list_repos" in entry["allowed_tools"]
        assert "create_repo" in entry["allowed_tools"]

    def test_translate_mcp_servers_no_blocked_keeps_all_allowed(self):
        """With no blocked tools, allowed_tools should pass through unchanged."""
        mcp_servers = {
            "github": {
                "type": "http",
                "url": "https://mcp.github.com/sse",
                "allowed_tools": ["list_repos", "delete_repo"],
            },
        }
        blocked: frozenset[str] = frozenset()

        result = _translate_mcp_servers(mcp_servers, blocked)
        assert result[0]["allowed_tools"] == ["list_repos", "delete_repo"]

    @pytest.mark.asyncio
    async def test_stdio_blocked_tool_rejected_by_validator(self, provider, runtime):
        """A stdio-bridged tool in blocked_tools should be rejected by the
        composite validator and not dispatched."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                _make_function_call(
                    name="mcp__filesystem__delete_file",
                    call_id="fc_c1",
                    arguments='{"path": "/important"}',
                ),
            ],
        )
        turn2 = _make_response(
            response_id="resp_2",
            output=[_make_message("Cannot delete")],
        )

        stdio_tool = ToolDefinition(
            name="mcp__filesystem__delete_file",
            description="Delete a file",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=lambda args: "deleted",
        )
        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset(),
            trust_level="standard",
            custom_tools=(stdio_tool,),
            blocked_tools=frozenset({"mcp__filesystem__delete_file"}),
        )

        mock_client = _make_stream_client(turn1, turn2)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Delete the file", binding, runtime)

        # The tool call should NOT appear in successful tool_calls
        # (it was rejected, so no ToolCall recorded)
        assert len(result.tool_calls) == 0

        # The second API call should have the rejection error fed back
        second_call_kwargs = mock_client.responses.create.call_args_list[1].kwargs
        second_input = second_call_kwargs.get("input")
        assert isinstance(second_input, list)
        assert second_input[0]["type"] == "function_call_output"
        assert "rejected" in second_input[0]["output"].lower() or "blocked" in second_input[0]["output"].lower()

    def test_translate_mcp_servers_multiple_servers_independent_blocking(self):
        """Blocked tools targeting different servers should only affect their
        respective server's allowed_tools."""
        mcp_servers = {
            "github": {
                "type": "http",
                "url": "https://mcp.github.com/sse",
                "allowed_tools": ["list_repos", "delete_repo"],
            },
            "slack": {
                "type": "http",
                "url": "https://mcp.slack.com/sse",
                "allowed_tools": ["post_message", "delete_message"],
            },
        }
        blocked = frozenset({"mcp__github__delete_repo", "mcp__slack__delete_message"})

        result = _translate_mcp_servers(mcp_servers, blocked)

        github_entry = next(e for e in result if e["server_label"] == "github")
        slack_entry = next(e for e in result if e["server_label"] == "slack")

        assert github_entry["allowed_tools"] == ["list_repos"]
        assert slack_entry["allowed_tools"] == ["post_message"]


# ---------------------------------------------------------------------------
# 5. Mixed-transport loop termination
# ---------------------------------------------------------------------------


class TestMixedTransportLoopTermination:
    """Verify that loop terminates or continues based on which item types
    are present in the response."""

    @pytest.mark.asyncio
    async def test_only_mcp_tool_calls_terminates_loop(self, provider, runtime):
        """Response with only mcp_tool_call items (no function_call) should
        terminate the loop -- the API already handled the round trip."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                _make_mcp_tool_call(
                    server_label="github",
                    name="list_repos",
                    call_id="mcp_c1",
                    output='["repo1"]',
                ),
                _make_message("Found repos"),
            ],
        )

        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset(),
            trust_level="standard",
            mcp_servers={"github": {"type": "http", "url": "https://mcp.github.com/sse"}},
        )

        mock_client = _make_stream_client(turn1)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("List repos", binding, runtime)

        assert result.success
        # Only 1 API call should have been made (loop terminated after turn 1)
        assert mock_client.responses.create.call_count == 1
        assert result.metadata["turns"] == 1
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "mcp__github__list_repos"

    @pytest.mark.asyncio
    async def test_mixed_items_continues_for_function_calls(self, provider, runtime):
        """Response with both mcp_tool_call AND function_call items should
        continue the loop to feed back function_call_output."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                _make_mcp_tool_call(
                    server_label="github",
                    name="list_repos",
                    call_id="mcp_c1",
                    output='["repo1"]',
                ),
                _make_function_call(
                    name="mcp__filesystem__read_file",
                    call_id="fc_c1",
                    arguments='{"path": "/tmp/x"}',
                ),
            ],
        )
        turn2 = _make_response(
            response_id="resp_2",
            output=[_make_message("Combined results")],
        )

        stdio_tool = ToolDefinition(
            name="mcp__filesystem__read_file",
            description="Read file",
            parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=lambda args: "file data",
        )
        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset(),
            trust_level="standard",
            custom_tools=(stdio_tool,),
            mcp_servers={"github": {"type": "http", "url": "https://mcp.github.com/sse"}},
        )

        mock_client = _make_stream_client(turn1, turn2)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Read and list", binding, runtime)

        assert result.success
        # 2 API calls: first with both items, second after function_call_output
        assert mock_client.responses.create.call_count == 2
        assert result.metadata["turns"] == 2
        assert len(result.tool_calls) == 2

        # Verify function_call_output was sent in the second call
        second_kwargs = mock_client.responses.create.call_args_list[1].kwargs
        second_input = second_kwargs["input"]
        assert any(item["type"] == "function_call_output" for item in second_input)

    @pytest.mark.asyncio
    async def test_only_function_calls_continues_loop(self, provider, runtime):
        """Response with only function_call items (no mcp_tool_call) should
        continue the loop to feed back function_call_output."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[
                _make_function_call("bash", "fc_c1", '{"command": "echo hi"}'),
            ],
        )
        turn2 = _make_response(
            response_id="resp_2",
            output=[_make_message("Echo done")],
        )

        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset({"bash"}),
            trust_level="standard",
        )

        mock_client = _make_stream_client(turn1, turn2)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Echo", binding, runtime)

        assert result.success
        assert mock_client.responses.create.call_count == 2
        assert result.metadata["turns"] == 2

    @pytest.mark.asyncio
    async def test_text_only_terminates_immediately(self, provider, runtime):
        """Response with only message items should terminate after 1 turn."""
        turn1 = _make_response(
            response_id="resp_1",
            output=[_make_message("Just text, no tools")],
        )

        binding = ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset(),
            trust_level="standard",
        )

        mock_client = _make_stream_client(turn1)
        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Hello", binding, runtime)

        assert result.success
        assert mock_client.responses.create.call_count == 1
        assert result.metadata["turns"] == 1
        assert result.output_text == "Just text, no tools"
