"""Unit tests for OpenAIProvider.

Tests the agent loop, tool dispatch, binding translation, effect emission,
and session handling using mocked openai SDK responses. No API calls.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.provider import DefaultProviderRuntime
from shepherd_core.types import (
    ProviderBinding,
    ToolDefinition,
)
from shepherd_providers.openai.provider import OpenAIProvider

# ---------------------------------------------------------------------------
# Helpers for building mock openai response objects
# ---------------------------------------------------------------------------


def _make_response(
    response_id: str = "resp_test_123",
    output: list | None = None,
    output_text: str = "",
) -> SimpleNamespace:
    """Build a mock openai Response object."""
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


def _make_message(text: str = "Hello") -> SimpleNamespace:
    content = [SimpleNamespace(text=text)]
    return SimpleNamespace(type="message", content=content)


def _make_reasoning(summary_text: str = "I think therefore I am") -> SimpleNamespace:
    summary = [SimpleNamespace(text=summary_text, type="summary_text")]
    return SimpleNamespace(type="reasoning", id="reason_1", summary=summary, content=None)


async def _mock_stream_from_response(response: SimpleNamespace):
    """Convert a mock Response into a mock SSE stream.

    Yields output_item.done events for each output item, then a
    response.completed event with the full response. This lets existing
    tests work unchanged with the streaming-based execute_sdk.
    """
    for idx, item in enumerate(response.output):
        yield SimpleNamespace(type="response.output_item.done", item=item, output_index=idx, sequence_number=idx)
    yield SimpleNamespace(type="response.completed", response=response, sequence_number=len(response.output))


def _make_stream_client(*responses: SimpleNamespace) -> MagicMock:
    """Build a mock client whose responses.create returns async streams.

    Each call to create() consumes the next response in order.
    """
    streams = iter([_mock_stream_from_response(r) for r in responses])
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=lambda **kw: next(streams))
    return mock_client


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
def scope():
    return MagicMock()


@pytest.fixture(autouse=True)
def fake_openai_module(monkeypatch):
    """Install a minimal openai module so unit tests stay offline."""

    class BadRequestError(Exception):
        def __init__(self, message: str, *, response=None, body=None):
            super().__init__(message)
            self.response = response
            self.body = body

    class APIError(Exception):
        def __init__(self, message: str, *, request=None, body=None):
            super().__init__(message)
            self.request = request
            self.body = body

    class AsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_module = ModuleType("openai")
    fake_module.BadRequestError = BadRequestError
    fake_module.APIError = APIError
    fake_module.AsyncOpenAI = AsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)


def _effect_types(scope_mock: MagicMock) -> list[str]:
    """Extract effect type names from scope.emit calls."""
    return [c.args[0].__class__.__name__ for c in scope_mock.emit.call_args_list]


# ---------------------------------------------------------------------------
# Binding translation
# ---------------------------------------------------------------------------


class TestBindingTranslation:
    def test_none_binding(self, provider):
        result = provider._translate_binding(None)
        assert result["model"] == "gpt-4o"

    def test_instructions_from_context(self, provider):
        binding = ProviderBinding(
            context_id="test",
            context_description="You are a helper.",
            system_prompt_additions=("Be concise.",),
            trust_level="standard",
        )
        result = provider._translate_binding(binding)
        assert "You are a helper." in result["instructions"]
        assert "Be concise." in result["instructions"]

    def test_structured_output_format(self, provider):
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            output_format={
                "type": "json_schema",
                "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
            },
        )
        result = provider._translate_binding(binding)
        assert result["text"]["format"]["type"] == "json_schema"
        assert result["text"]["format"]["name"] == "test"
        assert "schema" in result["text"]["format"]

    def test_session_id_passed(self, provider):
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            session_id="resp_abc",
            session_isolation="shared",
        )
        result = provider._translate_binding(binding)
        assert result["previous_response_id"] == "resp_abc"

    def test_session_id_omitted_for_isolated(self, provider):
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            session_id="resp_abc",
            session_isolation="isolated",
        )
        result = provider._translate_binding(binding)
        assert result["previous_response_id"] is None

    def test_session_id_passed_for_forked(self, provider):
        binding = ProviderBinding(
            context_id="test",
            trust_level="standard",
            session_id="resp_abc",
            session_isolation="forked",
        )
        result = provider._translate_binding(binding)
        assert result["previous_response_id"] == "resp_abc"


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_bash_capability(self, provider):
        binding = ProviderBinding(context_id="t", capabilities=frozenset({"bash"}), trust_level="standard")
        tools = provider._build_tool_schemas(binding)
        names = [t["name"] for t in tools if t.get("type") == "function"]
        assert "bash" in names

    def test_read_capability_includes_search(self, provider):
        binding = ProviderBinding(context_id="t", capabilities=frozenset({"read"}), trust_level="standard")
        tools = provider._build_tool_schemas(binding)
        names = [t["name"] for t in tools if t.get("type") == "function"]
        assert "read_file" in names
        assert "search_files" in names
        assert "search_content" in names

    def test_write_capability_includes_edit(self, provider):
        binding = ProviderBinding(context_id="t", capabilities=frozenset({"write"}), trust_level="standard")
        tools = provider._build_tool_schemas(binding)
        names = [t["name"] for t in tools if t.get("type") == "function"]
        assert "write_file" in names
        assert "edit_file" in names

    def test_web_capability_adds_web_search_preview(self, provider):
        binding = ProviderBinding(context_id="t", capabilities=frozenset({"web"}), trust_level="standard")
        tools = provider._build_tool_schemas(binding)
        web_tools = [t for t in tools if t.get("type") == "web_search_preview"]
        assert len(web_tools) == 1

    def test_custom_tools_added(self, provider):
        tool_def = ToolDefinition(
            name="my_tool",
            description="A custom tool",
            parameters_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            handler=lambda args: "ok",
        )
        binding = ProviderBinding(
            context_id="t",
            capabilities=frozenset(),
            trust_level="standard",
            custom_tools=(tool_def,),
        )
        tools = provider._build_tool_schemas(binding)
        names = [t.get("name") for t in tools]
        assert "my_tool" in names
        assert "my_tool" in provider._tool_handlers

    def test_custom_tools_reset_between_calls(self, provider):
        """_tool_handlers should match the CURRENT binding, not accumulate."""
        tool1 = ToolDefinition(
            name="tool_a",
            description="A",
            parameters_schema={"type": "object", "properties": {}},
            handler=lambda args: "a",
        )
        tool2 = ToolDefinition(
            name="tool_b",
            description="B",
            parameters_schema={"type": "object", "properties": {}},
            handler=lambda args: "b",
        )

        binding1 = ProviderBinding(context_id="t", trust_level="standard", custom_tools=(tool1,))
        provider._build_tool_schemas(binding1)
        assert "tool_a" in provider._tool_handlers

        binding2 = ProviderBinding(context_id="t", trust_level="standard", custom_tools=(tool2,))
        provider._build_tool_schemas(binding2)
        assert "tool_b" in provider._tool_handlers
        assert "tool_a" not in provider._tool_handlers

    def test_empty_custom_tools_clears_handlers(self, provider):
        tool1 = ToolDefinition(
            name="tool_a",
            description="A",
            parameters_schema={"type": "object", "properties": {}},
            handler=lambda args: "a",
        )
        binding1 = ProviderBinding(context_id="t", trust_level="standard", custom_tools=(tool1,))
        provider._build_tool_schemas(binding1)
        assert "tool_a" in provider._tool_handlers

        binding2 = ProviderBinding(context_id="t", trust_level="standard")
        provider._build_tool_schemas(binding2)
        assert provider._tool_handlers == {}


# ---------------------------------------------------------------------------
# Built-in tool dispatch
# ---------------------------------------------------------------------------


class TestBuiltinToolDispatch:
    def test_bash_dispatch(self, provider):
        result, success = provider._dispatch_builtin_tool("bash", {"command": "echo hello"}, None)
        assert success
        assert "hello" in result

    def test_read_file_dispatch(self, provider):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            result, success = provider._dispatch_builtin_tool("read_file", {"path": f.name}, None)
        assert success
        assert "test content" in result
        Path(f.name).unlink()

    def test_write_file_dispatch(self, provider):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        result, success = provider._dispatch_builtin_tool("write_file", {"path": path, "content": "new content"}, None)
        assert success
        assert "Written" in result
        with open(path) as f:
            assert f.read() == "new content"
        Path(path).unlink()

    def test_search_files_dispatch(self, provider):
        result, success = provider._dispatch_builtin_tool(
            "search_files", {"pattern": "**/*.py", "path": "design/spikes"}, None
        )
        assert success
        assert ".py" in result

    def test_search_content_dispatch(self, provider):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "example.py"
            target.write_text("class OpenAIProvider:\n    pass\n")
            result, success = provider._dispatch_builtin_tool(
                "search_content", {"pattern": "OpenAIProvider", "path": tmpdir}, None
            )
            assert success
            assert "OpenAIProvider" in result

    def test_edit_file_dispatch(self, provider):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world\nfoo bar\n")
            path = f.name
        _result, success = provider._dispatch_builtin_tool(
            "edit_file", {"path": path, "old_text": "foo bar", "new_text": "baz qux"}, None
        )
        assert success
        with open(path) as f:
            assert "baz qux" in f.read()
        Path(path).unlink()

    def test_edit_file_not_found(self, provider):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world\n")
            path = f.name
        result, success = provider._dispatch_builtin_tool(
            "edit_file", {"path": path, "old_text": "nonexistent", "new_text": "x"}, None
        )
        assert success  # dispatch succeeded, but edit returned error message
        assert "not found" in result
        Path(path).unlink()

    def test_edit_file_multiple_matches(self, provider):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("aaa\naaa\n")
            path = f.name
        result, success = provider._dispatch_builtin_tool(
            "edit_file", {"path": path, "old_text": "aaa", "new_text": "bbb"}, None
        )
        assert success
        assert "appears 2 times" in result
        Path(path).unlink()

    def test_unknown_tool(self, provider):
        result, success = provider._dispatch_builtin_tool("nonexistent", {}, None)
        assert not success
        assert "Unknown" in result


# ---------------------------------------------------------------------------
# Agent loop (mocked client)
# ---------------------------------------------------------------------------


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_simple_text_response(self, provider, binding, scope):
        """Model returns text, no tool calls — loop exits after 1 turn."""
        mock_client = _make_stream_client(_make_response(output=[_make_message("The answer is 42")]))

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "What is 6*7?", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert result.output_text == "The answer is 42"
        assert result.metadata["turns"] == 1
        assert len(result.tool_calls) == 0

    @pytest.mark.asyncio
    async def test_tool_call_round_trip(self, provider, binding, scope):
        """Model calls a tool, gets result, then produces text."""
        mock_client = _make_stream_client(
            _make_response(
                response_id="resp_1", output=[_make_function_call("bash", "call_1", '{"command": "echo hi"}')]
            ),
            _make_response(response_id="resp_2", output=[_make_message("Command returned: hi")]),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Run echo hi", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert result.metadata["turns"] == 2
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "bash"

    @pytest.mark.asyncio
    async def test_max_turns_enforced(self, provider, binding, scope):
        """Loop exits after max_turns with success=False."""
        provider.max_turns = 2
        call_resp = _make_response(output=[_make_function_call()])
        # Provide enough responses for max_turns iterations
        mock_client = _make_stream_client(call_resp, _make_response(output=[_make_function_call()]))

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Loop forever", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert not result.success
        assert result.metadata["error_type"] == "max_turns"

    @pytest.mark.asyncio
    async def test_effects_emitted(self, provider, binding, scope):
        """Verify PromptSent, ToolCallStarted, ToolCallCompleted, AgentMessage are emitted."""
        mock_client = _make_stream_client(
            _make_response(output=[_make_function_call()]),
            _make_response(output=[_make_message("Done")]),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            await provider.execute_sdk("Test", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test"))

        effects = _effect_types(scope)
        assert "PromptSent" in effects
        assert "ToolCallStarted" in effects
        assert "ToolCallCompleted" in effects
        assert "AgentMessage" in effects

    @pytest.mark.asyncio
    async def test_reasoning_items_emitted_as_thinking(self, provider, binding, scope):
        """Reasoning items should emit AgentThinking effects."""
        mock_client = _make_stream_client(
            _make_response(output=[_make_reasoning("Let me think about this..."), _make_message("The answer is 42")])
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Think hard", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        effects = _effect_types(scope)
        assert "AgentThinking" in effects
        assert result.metadata["thinking_length"] > 0

    @pytest.mark.asyncio
    async def test_session_id_returned(self, provider, binding, scope):
        """session_id should be the last response.id."""
        mock_client = _make_stream_client(_make_response(response_id="resp_final_456", output=[_make_message("ok")]))

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Hi", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.session_id == "resp_final_456"

    @pytest.mark.asyncio
    async def test_rejected_tool_feeds_back_error(self, provider, scope):
        """Rejected tool calls should get error function_call_output fed back."""
        binding = ProviderBinding(
            context_id="test",
            capabilities=frozenset({"read"}),
            trust_level="standard",
        )
        mock_client = _make_stream_client(
            _make_response(output=[_make_function_call("bash", "call_1", '{"command": "rm -rf /"}')]),
            _make_response(output=[_make_message("I can't do that")]),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Delete everything", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert len(result.tool_calls) == 0

        # The second API call should have received the rejection error
        second_call_kwargs = mock_client.responses.create.call_args_list[1].kwargs
        second_call_input = second_call_kwargs.get("input")
        assert isinstance(second_call_input, list)
        assert second_call_input[0]["type"] == "function_call_output"
        assert "rejected" in second_call_input[0]["output"].lower()

    @pytest.mark.asyncio
    async def test_structured_output_parsed(self, provider, scope):
        """Structured output should be parsed from output_text when output_format is set."""
        binding = ProviderBinding(
            context_id="test",
            capabilities=frozenset(),
            trust_level="standard",
            output_format={
                "type": "json_schema",
                "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
            },
        )
        mock_client = _make_stream_client(_make_response(output=[_make_message('{"x": "hello"}')]))

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Test", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.structured_output == {"x": "hello"}

    @pytest.mark.asyncio
    async def test_parameters_repassed_every_turn(self, provider, binding, scope):
        """tools, text, and truncation must be re-passed on every API call."""
        mock_client = _make_stream_client(
            _make_response(output=[_make_function_call()]),
            _make_response(output=[_make_message("done")]),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            await provider.execute_sdk("Test", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test"))

        for call in mock_client.responses.create.call_args_list:
            kwargs = call.kwargs
            assert "tools" in kwargs
            assert kwargs["truncation"] == "auto"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_turn(self, provider, binding, scope):
        """Multiple function_call items in one turn should all be dispatched."""
        mock_client = _make_stream_client(
            _make_response(
                output=[
                    _make_function_call("bash", "call_1", '{"command": "echo a"}'),
                    _make_function_call("bash", "call_2", '{"command": "echo b"}'),
                    _make_function_call("bash", "call_3", '{"command": "echo c"}'),
                ]
            ),
            _make_response(output=[_make_message("All done")]),
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Run three commands", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert len(result.tool_calls) == 3
        # Second call should have 3 function_call_output items
        second_call_kwargs = mock_client.responses.create.call_args_list[1].kwargs
        outputs = second_call_kwargs.get("input", [])
        assert len(outputs) == 3
        assert all(o["type"] == "function_call_output" for o in outputs)


# ---------------------------------------------------------------------------
# Streaming (delta + done events)
# ---------------------------------------------------------------------------


def _make_text_delta(delta: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=delta)


def _make_reasoning_delta(delta: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.reasoning_summary_text.delta", delta=delta)


def _make_completed(response_id: str = "resp_stream_1") -> SimpleNamespace:
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(id=response_id),
    )


def _make_output_item_done(item: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_item.done", item=item, output_index=0, sequence_number=0)


async def _raw_stream(events: list[SimpleNamespace]):
    """Async generator yielding events directly (no response conversion)."""
    for e in events:
        yield e


def _make_raw_stream_client(*event_sequences: list[SimpleNamespace]) -> MagicMock:
    """Build a mock client whose create() returns raw event streams."""
    streams = iter([_raw_stream(seq) for seq in event_sequences])
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=lambda **kw: next(streams))
    return mock_client


class TestStreaming:
    @pytest.fixture
    def provider(self):
        return OpenAIProvider(name="test", model="gpt-4o", max_turns=5)

    @pytest.fixture
    def binding(self):
        return ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset({"bash", "read", "write"}),
            trust_level="standard",
        )

    @pytest.fixture
    def scope(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_text_deltas_emit_partial_effects(self, provider, binding, scope):
        """Text delta events should emit AgentMessage with is_partial=True."""
        mock_client = _make_raw_stream_client(
            [
                _make_text_delta("Hello, "),
                _make_text_delta("world!"),
                _make_output_item_done(_make_message("Hello, world!")),
                _make_completed("resp_1"),
            ]
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Greet", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert result.output_text == "Hello, world!"

        # Check that partial effects were emitted
        partial_msgs = [
            c.args[0]
            for c in scope.emit.call_args_list
            if c.args[0].__class__.__name__ == "AgentMessage" and c.args[0].is_partial
        ]
        assert len(partial_msgs) == 2

    @pytest.mark.asyncio
    async def test_reasoning_deltas_emit_partial_thinking(self, provider, binding, scope):
        """Reasoning delta events should emit AgentThinking with is_partial=True."""
        mock_client = _make_raw_stream_client(
            [
                _make_reasoning_delta("Let me think..."),
                _make_output_item_done(_make_reasoning("Let me think...")),
                _make_text_delta("42"),
                _make_output_item_done(_make_message("42")),
                _make_completed("resp_1"),
            ]
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Think", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert result.metadata["thinking_length"] > 0

        partial_thinking = [
            c.args[0]
            for c in scope.emit.call_args_list
            if c.args[0].__class__.__name__ == "AgentThinking" and getattr(c.args[0], "is_partial", False)
        ]
        assert len(partial_thinking) == 1

    @pytest.mark.asyncio
    async def test_tool_dispatch_via_output_item_done(self, provider, binding, scope):
        """Tool calls should be dispatched from output_item.done events."""
        fc_item = _make_function_call("bash", "call_1", '{"command": "echo hi"}')
        mock_client = _make_raw_stream_client(
            [_make_output_item_done(fc_item), _make_completed("resp_1")],
            [_make_output_item_done(_make_message("Done")), _make_completed("resp_2")],
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Run it", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "bash"

    @pytest.mark.asyncio
    async def test_session_id_from_completed_event(self, provider, binding, scope):
        """Session ID should come from response.completed event."""
        mock_client = _make_raw_stream_client(
            [
                _make_output_item_done(_make_message("ok")),
                _make_completed("resp_stream_abc"),
            ]
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Hi", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.session_id == "resp_stream_abc"

    @pytest.mark.asyncio
    async def test_no_reasoning_events_still_works(self, provider, binding, scope):
        """Stream without reasoning events should work (reasoning is optional)."""
        mock_client = _make_raw_stream_client(
            [
                _make_text_delta("answer"),
                _make_output_item_done(_make_message("answer")),
                _make_completed("resp_1"),
            ]
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Ask", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert result.metadata["thinking_length"] == 0

    @pytest.mark.asyncio
    async def test_stream_true_passed_to_api(self, provider, binding, scope):
        """Verify that stream=True is passed to responses.create."""
        mock_client = _make_raw_stream_client(
            [
                _make_output_item_done(_make_message("ok")),
                _make_completed("resp_1"),
            ]
        )

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            await provider.execute_sdk("Hi", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test"))

        assert mock_client.responses.create.call_args.kwargs.get("stream") is True


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.fixture
    def provider(self):
        return OpenAIProvider(name="test", model="gpt-4o", max_turns=5)

    @pytest.fixture
    def binding(self):
        return ProviderBinding(
            context_id="test_ctx",
            capabilities=frozenset({"bash", "read", "write"}),
            trust_level="standard",
        )

    @pytest.fixture
    def scope(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_cold_restart_restores_instructions(self, provider, scope):
        """When previous_response_not_found fires on turn 2+, instructions must be restored."""
        import openai as openai_mod

        # Use a binding with context_description so instructions are non-None
        binding = ProviderBinding(
            context_id="test_ctx",
            context_description="You are a helpful assistant.",
            capabilities=frozenset({"bash", "read", "write"}),
            trust_level="standard",
        )

        turn1_stream = _raw_stream(
            [
                _make_output_item_done(_make_function_call("bash", "c1", '{"command": "ls"}')),
                _make_completed("resp_1"),
            ]
        )
        # After tool dispatch, turn 2 create raises BadRequestError
        bad_err = openai_mod.BadRequestError(
            message="not found",
            response=MagicMock(status_code=400, headers={}),
            body={"error": {"code": "previous_response_not_found", "message": "not found"}},
        )
        # Retry after cold restart succeeds
        retry_stream = _raw_stream(
            [
                _make_output_item_done(_make_message("restarted")),
                _make_completed("resp_new"),
            ]
        )

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(side_effect=[turn1_stream, bad_err, retry_stream])

        with patch("shepherd_providers.openai.provider._get_client", return_value=mock_client):
            result = await provider.execute_sdk(
                "Do stuff", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        assert result.success
        assert result.output_text == "restarted"

        # The retry call (3rd) should have instructions restored and no previous_response_id
        retry_kwargs = mock_client.responses.create.call_args_list[2].kwargs
        assert "previous_response_id" not in retry_kwargs
        assert "instructions" in retry_kwargs
        assert "You are a helpful assistant." in retry_kwargs["instructions"]

    @pytest.mark.asyncio
    async def test_api_error_includes_suggestions(self, provider, binding, scope):
        """SDKExecutionError should include suggestions from suggest_fixes."""
        import openai as openai_mod
        from shepherd_core.errors import SDKExecutionError

        err = openai_mod.APIError(
            message="rate limit exceeded",
            request=MagicMock(),
            body={"error": {"message": "rate limit exceeded"}},
        )
        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(side_effect=err)

        with (
            patch("shepherd_providers.openai.provider._get_client", return_value=mock_client),
            pytest.raises(SDKExecutionError) as exc_info,
        ):
            await provider.execute_sdk("Test", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test"))

        assert exc_info.value.original_error is err
        assert exc_info.value.sdk_options is not None

    @pytest.mark.asyncio
    async def test_error_includes_last_tool_context(self, provider, binding, scope):
        """SDKExecutionError should include last_tool_name when error follows a tool call."""
        import openai as openai_mod
        from shepherd_core.errors import SDKExecutionError

        # Turn 1: tool call
        turn1_stream = _raw_stream(
            [
                _make_output_item_done(_make_function_call("bash", "c1", '{"command": "ls"}')),
                _make_completed("resp_1"),
            ]
        )
        # Turn 2: API error
        err = openai_mod.APIError(
            message="internal error",
            request=MagicMock(),
            body={"error": {"message": "internal error"}},
        )

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(side_effect=[turn1_stream, err])

        with (
            patch("shepherd_providers.openai.provider._get_client", return_value=mock_client),
            pytest.raises(SDKExecutionError) as exc_info,
        ):
            await provider.execute_sdk("Run", binding, DefaultProviderRuntime.from_emitter(scope, task_name="test"))

        assert exc_info.value.last_tool_name == "bash"
        assert exc_info.value.last_tool_params == {"command": "ls"}
