"""Tests for Claude provider tool result handling and LLM metadata emission.

Verifies:
- ToolResultBlock in UserMessage produces ToolResult + ToolCallCompleted
- Duplicate ToolResultBlocks across message types are deduped
- LLMResponseReceived is emitted with correct metadata
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from shepherd_core.effects import LLMResponseReceived, PromptSent, ToolCallCompleted
from shepherd_core.provider import DefaultProviderRuntime


class TestClaudeToolResults:
    @pytest.fixture
    def provider(self):
        from shepherd_providers.claude.provider import ClaudeProvider

        return ClaudeProvider(name="test", model="claude-haiku-4-5-20251001")

    @pytest.fixture
    def mock_scope(self):
        scope = MagicMock()
        scope.emit = MagicMock()
        return scope

    @pytest.fixture
    def mock_sdk(self):
        """Create mock SDK classes for testing.

        Uses concrete class definitions so isinstance() checks work correctly.
        """

        class MockTextBlock:
            def __init__(self, text: str):
                self.text = text

        class MockToolUseBlock:
            def __init__(self, id: str, name: str, input: dict):
                self.id = id
                self.name = name
                self.input = input

        class MockToolResultBlock:
            def __init__(
                self,
                tool_use_id: str,
                content: str | list[dict] | None,
                is_error: bool | None = None,
            ):
                self.tool_use_id = tool_use_id
                self.content = content
                self.is_error = is_error

        class MockAssistantMessage:
            def __init__(self, content: list):
                self.content = content

        class MockUserMessage:
            def __init__(self, content: list | str):
                self.content = content

        class MockResultMessage:
            def __init__(
                self,
                session_id: str | None = None,
                result: str | None = None,
                structured_output: dict | None = None,
                duration_ms: float = 100.0,
                duration_api_ms: float = 80.0,
                num_turns: int = 1,
                is_error: bool = False,
                total_cost_usd: float | None = 0.001,
                usage: dict | None = None,
                model: str = "claude-haiku-4-5-20251001",
            ):
                self.session_id = session_id
                self.result = result
                self.structured_output = structured_output
                self.duration_ms = duration_ms
                self.duration_api_ms = duration_api_ms
                self.num_turns = num_turns
                self.is_error = is_error
                self.total_cost_usd = total_cost_usd
                self.usage = usage or {
                    "input_tokens": 50,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
                self.model = model

        class MockThinkingBlock:
            def __init__(self, thinking: str):
                self.thinking = thinking

        return {
            "AssistantMessage": MockAssistantMessage,
            "UserMessage": MockUserMessage,
            "ResultMessage": MockResultMessage,
            "TextBlock": MockTextBlock,
            "ThinkingBlock": MockThinkingBlock,
            "ToolUseBlock": MockToolUseBlock,
            "ToolResultBlock": MockToolResultBlock,
            "ClaudeAgentOptions": MagicMock,
        }

    @pytest.mark.asyncio
    async def test_tool_result_in_user_message_emits_completion(self, provider, mock_scope, mock_sdk):
        MockAssistantMessage = mock_sdk["AssistantMessage"]
        MockUserMessage = mock_sdk["UserMessage"]
        MockToolUseBlock = mock_sdk["ToolUseBlock"]
        MockToolResultBlock = mock_sdk["ToolResultBlock"]

        async def mock_query(*args, **kwargs):
            yield MockAssistantMessage([MockToolUseBlock(id="tool-123", name="Read", input={"file_path": "x"})])
            yield MockUserMessage([MockToolResultBlock(tool_use_id="tool-123", content="ok", is_error=False)])

        mock_sdk["query"] = mock_query

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            result = await provider.execute_sdk(
                prompt="Test",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
            )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "tool-123"
        assert result.tool_calls[0].name == "Read"

        assert len(result.tool_results) == 1
        assert result.tool_results[0].tool_call_id == "tool-123"
        assert result.tool_results[0].success is True
        assert result.tool_results[0].output == "ok"

        completed_effects = [
            call.args[0] for call in mock_scope.emit.call_args_list if isinstance(call.args[0], ToolCallCompleted)
        ]
        assert len(completed_effects) == 1
        effect = completed_effects[0]
        assert effect.tool_call_id == "tool-123"
        assert effect.tool_name == "Read"
        assert effect.success is True
        assert effect.output_preview == "ok"

    @pytest.mark.asyncio
    async def test_duplicate_tool_results_are_deduped(self, provider, mock_scope, mock_sdk):
        MockAssistantMessage = mock_sdk["AssistantMessage"]
        MockUserMessage = mock_sdk["UserMessage"]
        MockToolUseBlock = mock_sdk["ToolUseBlock"]
        MockToolResultBlock = mock_sdk["ToolResultBlock"]

        async def mock_query(*args, **kwargs):
            yield MockAssistantMessage([MockToolUseBlock(id="tool-123", name="Read", input={"file_path": "x"})])
            # Duplicate tool result in both message types (defensive behavior)
            yield MockAssistantMessage([MockToolResultBlock(tool_use_id="tool-123", content="ok", is_error=False)])
            yield MockUserMessage([MockToolResultBlock(tool_use_id="tool-123", content="ok", is_error=False)])

        mock_sdk["query"] = mock_query

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            result = await provider.execute_sdk(
                prompt="Test",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
            )

        assert len(result.tool_results) == 1

        completed_effects = [
            call.args[0] for call in mock_scope.emit.call_args_list if isinstance(call.args[0], ToolCallCompleted)
        ]
        assert len(completed_effects) == 1


class TestClaudeLLMMetadata:
    """Tests for LLMResponseReceived emission from Claude provider."""

    @pytest.fixture
    def provider(self):
        from shepherd_providers.claude.provider import ClaudeProvider

        return ClaudeProvider(name="test", model="claude-haiku-4-5-20251001")

    @pytest.fixture
    def mock_scope(self):
        scope = MagicMock()
        scope.emit = MagicMock()
        return scope

    @pytest.fixture
    def mock_sdk(self):
        """Create mock SDK classes with profiling metadata."""

        class MockTextBlock:
            def __init__(self, text: str):
                self.text = text

        class MockToolUseBlock:
            def __init__(self, id: str, name: str, input: dict):
                self.id = id
                self.name = name
                self.input = input

        class MockToolResultBlock:
            def __init__(self, tool_use_id: str, content: str | list[dict] | None, is_error: bool | None = None):
                self.tool_use_id = tool_use_id
                self.content = content
                self.is_error = is_error

        class MockAssistantMessage:
            def __init__(self, content: list, model: str = "claude-haiku-4-5-20251001"):
                self.content = content
                self.model = model

        class MockUserMessage:
            def __init__(self, content: list | str):
                self.content = content

        class MockResultMessage:
            def __init__(
                self,
                session_id: str | None = None,
                result: str | None = None,
                structured_output: dict | None = None,
                duration_ms: float = 100.0,
                duration_api_ms: float = 80.0,
                num_turns: int = 1,
                is_error: bool = False,
                total_cost_usd: float | None = 0.002,
                usage: dict | None = None,
                model: str = "claude-haiku-4-5-20251001",
            ):
                self.session_id = session_id
                self.result = result
                self.structured_output = structured_output
                self.duration_ms = duration_ms
                self.duration_api_ms = duration_api_ms
                self.num_turns = num_turns
                self.is_error = is_error
                self.total_cost_usd = total_cost_usd
                self.usage = usage or {
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 5,
                }
                self.model = model

        class MockThinkingBlock:
            def __init__(self, thinking: str):
                self.thinking = thinking

        return {
            "AssistantMessage": MockAssistantMessage,
            "UserMessage": MockUserMessage,
            "ResultMessage": MockResultMessage,
            "TextBlock": MockTextBlock,
            "ThinkingBlock": MockThinkingBlock,
            "ToolUseBlock": MockToolUseBlock,
            "ToolResultBlock": MockToolResultBlock,
            "ClaudeAgentOptions": MagicMock,
        }

    @pytest.mark.asyncio
    async def test_emits_llm_response_received(self, provider, mock_scope, mock_sdk):
        """Claude provider emits LLMResponseReceived with SDK metadata."""
        MockAssistantMessage = mock_sdk["AssistantMessage"]
        MockTextBlock = mock_sdk["TextBlock"]
        MockResultMessage = mock_sdk["ResultMessage"]

        async def mock_query(*args, **kwargs):
            yield MockAssistantMessage([MockTextBlock("Hello")])
            yield MockResultMessage(result="Hello")

        mock_sdk["query"] = mock_query

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            await provider.execute_sdk(
                prompt="Hi",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
            )

        llm_effects = [
            call.args[0] for call in mock_scope.emit.call_args_list if isinstance(call.args[0], LLMResponseReceived)
        ]
        assert len(llm_effects) == 1
        effect = llm_effects[0]
        assert effect.input_tokens == 200
        assert effect.output_tokens == 100
        assert effect.duration_ms == 100.0
        assert effect.duration_api_ms == 80.0
        assert effect.num_turns == 1
        assert effect.cost_usd == 0.002
        assert effect.model_id == "claude-haiku-4-5-20251001"
        assert effect.is_error is False
        assert effect.cache_creation_input_tokens == 10
        assert effect.cache_read_input_tokens == 5

    @pytest.mark.asyncio
    async def test_emits_prompt_sent_with_model_id(self, provider, mock_scope, mock_sdk):
        """Claude provider emits PromptSent with model_id."""
        MockAssistantMessage = mock_sdk["AssistantMessage"]
        MockTextBlock = mock_sdk["TextBlock"]
        MockResultMessage = mock_sdk["ResultMessage"]

        async def mock_query(*args, **kwargs):
            yield MockAssistantMessage([MockTextBlock("Hello")])
            yield MockResultMessage(result="Hello")

        mock_sdk["query"] = mock_query

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            await provider.execute_sdk(
                prompt="Hi",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
            )

        prompt_effects = [
            call.args[0] for call in mock_scope.emit.call_args_list if isinstance(call.args[0], PromptSent)
        ]
        assert len(prompt_effects) == 1
        assert prompt_effects[0].model_id == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_metadata_emitted_on_error_path(self, provider, mock_scope, mock_sdk):
        """LLMResponseReceived is emitted even when stream errors."""
        MockResultMessage = mock_sdk["ResultMessage"]

        async def mock_query(*args, **kwargs):
            yield MockResultMessage(result="", is_error=True, total_cost_usd=None)

        mock_sdk["query"] = mock_query

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            await provider.execute_sdk(
                prompt="Hi",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
            )

        llm_effects = [
            call.args[0] for call in mock_scope.emit.call_args_list if isinstance(call.args[0], LLMResponseReceived)
        ]
        assert len(llm_effects) == 1
        # Metadata should still be present (even if partial)
