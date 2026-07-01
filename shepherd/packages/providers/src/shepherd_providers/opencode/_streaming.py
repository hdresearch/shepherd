"""SSE event consumer for real-time OpenCode streaming.

Consumes the global SSE event stream from `client.subscribe_events()`,
filters events by session_id, and emits real-time effects and verbose
formatter callbacks.

Event lifecycle for a tool call:
    message.part.updated (ToolPart, state=pending)   → ToolCallStarted
    message.part.updated (ToolPart, state=running)    → (progress, ignored)
    message.part.updated (ToolPart, state=completed)  → ToolCallCompleted

Event lifecycle for text output:
    message.part.updated (TextPart, text="")          → (created, ignored)
    message.part.updated (TextPart, text="Hello")     → AgentMessage(is_partial=True)

Completion:
    session.idle (matching session_id)                → done
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from shepherd_core.effects import (
    AgentMessage,
    ToolCallCompleted,
    ToolCallStarted,
)

logger = logging.getLogger(__name__)


@dataclass
class StreamingResult:
    """Accumulated result from SSE event consumption."""

    output_text: str = ""
    thinking: str = ""
    tool_calls_started: int = 0
    tool_calls_completed: int = 0
    completed: bool = False
    error: str | None = None
    assistant_message: Any = None  # Last AssistantMessage from message.updated


@dataclass
class SSEConsumer:
    """Consumes SSE events for a specific session and emits effects.

    Attributes:
        session_id: The session to filter events for.
        runtime: ProviderRuntime for effect emission.
        task_name: Task name for effect attribution.
        provider_id: Provider ID for effect attribution.
        formatter: Optional VerboseFormatter for real-time output.
    """

    session_id: str
    runtime: Any
    task_name: str | None = None
    provider_id: str = ""
    formatter: Any = None

    # Internal state
    _last_text: str = field(default="", repr=False)
    _seen_tools: dict[str, str] = field(default_factory=dict, repr=False)  # part_id → status
    _result: StreamingResult = field(default_factory=StreamingResult, repr=False)

    def _get_session_id(self, event: Any) -> str | None:
        """Extract session_id from an event's properties."""
        props = getattr(event, "properties", None)
        if props is None:
            return None
        return getattr(props, "session_id", None) or getattr(props, "sessionID", None)

    def _get_part_session_id(self, part: Any) -> str | None:
        """Extract session_id from a message part."""
        return getattr(part, "session_id", None) or getattr(part, "sessionID", None)

    async def consume(self, event_stream: Any, timeout: float = 300.0) -> StreamingResult:
        """Consume events from the stream until session.idle or timeout.

        Args:
            event_stream: AsyncStream from client.subscribe_events().
            timeout: Maximum time to wait for completion.

        Returns:
            StreamingResult with accumulated output.
        """
        import asyncio

        try:
            async with asyncio.timeout(timeout):
                async for event in event_stream:
                    event_type = getattr(event, "type", None)

                    if event_type == "session.idle":
                        sid = self._get_session_id(event)
                        if sid == self.session_id:
                            self._result.completed = True
                            return self._result

                    elif event_type == "session.error":
                        sid = self._get_session_id(event)
                        if sid == self.session_id:
                            props = getattr(event, "properties", None)
                            err = getattr(props, "error", None) if props else None
                            self._result.error = str(err) if err else "Unknown session error"
                            return self._result

                    elif event_type == "message.updated":
                        self._handle_message_updated(event)

                    elif event_type == "message.part.updated":
                        self._handle_part_updated(event)

        except TimeoutError:
            logger.warning(f"SSE stream timed out after {timeout}s for session {self.session_id}")
            self._result.error = f"Timed out after {timeout}s"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"SSE stream error for session {self.session_id}: {e}")
            self._result.error = f"Streaming error: {e}"

        return self._result

    def _handle_message_updated(self, event: Any) -> None:
        """Handle a message.updated event to capture AssistantMessage metadata.

        The message.updated event carries the full AssistantMessage (with
        tokens, cost, time) in properties.info. We capture the last assistant
        message for our session so the provider can emit LLMResponseReceived.
        """
        props = getattr(event, "properties", None)
        if props is None:
            return

        info = getattr(props, "info", None)
        if info is None:
            return

        # Filter by session and role
        msg_session = getattr(info, "session_id", None)
        if msg_session and msg_session != self.session_id:
            return

        role = getattr(info, "role", None)
        if role == "assistant":
            self._result.assistant_message = info

    def _handle_part_updated(self, event: Any) -> None:
        """Handle a message.part.updated event."""
        props = getattr(event, "properties", None)
        if props is None:
            return

        part = getattr(props, "part", None)
        if part is None:
            return

        # Filter by session
        part_sid = self._get_part_session_id(part)
        if part_sid and part_sid != self.session_id:
            return

        part_type = getattr(part, "type", None)

        if part_type == "text":
            self._handle_text_part(part)
        elif part_type == "tool":
            self._handle_tool_part(part)

    def _handle_text_part(self, part: Any) -> None:
        """Handle a TextPart update — emit partial AgentMessage."""
        text = getattr(part, "text", "") or ""
        if not text or text == self._last_text:
            return

        # Emit the new delta as a partial effect
        delta = text[len(self._last_text) :]
        self._last_text = text

        if delta:
            self.runtime.effects.emit(
                AgentMessage(
                    task_name=self.task_name,
                    provider_id=self.provider_id,
                    content=delta,
                    is_partial=True,
                )
            )
            if self.formatter:
                self.formatter.on_text_delta(delta)

        # Update accumulated result
        self._result.output_text = text

    def _handle_tool_part(self, part: Any) -> None:
        """Handle a ToolPart update — emit ToolCallStarted/Completed."""
        state = getattr(part, "state", None)
        if state is None:
            return

        status = getattr(state, "status", None)
        part_id = getattr(part, "id", "")
        tool_name = getattr(part, "tool", "")
        prev_status = self._seen_tools.get(part_id)

        if status == "pending" and prev_status is None:
            # First time seeing this tool — emit ToolCallStarted
            self._seen_tools[part_id] = "pending"
            call_id = getattr(part, "call_id", part_id)

            input_data = getattr(state, "input", None)
            params = dict(input_data) if isinstance(input_data, dict) else {}

            self.runtime.effects.emit(
                ToolCallStarted(
                    task_name=self.task_name,
                    provider_id=self.provider_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    params=params,
                )
            )
            if self.formatter:
                self.formatter.on_tool_call_started(tool_name, str(params))

            self._result.tool_calls_started += 1

        elif status == "running" and prev_status in ("pending", "running"):
            # Running transition or progress update — just track state.
            # ToolCallStarted was already emitted on pending.
            self._seen_tools[part_id] = "running"

        elif status == "running" and prev_status is None:
            # Skipped pending, went straight to running — emit ToolCallStarted
            self._seen_tools[part_id] = "running"
            call_id = getattr(part, "call_id", part_id)
            input_data = getattr(state, "input", None)
            params = dict(input_data) if isinstance(input_data, dict) else {}

            self.runtime.effects.emit(
                ToolCallStarted(
                    task_name=self.task_name,
                    provider_id=self.provider_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    params=params,
                )
            )
            if self.formatter:
                self.formatter.on_tool_call_started(tool_name, str(params))

            self._result.tool_calls_started += 1

        elif status == "completed" and prev_status in ("pending", "running", None):
            self._seen_tools[part_id] = "completed"
            call_id = getattr(part, "call_id", part_id)
            output = getattr(state, "output", "") or ""

            # If we never saw pending/running, emit ToolCallStarted first
            if prev_status is None:
                input_data = getattr(state, "input", None)
                params = dict(input_data) if isinstance(input_data, dict) else {}
                self.runtime.effects.emit(
                    ToolCallStarted(
                        task_name=self.task_name,
                        provider_id=self.provider_id,
                        tool_call_id=call_id,
                        tool_name=tool_name,
                        params=params,
                    )
                )
                self._result.tool_calls_started += 1

            self.runtime.effects.emit(
                ToolCallCompleted(
                    task_name=self.task_name,
                    provider_id=self.provider_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    success=True,
                    output=output,
                )
            )
            if self.formatter:
                self.formatter.on_tool_call_completed(tool_name, output[:200], False)

            self._result.tool_calls_completed += 1

        elif status == "error":
            self._seen_tools[part_id] = "error"
            call_id = getattr(part, "call_id", part_id)
            error_msg = str(getattr(state, "error", "Tool execution failed"))

            self.runtime.effects.emit(
                ToolCallCompleted(
                    task_name=self.task_name,
                    provider_id=self.provider_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    success=False,
                    output=error_msg,
                )
            )
            if self.formatter:
                self.formatter.on_tool_call_completed(tool_name, error_msg[:200], True)

            self._result.tool_calls_completed += 1


__all__ = ["SSEConsumer", "StreamingResult"]
