"""Tests for LiteLLMProvider."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.effects import LLMResponseReceived, PromptSent, ToolCallCompleted
from shepherd_core.provider import DefaultProviderRuntime
from shepherd_providers.litellm.provider import LiteLLMProvider


@pytest.fixture
def provider():
    return LiteLLMProvider(name="test", model="claude-sonnet-4-6")


@pytest.fixture(autouse=True)
def fake_litellm_module(monkeypatch):
    """Install a minimal litellm module so unit tests stay offline."""

    async def acompletion(**kwargs):
        raise AssertionError(f"Test should patch litellm.acompletion: {kwargs}")

    fake_module = ModuleType("litellm")
    fake_module.acompletion = acompletion
    monkeypatch.setitem(sys.modules, "litellm", fake_module)


def _mock_response(content="Hello", tool_calls=None):
    """Build a mock litellm response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return resp


def _mock_tool_call(name="bash", arguments='{"command": "echo hello"}', call_id="call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


class TestLiteLLMProviderProperties:
    def test_provider_id(self, provider):
        assert provider.provider_id == "litellm-test"

    def test_capabilities(self, provider):
        caps = provider.capabilities
        assert caps.provider_type == "litellm"
        assert caps.supports_tools is True


class TestSingleTurn:
    @pytest.mark.asyncio
    async def test_no_tools(self, provider):
        """LLM returns text with no tool calls — loop exits after 1 turn."""
        mock_resp = _mock_response(content="The answer is 42")
        scope = MagicMock()

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            result = await provider.execute_sdk(
                prompt="What is the meaning of life?",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test"),
            )

        assert result.output_text == "The answer is 42"
        assert result.metadata["turns"] == 1


class TestMultiTurn:
    @pytest.mark.asyncio
    async def test_tool_use_loop(self, provider):
        """LLM calls a tool, gets result, then responds with text."""
        tc = _mock_tool_call()
        resp_with_tool = _mock_response(content=None, tool_calls=[tc])
        resp_final = _mock_response(content="Done!")

        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, side_effect=[resp_with_tool, resp_final]),
            patch.object(provider, "_dispatch_tool", return_value="hello"),
        ):
            result = await provider.execute_sdk(
                prompt="Run echo hello",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test"),
            )

        assert result.output_text == "Done!"
        assert result.metadata["turns"] == 2
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "bash"

    @pytest.mark.asyncio
    async def test_max_turns_terminates(self, provider):
        """Loop should stop after max_turns."""
        provider.max_turns = 2
        tc = _mock_tool_call()
        resp_with_tool = _mock_response(content="still working", tool_calls=[tc])

        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, return_value=resp_with_tool),
            patch.object(provider, "_dispatch_tool", return_value="ok"),
        ):
            result = await provider.execute_sdk(
                prompt="Do something",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test"),
            )

        assert result.metadata["turns"] == 2


class TestToolErrorRecovery:
    @pytest.mark.asyncio
    async def test_tool_error_fed_back(self, provider):
        """Tool errors should be sent back as tool results, not raised."""
        tc = _mock_tool_call()
        resp_with_tool = _mock_response(content=None, tool_calls=[tc])
        resp_final = _mock_response(content="Command failed, trying another way")

        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, side_effect=[resp_with_tool, resp_final]),
            patch.object(provider, "_dispatch_tool", side_effect=RuntimeError("command not found")),
        ):
            result = await provider.execute_sdk(
                prompt="Run something",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test"),
            )

        assert result.output_text == "Command failed, trying another way"
        assert result.tool_results[0].success is False


class TestEffectEmission:
    @pytest.mark.asyncio
    async def test_effects_emitted_on_completion(self, provider):
        """Provider should emit AgentMessage effect on completion."""
        mock_resp = _mock_response(content="Hello")
        scope = MagicMock()

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            await provider.execute_sdk(
                prompt="Hi", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        emit_calls = scope.emit.call_args_list
        # Should have PromptSent and AgentMessage at minimum
        assert len(emit_calls) >= 2

    @pytest.mark.asyncio
    async def test_tool_call_effects_emitted(self, provider):
        """Provider should emit ToolCallStarted and ToolCallCompleted."""
        tc = _mock_tool_call()
        resp_with_tool = _mock_response(content=None, tool_calls=[tc])
        resp_final = _mock_response(content="Done")
        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, side_effect=[resp_with_tool, resp_final]),
            patch.object(provider, "_dispatch_tool", return_value="ok"),
        ):
            await provider.execute_sdk(
                prompt="Do it", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        emit_calls = scope.emit.call_args_list
        effect_types = [type(call.args[0]).__name__ for call in emit_calls]
        assert "ToolCallStarted" in effect_types
        assert "ToolCallCompleted" in effect_types


class TestToolSchemas:
    def test_default_tools(self, provider):
        """No binding should give bash tool."""
        tools = provider._build_tool_schemas(None)
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "bash"

    def test_capability_tools(self, provider):
        """Binding with capabilities should give matching tools."""
        binding = MagicMock()
        binding.capabilities = frozenset({"read", "write", "bash"})
        binding.custom_tools = []

        tools = provider._build_tool_schemas(binding)
        names = {t["function"]["name"] for t in tools}
        assert names == {"bash", "read_file", "write_file"}


class TestToolDispatch:
    def test_bash_local(self, provider):
        """Bash tool should work locally."""
        result = provider._dispatch_tool("bash", {"command": "echo hello"})
        assert "hello" in result
        assert "exit_code: 0" in result

    def test_read_file(self, provider, tmp_path):
        """Read tool should read files."""
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = provider._dispatch_tool("read_file", {"path": str(f)})
        assert result == "content"

    def test_write_file(self, provider, tmp_path):
        """Write tool should write files."""
        f = tmp_path / "out.txt"
        result = provider._dispatch_tool("write_file", {"path": str(f), "content": "hello"})
        assert "Written" in result
        assert f.read_text() == "hello"

    def test_unknown_tool(self, provider):
        result = provider._dispatch_tool("nonexistent", {})
        assert "Unknown tool" in result


class TestLLMResponseMetadata:
    """Tests for LLMResponseReceived emission from LiteLLM provider."""

    @pytest.mark.asyncio
    async def test_emits_llm_response_received(self, provider):
        """Provider emits LLMResponseReceived with token counts."""
        mock_resp = _mock_response(content="Hello")
        scope = MagicMock()

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            await provider.execute_sdk(
                prompt="Hi", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        llm_effects = [
            call.args[0] for call in scope.emit.call_args_list if isinstance(call.args[0], LLMResponseReceived)
        ]
        assert len(llm_effects) == 1
        effect = llm_effects[0]
        assert effect.input_tokens == 10
        assert effect.output_tokens == 5
        assert effect.total_tokens == 15
        assert effect.num_turns == 1
        assert effect.duration_ms > 0

    @pytest.mark.asyncio
    async def test_aggregates_tokens_across_turns(self, provider):
        """Multi-turn execution aggregates tokens from all turns."""
        tc = _mock_tool_call()
        resp1 = _mock_response(content=None, tool_calls=[tc])
        resp2 = _mock_response(content="Done!")
        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, side_effect=[resp1, resp2]),
            patch.object(provider, "_dispatch_tool", return_value="ok"),
        ):
            await provider.execute_sdk(
                prompt="Do it", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        llm_effects = [
            call.args[0] for call in scope.emit.call_args_list if isinstance(call.args[0], LLMResponseReceived)
        ]
        assert len(llm_effects) == 1
        effect = llm_effects[0]
        # 2 turns x 10 prompt_tokens + 2 turns x 5 completion_tokens
        assert effect.input_tokens == 20
        assert effect.output_tokens == 10
        assert effect.num_turns == 2

    @pytest.mark.asyncio
    async def test_cost_usd_from_completion_cost(self, provider):
        """cost_usd is populated via litellm.completion_cost."""
        mock_resp = _mock_response(content="Hello")
        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp),
            patch("litellm.completion_cost", return_value=0.001, create=True),
        ):
            await provider.execute_sdk(
                prompt="Hi", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        llm_effects = [
            call.args[0] for call in scope.emit.call_args_list if isinstance(call.args[0], LLMResponseReceived)
        ]
        effect = llm_effects[0]
        assert effect.cost_usd is not None
        assert abs(effect.cost_usd - 0.001) < 1e-9

    @pytest.mark.asyncio
    async def test_cost_usd_none_on_error(self, provider):
        """cost_usd is None when litellm.completion_cost raises."""
        mock_resp = _mock_response(content="Hello")
        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp),
            patch("litellm.completion_cost", side_effect=Exception("unknown model"), create=True),
        ):
            await provider.execute_sdk(
                prompt="Hi", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        llm_effects = [
            call.args[0] for call in scope.emit.call_args_list if isinstance(call.args[0], LLMResponseReceived)
        ]
        effect = llm_effects[0]
        assert effect.cost_usd is None

    @pytest.mark.asyncio
    async def test_tool_call_completed_has_duration(self, provider):
        """ToolCallCompleted carries duration_ms."""
        tc = _mock_tool_call()
        resp_with_tool = _mock_response(content=None, tool_calls=[tc])
        resp_final = _mock_response(content="Done")
        scope = MagicMock()

        with (
            patch("litellm.acompletion", new_callable=AsyncMock, side_effect=[resp_with_tool, resp_final]),
            patch.object(provider, "_dispatch_tool", return_value="ok"),
        ):
            await provider.execute_sdk(
                prompt="Do it", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        tool_completed = [
            call.args[0] for call in scope.emit.call_args_list if isinstance(call.args[0], ToolCallCompleted)
        ]
        assert len(tool_completed) == 1
        assert tool_completed[0].duration_ms > 0

    @pytest.mark.asyncio
    async def test_model_id_from_response(self, provider):
        """model_id reflects the actual served model."""
        mock_resp = _mock_response(content="Hello")
        mock_resp.model = "claude-sonnet-4-6-20250514"
        scope = MagicMock()

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            await provider.execute_sdk(
                prompt="Hi", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        llm_effects = [
            call.args[0] for call in scope.emit.call_args_list if isinstance(call.args[0], LLMResponseReceived)
        ]
        assert llm_effects[0].model_id == "claude-sonnet-4-6-20250514"

    @pytest.mark.asyncio
    async def test_prompt_sent_has_model_id(self, provider):
        """PromptSent carries model_id for the requested model."""
        mock_resp = _mock_response(content="Hello")
        scope = MagicMock()

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            await provider.execute_sdk(
                prompt="Hi", binding=None, runtime=DefaultProviderRuntime.from_emitter(scope, task_name="test")
            )

        prompt_effects = [call.args[0] for call in scope.emit.call_args_list if isinstance(call.args[0], PromptSent)]
        assert len(prompt_effects) >= 1
        assert prompt_effects[0].model_id == "claude-sonnet-4-6"
