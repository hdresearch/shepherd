"""Unit tests for MCP stream handling in OpenAIProvider.

Tests the StreamResult dataclass, MCP tool call processing, mixed-transport
turns, approval request handling, unknown item types, and tool name
normalization. Uses the same mock pattern as test_openai_provider.py.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.types import ProviderBinding
from shepherd_providers.openai.provider import OpenAIProvider, StreamResult

# ---------------------------------------------------------------------------
# Helpers (same mock pattern as test_openai_provider.py)
# ---------------------------------------------------------------------------


def _make_response(
    response_id: str = "resp_test_123",
    output: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        output=output or [],
        output_text="",
    )


def _make_function_call(
    name: str = "bash",
    call_id: str = "call_1",
    arguments: str = '{"command": "echo hi"}',
) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, call_id=call_id, arguments=arguments)


def _make_message(text: str = "Hello") -> SimpleNamespace:
    content = [SimpleNamespace(text=text)]
    return SimpleNamespace(type="message", content=content)


def _make_mcp_tool_call(
    server_label: str = "github",
    name: str = "read_file",
    call_id: str = "mcp_call_1",
    arguments: str = '{"path": "/tmp/x"}',
    output: str = "file contents here",
    error: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        type="mcp_call",
        server_label=server_label,
        name=name,
        id=call_id,
        arguments=arguments,
        output=output,
        error=error,
    )


def _make_mcp_list_tools(
    server_label: str = "github",
    tools: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        type="mcp_list_tools",
        server_label=server_label,
        tools=tools or [SimpleNamespace(name="read_file"), SimpleNamespace(name="write_file")],
    )


def _make_mcp_approval_request(
    approval_id: str = "approval_1",
    server_label: str = "github",
    tool_name: str = "write_file",
) -> SimpleNamespace:
    return SimpleNamespace(
        type="mcp_approval_request",
        id=approval_id,
        server_label=server_label,
        name=tool_name,
    )


def _make_unknown_item(item_type: str = "quantum_entanglement") -> SimpleNamespace:
    return SimpleNamespace(type=item_type, data="mystery")


def _make_output_item_done(item: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_item.done", item=item, output_index=0, sequence_number=0)


def _make_completed(response_id: str = "resp_1") -> SimpleNamespace:
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(id=response_id),
    )


async def _raw_stream(events: list[SimpleNamespace]):
    for e in events:
        yield e


def _make_raw_stream_client(*event_sequences: list[SimpleNamespace]) -> MagicMock:
    streams = iter([_raw_stream(seq) for seq in event_sequences])
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=lambda **kw: next(streams))
    return mock_client


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
def binding():
    return ProviderBinding(
        context_id="test_ctx",
        capabilities=frozenset({"bash", "read", "write"}),
        trust_level="standard",
    )


@pytest.fixture
def runtime():
    rt = MagicMock()
    rt.task_name = "test"
    rt.effects = MagicMock()
    rt.effects.emit = MagicMock()
    return rt


# ---------------------------------------------------------------------------
# Test 1: StreamResult fields
# ---------------------------------------------------------------------------


class TestStreamResultFields:
    @pytest.mark.asyncio
    async def test_consume_stream_returns_stream_result(self, provider, runtime):
        """_consume_stream should return a StreamResult with correct field values."""
        mock_client = _make_raw_stream_client(
            [
                _make_output_item_done(_make_message("hello")),
                _make_completed("resp_abc"),
            ]
        )
        kwargs = {"model": "gpt-4o", "input": "test"}

        sr = await provider._consume_stream(mock_client, kwargs, runtime)

        assert isinstance(sr, StreamResult)
        assert sr.text == "hello"
        assert sr.response_id == "resp_abc"
        assert sr.func_calls == []
        assert sr.mcp_tool_calls == []
        assert sr.mcp_list_tools == []
        assert sr.mcp_approval_requests == []
        assert sr.thinking == ""

    @pytest.mark.asyncio
    async def test_stream_result_with_function_call(self, provider, runtime):
        """StreamResult should populate func_calls for function_call items."""
        fc = _make_function_call("bash", "c1", '{"command": "ls"}')
        mock_client = _make_raw_stream_client([_make_output_item_done(fc), _make_completed("resp_1")])
        kwargs = {"model": "gpt-4o", "input": "test"}

        sr = await provider._consume_stream(mock_client, kwargs, runtime)

        assert len(sr.func_calls) == 1
        assert sr.func_calls[0].name == "bash"

    @pytest.mark.asyncio
    async def test_stream_result_with_mcp_items(self, provider, runtime):
        """StreamResult should populate all MCP lists from respective item types."""
        mock_client = _make_raw_stream_client(
            [
                _make_output_item_done(_make_mcp_list_tools("github")),
                _make_output_item_done(_make_mcp_tool_call("github", "read_file")),
                _make_output_item_done(_make_mcp_approval_request("a1")),
                _make_completed("resp_1"),
            ]
        )
        kwargs = {"model": "gpt-4o", "input": "test"}

        sr = await provider._consume_stream(mock_client, kwargs, runtime)

        assert len(sr.mcp_list_tools) == 1
        assert len(sr.mcp_tool_calls) == 1
        assert len(sr.mcp_approval_requests) == 1


# ---------------------------------------------------------------------------
# Test 2: MCP tool call processing
# ---------------------------------------------------------------------------


class TestMCPToolCallProcessing:
    @pytest.mark.asyncio
    async def test_mcp_tool_call_emits_effects(self, provider, binding, runtime):
        """MCP tool calls should emit ToolCallStarted and ToolCallCompleted effects."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_tool_call("github", "read_file", "mcp_c1", '{"path": "/x"}', "content"),
                    _make_message("Done"),
                ],
            ),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert result.success
        effects = _effect_types(runtime)
        assert "ToolCallStarted" in effects
        assert "ToolCallCompleted" in effects

    @pytest.mark.asyncio
    async def test_mcp_tool_call_recorded_in_result(self, provider, binding, runtime):
        """MCP tool calls should be recorded in ExecutionResult tool_calls and tool_results."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_tool_call("github", "read_file", "mcp_c1", '{"path": "/x"}', "content"),
                    _make_message("Done"),
                ],
            ),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "mcp__github__read_file"
        assert len(result.tool_results) == 1
        assert result.tool_results[0].success

    @pytest.mark.asyncio
    async def test_mcp_tool_call_no_function_call_output_sent(self, provider, binding, runtime):
        """MCP tool calls should NOT add function_call_output to input_items."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_tool_call("github", "read_file", "mcp_c1", '{"path": "/x"}', "content"),
                    _make_message("Done"),
                ],
            ),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Test", binding, runtime)

        # Only one API call should have been made (no continuation needed)
        assert mock_client.responses.create.call_count == 1
        assert result.success


# ---------------------------------------------------------------------------
# Test 3: Mixed transport turn
# ---------------------------------------------------------------------------


class TestMixedTransport:
    @pytest.mark.asyncio
    async def test_mixed_mcp_and_function_call(self, provider, binding, runtime):
        """Response with both mcp_tool_call and function_call items should process both."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_tool_call("github", "search", "mcp_c1", '{"q": "test"}', "results"),
                    _make_function_call("bash", "call_1", '{"command": "echo mixed"}'),
                ],
            ),
            _make_response(response_id="resp_2", output=[_make_message("All done")]),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert result.success
        # Should have both tool calls recorded
        assert len(result.tool_calls) == 2
        tool_names = [tc.name for tc in result.tool_calls]
        assert "mcp__github__search" in tool_names
        assert "bash" in tool_names

        # Second API call should have function_call_output for the bash call only
        second_call_kwargs = mock_client.responses.create.call_args_list[1].kwargs
        second_input = second_call_kwargs.get("input", [])
        assert len(second_input) == 1
        assert second_input[0]["type"] == "function_call_output"
        assert second_input[0]["call_id"] == "call_1"


# ---------------------------------------------------------------------------
# Test 4: MCP-only turn (loop breaks)
# ---------------------------------------------------------------------------


class TestMCPOnlyTurn:
    @pytest.mark.asyncio
    async def test_mcp_only_turn_breaks_loop(self, provider, binding, runtime):
        """Response with mcp_tool_calls only (no func_calls) should break the loop."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_tool_call("github", "read_file", "mcp_c1", "{}", "ok"),
                    _make_message("Result from MCP"),
                ],
            ),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert result.success
        assert result.output_text == "Result from MCP"
        # Only one API call — no continuation
        assert mock_client.responses.create.call_count == 1
        assert result.metadata["turns"] == 1


# ---------------------------------------------------------------------------
# Test 5: Approval request handling
# ---------------------------------------------------------------------------


class TestApprovalRequestHandling:
    @pytest.mark.asyncio
    async def test_approval_request_auto_approved_with_warning(self, provider, binding, runtime, caplog):
        """MCP approval requests should be auto-approved with a WARNING log."""
        mock_client = _make_stream_client(
            # Turn 1: approval request (causes loop to continue)
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_approval_request("approval_1", "github", "write_file"),
                ],
            ),
            # Turn 2: normal completion
            _make_response(response_id="resp_2", output=[_make_message("Approved and done")]),
        )

        with (
            patch("shepherd_providers.openai.provider._get_client", return_value=mock_client),
            caplog.at_level(logging.WARNING, logger="shepherd_providers.openai.provider"),
        ):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert result.success
        assert "Auto-approving" in caplog.text
        assert "approval_1" in caplog.text


# ---------------------------------------------------------------------------
# Test 6: Unknown item type
# ---------------------------------------------------------------------------


class TestUnknownItemType:
    @pytest.mark.asyncio
    async def test_unknown_item_type_logs_warning(self, provider, binding, runtime, caplog):
        """Unrecognized item types should log a warning and not crash."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_unknown_item("quantum_entanglement"),
                    _make_message("Still works"),
                ],
            ),
        )

        with (
            patch("shepherd_providers.openai.provider._get_client", return_value=mock_client),
            caplog.at_level(logging.WARNING, logger="shepherd_providers.openai.provider"),
        ):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert result.success
        assert result.output_text == "Still works"
        assert "Unrecognized output item type" in caplog.text
        assert "quantum_entanglement" in caplog.text


# ---------------------------------------------------------------------------
# Test 7: Tool name normalization
# ---------------------------------------------------------------------------


class TestToolNameNormalization:
    @pytest.mark.asyncio
    async def test_mcp_tool_name_normalized(self, provider, binding, runtime):
        """mcp_tool_call with server_label='github' and name='read_file' -> 'mcp__github__read_file'."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_tool_call("github", "read_file", "mcp_c1", '{"path": "/x"}', "ok"),
                    _make_message("Done"),
                ],
            ),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert result.tool_calls[0].name == "mcp__github__read_file"

        # Also check that the emitted effects use the normalized name
        started_effects = _effects_of_type(runtime, "ToolCallStarted")
        mcp_started = [e for e in started_effects if e.tool_name.startswith("mcp__")]
        assert len(mcp_started) == 1
        assert mcp_started[0].tool_name == "mcp__github__read_file"

    @pytest.mark.asyncio
    async def test_mcp_tool_name_with_different_server(self, provider, binding, runtime):
        """Different server labels produce correct normalized names."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1",
                output=[
                    _make_mcp_tool_call("slack", "send_message", "mcp_c1", "{}", "sent"),
                    _make_message("Done"),
                ],
            ),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk("Test", binding, runtime)

        assert result.tool_calls[0].name == "mcp__slack__send_message"
