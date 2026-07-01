"""Phase 3 tests: SSE streaming and real-time effects.

Tests cover: SSE event consumer (filtering, tool state machine, text streaming,
completion detection, timeout), streaming execution path, and effect types.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.effects import (
    AgentMessage,
    ToolCallCompleted,
    ToolCallStarted,
)
from shepherd_providers.opencode._streaming import SSEConsumer
from shepherd_providers.opencode.provider import OpenCodeProvider

# ---------------------------------------------------------------------------
# Helpers: Mock SSE events
# ---------------------------------------------------------------------------


def _make_event(event_type: str, properties: Any = None) -> MagicMock:
    e = MagicMock()
    e.type = event_type
    e.properties = properties
    return e


def _make_session_idle(session_id: str) -> MagicMock:
    props = MagicMock()
    props.session_id = session_id
    return _make_event("session.idle", props)


def _make_session_error(session_id: str, error: str) -> MagicMock:
    props = MagicMock()
    props.session_id = session_id
    props.error = error
    return _make_event("session.error", props)


def _make_text_part_updated(session_id: str, text: str) -> MagicMock:
    part = MagicMock()
    part.type = "text"
    part.session_id = session_id
    part.text = text
    props = MagicMock()
    props.part = part
    return _make_event("message.part.updated", props)


def _make_tool_part_updated(
    session_id: str,
    part_id: str,
    tool_name: str,
    status: str,
    *,
    call_id: str = "",
    input_data: dict | None = None,
    output: str = "",
) -> MagicMock:
    state = MagicMock()
    state.status = status
    state.input = input_data or {}
    state.output = output

    part = MagicMock()
    part.type = "tool"
    part.session_id = session_id
    part.id = part_id
    part.call_id = call_id or part_id
    part.tool = tool_name
    part.state = state

    props = MagicMock()
    props.part = part
    return _make_event("message.part.updated", props)


async def _async_iter(events: list[Any]):
    """Create an async iterator from a list."""
    for e in events:
        yield e


# ---------------------------------------------------------------------------
# TestSSEConsumer
# ---------------------------------------------------------------------------


class TestSSEConsumerFiltering:
    """Events from other sessions should be ignored."""

    @pytest.mark.asyncio
    async def test_ignores_other_session_events(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="my-session", runtime=runtime, provider_id="test")

        events = [
            _make_text_part_updated("other-session", "Hello"),
            _make_session_idle("my-session"),
        ]

        result = await consumer.consume(_async_iter(events))
        assert result.completed is True
        assert result.output_text == ""  # Text from other session ignored

    @pytest.mark.asyncio
    async def test_ignores_other_session_idle(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="my-session", runtime=runtime, provider_id="test")

        events = [
            _make_session_idle("other-session"),
            _make_text_part_updated("my-session", "Hi"),
            _make_session_idle("my-session"),
        ]

        result = await consumer.consume(_async_iter(events))
        assert result.completed is True
        assert result.output_text == "Hi"


class TestSSEConsumerTextStreaming:
    """Text parts should emit partial AgentMessage deltas."""

    @pytest.mark.asyncio
    async def test_text_deltas(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [
            _make_text_part_updated("s1", "Hel"),
            _make_text_part_updated("s1", "Hello"),
            _make_text_part_updated("s1", "Hello world"),
            _make_session_idle("s1"),
        ]

        result = await consumer.consume(_async_iter(events))

        # Should emit 3 partial AgentMessage effects (one per text change)
        msg_calls = [c.args[0] for c in runtime.effects.emit.call_args_list if isinstance(c.args[0], AgentMessage)]
        assert len(msg_calls) == 3
        assert msg_calls[0].content == "Hel"
        assert msg_calls[0].is_partial is True
        assert msg_calls[1].content == "lo"  # Delta from "Hel" to "Hello"
        assert msg_calls[2].content == " world"  # Delta from "Hello" to "Hello world"

        # Final accumulated text
        assert result.output_text == "Hello world"

    @pytest.mark.asyncio
    async def test_duplicate_text_ignored(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [
            _make_text_part_updated("s1", "Hello"),
            _make_text_part_updated("s1", "Hello"),  # Duplicate
            _make_session_idle("s1"),
        ]

        result = await consumer.consume(_async_iter(events))
        msg_calls = [c.args[0] for c in runtime.effects.emit.call_args_list if isinstance(c.args[0], AgentMessage)]
        assert len(msg_calls) == 1  # Only one emission, duplicate ignored


class TestSSEConsumerToolStateMachine:
    """Tool parts should emit ToolCallStarted/Completed at correct transitions."""

    @pytest.mark.asyncio
    async def test_pending_to_completed(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [
            _make_tool_part_updated("s1", "t1", "bash", "pending"),
            _make_tool_part_updated("s1", "t1", "bash", "running", input_data={"command": "echo hi"}),
            _make_tool_part_updated("s1", "t1", "bash", "completed", output="hi\n"),
            _make_session_idle("s1"),
        ]

        result = await consumer.consume(_async_iter(events))

        started = [c.args[0] for c in runtime.effects.emit.call_args_list if isinstance(c.args[0], ToolCallStarted)]
        completed = [c.args[0] for c in runtime.effects.emit.call_args_list if isinstance(c.args[0], ToolCallCompleted)]

        assert len(started) == 1
        assert started[0].tool_name == "bash"
        assert len(completed) == 1
        assert completed[0].success is True
        assert completed[0].output == "hi\n"

        assert result.tool_calls_started == 1
        assert result.tool_calls_completed == 1

    @pytest.mark.asyncio
    async def test_completed_without_pending(self) -> None:
        """If we only see completed (missed pending/running), still emit both."""
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [
            _make_tool_part_updated("s1", "t1", "read", "completed", output="file contents"),
            _make_session_idle("s1"),
        ]

        result = await consumer.consume(_async_iter(events))

        started = [c.args[0] for c in runtime.effects.emit.call_args_list if isinstance(c.args[0], ToolCallStarted)]
        completed = [c.args[0] for c in runtime.effects.emit.call_args_list if isinstance(c.args[0], ToolCallCompleted)]

        assert len(started) == 1  # Auto-emitted on completed
        assert len(completed) == 1

    @pytest.mark.asyncio
    async def test_tool_error(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [
            _make_tool_part_updated("s1", "t1", "bash", "pending"),
            _make_tool_part_updated("s1", "t1", "bash", "error"),
            _make_session_idle("s1"),
        ]

        result = await consumer.consume(_async_iter(events))

        completed = [c.args[0] for c in runtime.effects.emit.call_args_list if isinstance(c.args[0], ToolCallCompleted)]
        assert len(completed) == 1
        assert completed[0].success is False

    @pytest.mark.asyncio
    async def test_multiple_tools(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [
            _make_tool_part_updated("s1", "t1", "bash", "pending"),
            _make_tool_part_updated("s1", "t1", "bash", "completed", output="ok"),
            _make_tool_part_updated("s1", "t2", "write", "pending"),
            _make_tool_part_updated("s1", "t2", "write", "completed", output="wrote file"),
            _make_session_idle("s1"),
        ]

        result = await consumer.consume(_async_iter(events))
        assert result.tool_calls_started == 2
        assert result.tool_calls_completed == 2


class TestSSEConsumerCompletion:
    """Completion and error signals."""

    @pytest.mark.asyncio
    async def test_session_idle_completes(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [_make_session_idle("s1")]
        result = await consumer.consume(_async_iter(events))
        assert result.completed is True

    @pytest.mark.asyncio
    async def test_session_error(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        events = [_make_session_error("s1", "Model rate limited")]
        result = await consumer.consume(_async_iter(events))
        assert result.error == "Model rate limited"

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        consumer = SSEConsumer(session_id="s1", runtime=runtime, provider_id="test")

        async def slow_stream():
            await asyncio.sleep(10)  # Will be interrupted by timeout
            return
            yield  # Make it an async generator

        result = await consumer.consume(slow_stream(), timeout=0.1)
        assert result.error is not None
        assert "Timed out" in result.error


# ---------------------------------------------------------------------------
# TestStreamingExecution
# ---------------------------------------------------------------------------


class TestStreamingExecution:
    """Test the streaming execution path in the provider."""

    @pytest.mark.asyncio
    async def test_streaming_emits_real_time_effects(self) -> None:
        """Streaming path should emit ToolCallStarted/Completed, not ToolCallBatch."""
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        provider = OpenCodeProvider(name="test", streaming=True)

        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session

        # subscribe_events is a sync method returning an async stream
        events = [
            _make_text_part_updated("s1", "Hello"),
            _make_tool_part_updated("s1", "t1", "bash", "pending"),
            _make_tool_part_updated("s1", "t1", "bash", "completed", output="ok"),
            _make_session_idle("s1"),
        ]
        client.subscribe_events = AsyncMock(return_value=_async_iter(events))

        # chat completes when called
        client.send_message = AsyncMock(return_value=None)

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            result = await provider.execute_sdk("Do something", None, runtime)

        assert result.success is True
        assert result.output_text == "Hello"
        assert result.metadata.get("streaming") is True

        # Should have real-time effects, not ToolCallBatch
        effect_types = {type(c.args[0]).__name__ for c in runtime.effects.emit.call_args_list}
        assert "ToolCallStarted" in effect_types
        assert "ToolCallCompleted" in effect_types
        assert "ToolCallBatch" not in effect_types

    @pytest.mark.asyncio
    async def test_non_streaming_uses_batch(self) -> None:
        """Non-streaming path should use ToolCallBatch (Phase 1 behavior)."""
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        provider = OpenCodeProvider(name="test", streaming=False)

        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session

        chat_result = MagicMock()
        chat_result.parts = [
            {
                "type": "tool-invocation",
                "tool": "bash",
                "call_id": "tc1",
                "id": "tc1",
                "input": "echo hi",
                "output": "hi",
            },
            {"type": "text", "text": "Done."},
        ]
        client.send_message.return_value = chat_result

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            result = await provider.execute_sdk("Do something", None, runtime)

        effect_types = {type(c.args[0]).__name__ for c in runtime.effects.emit.call_args_list}
        assert "ToolCallBatch" in effect_types
        assert "ToolCallStarted" not in effect_types

    @pytest.mark.asyncio
    async def test_streaming_serialization_round_trip(self) -> None:
        """streaming field should survive to_config/from_config."""
        p = OpenCodeProvider(name="test", streaming=False)
        config = p.to_config()
        assert config["streaming"] is False

        p2 = OpenCodeProvider.from_config(config)
        assert p2.streaming is False

        # Default (True) is not serialized
        p3 = OpenCodeProvider(name="test", streaming=True)
        config3 = p3.to_config()
        assert "streaming" not in config3

        p4 = OpenCodeProvider.from_config(config3)
        assert p4.streaming is True


class TestVerboseStreaming:
    """Verbose formatter should receive streaming callbacks."""

    @pytest.mark.asyncio
    async def test_formatter_receives_text_delta(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        formatter = MagicMock()
        consumer = SSEConsumer(
            session_id="s1",
            runtime=runtime,
            provider_id="test",
            formatter=formatter,
        )

        events = [
            _make_text_part_updated("s1", "Hel"),
            _make_text_part_updated("s1", "Hello"),
            _make_session_idle("s1"),
        ]

        await consumer.consume(_async_iter(events))

        # Should have received two delta calls
        assert formatter.on_text_delta.call_count == 2
        formatter.on_text_delta.assert_any_call("Hel")
        formatter.on_text_delta.assert_any_call("lo")

    @pytest.mark.asyncio
    async def test_formatter_receives_tool_callbacks(self) -> None:
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        formatter = MagicMock()
        consumer = SSEConsumer(
            session_id="s1",
            runtime=runtime,
            provider_id="test",
            formatter=formatter,
        )

        events = [
            _make_tool_part_updated("s1", "t1", "bash", "pending"),
            _make_tool_part_updated("s1", "t1", "bash", "completed", output="done"),
            _make_session_idle("s1"),
        ]

        await consumer.consume(_async_iter(events))

        formatter.on_tool_call_started.assert_called_once()
        formatter.on_tool_call_completed.assert_called_once()
