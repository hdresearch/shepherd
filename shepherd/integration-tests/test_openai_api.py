"""Integration API regression tests for OpenAI Responses API provider.

These tests call the live OpenAI API and validate behavior documented
in Spikes 2, 9, and 10. They require OPENAI_API_KEY to be set.

Run with:
    OPENAI_API_KEY=sk-... uv run pytest shepherd/integration-tests/test_openai_api.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

SKIP_REASON = "OPENAI_API_KEY not set"


@pytest.fixture
def client():
    """Create an AsyncOpenAI client, skipping if no API key."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip(SKIP_REASON)
    import openai

    return openai.AsyncOpenAI()


# ---------------------------------------------------------------------------
# Spike 2 regressions: basic API contract
# ---------------------------------------------------------------------------


class TestResponsesAPIContract:
    """Spike 2 regressions: basic API contract."""

    @pytest.mark.asyncio
    async def test_simple_response_has_id_and_output(self, client):
        """Response object has .id and .output (Spike 2, Step 1)."""
        response = await client.responses.create(
            model="gpt-4o",
            input="Say hello.",
        )
        assert response.id
        assert isinstance(response.output, list)
        assert len(response.output) > 0

    @pytest.mark.asyncio
    async def test_function_call_output_round_trip(self, client):
        """Tool call → function_call_output → text response (Spike 2, Step 1)."""
        tool = {
            "type": "function",
            "name": "get_fact",
            "description": "Get a fact.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        }

        resp1 = await client.responses.create(
            model="gpt-4o",
            input="Get a fact about cats.",
            tools=[tool],
        )

        func_calls = [item for item in resp1.output if item.type == "function_call"]
        if not func_calls:
            pytest.skip("Model answered directly without tool call")

        fc = func_calls[0]
        assert fc.name == "get_fact"
        assert fc.call_id

        resp2 = await client.responses.create(
            model="gpt-4o",
            input=[{"type": "function_call_output", "call_id": fc.call_id, "output": "Cats sleep 16 hours a day."}],
            previous_response_id=resp1.id,
        )

        text_items = [item for item in resp2.output if item.type == "message"]
        assert len(text_items) > 0


# ---------------------------------------------------------------------------
# Spike 9 regressions: loop termination
# ---------------------------------------------------------------------------


class TestLoopTermination:
    """Spike 9 regressions: loop termination."""

    @pytest.mark.asyncio
    async def test_no_function_calls_means_terminal(self, client):
        """Response with only message items has no function_call items (Spike 9, Step 1)."""
        response = await client.responses.create(
            model="gpt-4o",
            input="What is 2+2?",
        )
        func_calls = [item for item in response.output if item.type == "function_call"]
        assert len(func_calls) == 0


# ---------------------------------------------------------------------------
# Spike 10 regressions: streaming
# ---------------------------------------------------------------------------


class TestStreamingContract:
    """Spike 10 regressions: streaming event model."""

    @pytest.mark.asyncio
    async def test_streaming_event_lifecycle(self, client):
        """Streaming produces created → deltas → completed (Spike 10, Step 1)."""
        event_types: list[str] = []
        response_id = None

        stream = await client.responses.create(
            model="gpt-4o",
            input="Say 'hello'.",
            stream=True,
        )
        async for event in stream:
            event_types.append(event.type)
            if event.type == "response.completed":
                response_id = event.response.id

        assert "response.created" in event_types
        assert "response.completed" in event_types
        assert response_id is not None

    @pytest.mark.asyncio
    async def test_tool_call_via_output_item_done(self, client):
        """Tool dispatch uses output_item.done, not function_call_arguments.done (Spike 10, Step 2)."""
        tool = {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }

        output_item = None
        stream = await client.responses.create(
            model="gpt-4o",
            input="Weather in Tokyo?",
            tools=[tool],
            stream=True,
        )
        async for event in stream:
            if (
                event.type == "response.output_item.done"
                and hasattr(event.item, "type")
                and event.item.type == "function_call"
            ):
                output_item = event.item

        if output_item is None:
            pytest.skip("Model answered directly without tool call")

        assert output_item.name == "get_weather"
        assert output_item.call_id
        assert output_item.arguments

    @pytest.mark.asyncio
    async def test_errors_raise_on_create_not_in_stream(self, client):
        """Invalid previous_response_id raises on create(), not in stream (Spike 10, Step 5)."""
        import openai

        with pytest.raises(openai.BadRequestError):
            await client.responses.create(
                model="gpt-4o",
                input="Hello",
                previous_response_id="resp_nonexistent_12345",
                stream=True,
            )

    @pytest.mark.asyncio
    async def test_session_chaining_with_streaming(self, client):
        """previous_response_id works with stream=True (Spike 10, Step 4)."""
        tool = {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }

        # Turn 1: get tool call
        turn1_id = None
        tool_call_item = None
        stream = await client.responses.create(
            model="gpt-4o",
            input="Weather in Paris?",
            tools=[tool],
            stream=True,
        )
        async for event in stream:
            if (
                event.type == "response.output_item.done"
                and hasattr(event.item, "type")
                and event.item.type == "function_call"
            ):
                tool_call_item = event.item
            elif event.type == "response.completed":
                turn1_id = event.response.id

        if tool_call_item is None:
            pytest.skip("Model answered directly without tool call")

        # Turn 2: feed result with session chaining
        turn2_text = []
        stream2 = await client.responses.create(
            model="gpt-4o",
            input=[{"type": "function_call_output", "call_id": tool_call_item.call_id, "output": '{"temp": "20C"}'}],
            previous_response_id=turn1_id,
            tools=[tool],
            stream=True,
        )
        async for event in stream2:
            if event.type == "response.output_text.delta":
                turn2_text.append(event.delta)

        text = "".join(turn2_text)
        assert text  # Should have produced some text
