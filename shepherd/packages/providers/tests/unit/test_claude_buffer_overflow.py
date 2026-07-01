"""Tests for Claude provider buffer overflow handling.

Verifies:
- Buffer overflow detection and graceful failure
- Partial results preservation
- ExecutionFailed effect emission
- Recovery prompt generation
- execute_sdk_with_recovery() retry logic
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.effects import ExecutionFailed, RecoveryAttempted
from shepherd_core.provider import DefaultProviderRuntime
from shepherd_core.types import ExecutionResult, ProviderBinding, ToolCall

# =============================================================================
# Tests for execute_sdk() error detection (core bug fix)
# =============================================================================


class TestExecuteSdkErrorDetection:
    """Tests for buffer overflow detection in execute_sdk().

    These tests verify the core error handling logic by mocking the SDK's
    query() function to simulate buffer overflow errors mid-stream.
    """

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

        Uses proper class definitions so isinstance() checks work correctly.
        """

        # Define mock classes that isinstance() can check against
        class MockTextBlock:
            def __init__(self, text: str):
                self.text = text

        class MockThinkingBlock:
            def __init__(self, thinking: str):
                self.thinking = thinking

        class MockToolUseBlock:
            def __init__(self, id: str, name: str, input: dict):
                self.id = id
                self.name = name
                self.input = input

        class MockToolResultBlock:
            def __init__(self, tool_use_id: str, content: str, is_error: bool = False):
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
            ):
                self.session_id = session_id
                self.result = result
                self.structured_output = structured_output

        # Create instances for test scenarios
        text_block = MockTextBlock("Partial response before error")
        tool_use_block = MockToolUseBlock(id="tool-123", name="Bash", input={"command": "find / -type f"})
        assistant_msg = MockAssistantMessage([text_block, tool_use_block])

        return {
            "AssistantMessage": MockAssistantMessage,
            "ResultMessage": MockResultMessage,
            "UserMessage": MockUserMessage,
            "TextBlock": MockTextBlock,
            "ThinkingBlock": MockThinkingBlock,
            "ToolUseBlock": MockToolUseBlock,
            "ToolResultBlock": MockToolResultBlock,
            "ClaudeAgentOptions": MagicMock,
            # Pre-built instances for convenience
            "assistant_msg": assistant_msg,
            "tool_use_block": tool_use_block,
            "text_block": text_block,
        }

    @pytest.mark.asyncio
    async def test_buffer_overflow_detected_and_returns_graceful_failure(self, provider, mock_scope, mock_sdk):
        """Buffer overflow error should be caught and return success=False."""

        async def mock_query_raises_buffer_error(*args, **kwargs):
            """Simulate SDK raising buffer overflow after yielding some messages."""
            yield mock_sdk["assistant_msg"]
            raise Exception("Maximum buffer size exceeded: 1048576 bytes")

        mock_sdk["query"] = mock_query_raises_buffer_error

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            result = await provider.execute_sdk(
                prompt="Find all files",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
            )

        assert result.success is False
        assert result.metadata.get("error_type") == "buffer_overflow"
        assert result.metadata.get("partial") is True
        assert "1MB" in result.output_text or "buffer" in result.output_text.lower()

    @pytest.mark.asyncio
    async def test_buffer_overflow_preserves_partial_tool_calls(self, provider, mock_scope, mock_sdk):
        """Tool calls collected before failure should be preserved in result."""

        async def mock_query_raises_after_tool(*args, **kwargs):
            yield mock_sdk["assistant_msg"]
            raise Exception("buffer size limit exceeded")

        mock_sdk["query"] = mock_query_raises_after_tool

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            result = await provider.execute_sdk(
                prompt="Run command",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
            )

        # Tool calls should be preserved
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "Bash"
        assert result.tool_calls[0].id == "tool-123"

        # Metadata should include last tool info
        assert result.metadata.get("last_tool_name") == "Bash"
        assert result.metadata.get("last_tool_params") == {"command": "find / -type f"}

    @pytest.mark.asyncio
    async def test_buffer_overflow_emits_execution_failed_effect(self, provider, mock_scope, mock_sdk):
        """ExecutionFailed effect should be emitted on buffer overflow."""

        async def mock_query_raises(*args, **kwargs):
            yield mock_sdk["assistant_msg"]
            raise Exception("Maximum buffer size exceeded")

        mock_sdk["query"] = mock_query_raises

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            await provider.execute_sdk(
                prompt="Test",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
            )

        # Find ExecutionFailed effect in emitted effects
        execution_failed_effects = [
            call.args[0] for call in mock_scope.emit.call_args_list if isinstance(call.args[0], ExecutionFailed)
        ]

        assert len(execution_failed_effects) == 1
        effect = execution_failed_effects[0]
        assert effect.error_type == "buffer_overflow"
        assert effect.tool_calls_completed == 1
        assert effect.last_tool_name == "Bash"

    @pytest.mark.asyncio
    async def test_non_buffer_errors_are_reraised(self, provider, mock_scope, mock_sdk):
        """Non-buffer-overflow errors should be wrapped in SDKExecutionError."""
        from shepherd_core.errors import SDKExecutionError

        async def mock_query_raises_other_error(*args, **kwargs):
            yield mock_sdk["assistant_msg"]
            raise ConnectionError("Network connection lost")

        mock_sdk["query"] = mock_query_raises_other_error

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            with pytest.raises(SDKExecutionError) as exc_info:
                await provider.execute_sdk(
                    prompt="Test",
                    binding=None,
                    runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
                )
            # Verify the original error is preserved in the chain
            assert "Network connection lost" in str(exc_info.value)
            assert exc_info.value.__cause__ is not None
            assert isinstance(exc_info.value.__cause__, ConnectionError)

    @pytest.mark.asyncio
    async def test_recoverable_flag_depends_on_session_id(self, provider, mock_scope, mock_sdk):
        """Recoverable should be True only when session_id is available."""
        # Create a result message with session_id using mock class
        MockResultMessage = mock_sdk["ResultMessage"]
        result_msg = MockResultMessage(session_id="session-abc-123")

        async def mock_query_with_session_then_error(*args, **kwargs):
            # First yield a result message (provides session_id)
            yield result_msg
            # Then yield assistant message
            yield mock_sdk["assistant_msg"]
            # Then error
            raise Exception("buffer size exceeded")

        mock_sdk["query"] = mock_query_with_session_then_error

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            result = await provider.execute_sdk(
                prompt="Test",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
            )

        assert result.session_id == "session-abc-123"
        assert result.metadata.get("recoverable") is True

    @pytest.mark.asyncio
    async def test_recoverable_false_without_session_id(self, provider, mock_scope, mock_sdk):
        """Recoverable should be False when no session_id is available."""

        async def mock_query_error_before_result(*args, **kwargs):
            # Error before any ResultMessage
            yield mock_sdk["assistant_msg"]
            raise Exception("buffer size exceeded")

        mock_sdk["query"] = mock_query_error_before_result

        with patch("shepherd_providers.claude.provider._get_sdk", return_value=mock_sdk):
            result = await provider.execute_sdk(
                prompt="Test",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test_task"),
            )

        assert result.session_id is None
        assert result.metadata.get("recoverable") is False


class TestBufferOverflowDetection:
    """Tests for buffer overflow error detection in execute_sdk()."""

    def test_recovery_prompt_includes_failed_tool(self):
        """Recovery prompt should mention the failed tool and parameters."""
        from shepherd_providers.claude.provider import ClaudeProvider

        provider = ClaudeProvider(name="test", model="claude-haiku-4-5-20251001")

        # Simulate a failed result with tool info
        result = ExecutionResult(
            success=False,
            output_text="Buffer overflow error",
            tool_calls=(),
            tool_results=(),
            session_id="test-session-123",
            structured_output={},
            metadata={
                "error_type": "buffer_overflow",
                "last_tool_name": "Bash",
                "last_tool_params": {"command": "find / -type f"},
            },
        )

        prompt = provider._build_recovery_prompt(result)

        assert "1MB buffer limit" in prompt
        assert "Bash" in prompt
        assert "find" in prompt
        assert "head/tail" in prompt

    def test_recovery_prompt_without_tool_info(self):
        """Recovery prompt should work without tool information."""
        from shepherd_providers.claude.provider import ClaudeProvider

        provider = ClaudeProvider(name="test", model="claude-haiku-4-5-20251001")

        result = ExecutionResult(
            success=False,
            output_text="Buffer overflow error",
            tool_calls=(),
            tool_results=(),
            session_id="test-session-123",
            structured_output={},
            metadata={
                "error_type": "buffer_overflow",
                "last_tool_name": None,
                "last_tool_params": None,
            },
        )

        prompt = provider._build_recovery_prompt(result)

        assert "1MB buffer limit" in prompt
        assert "head/tail" in prompt

    def test_create_recovery_binding_from_none(self):
        """Recovery binding should work when original binding is None."""
        from shepherd_providers.claude.provider import ClaudeProvider

        provider = ClaudeProvider(name="test", model="claude-haiku-4-5-20251001")

        binding = provider._create_recovery_binding(None, "session-123")

        assert binding.context_id == "recovery"
        assert binding.session_id == "session-123"
        assert binding.session_isolation == "forked"

    def test_create_recovery_binding_preserves_original_settings(self):
        """Recovery binding should preserve original binding settings."""
        from shepherd_providers.claude.provider import ClaudeProvider

        provider = ClaudeProvider(name="test", model="claude-haiku-4-5-20251001")

        original = ProviderBinding(
            context_id="workspace",
            trust_level="elevated",
            cwd="/some/path",
            capabilities=frozenset({"Read", "Write"}),
        )

        binding = provider._create_recovery_binding(original, "session-123")

        assert binding.context_id == "workspace"
        assert binding.trust_level == "elevated"
        assert binding.cwd == "/some/path"
        assert binding.capabilities == frozenset({"Read", "Write"})
        assert binding.session_id == "session-123"
        assert binding.session_isolation == "forked"


class TestExecutionFailedEffect:
    """Tests for ExecutionFailed effect structure."""

    def test_execution_failed_effect_fields(self):
        """ExecutionFailed effect should have all required fields."""
        effect = ExecutionFailed(
            task_name="test_task",
            provider_id="provider:claude:test",
            error_type="buffer_overflow",
            error_message="Maximum buffer size exceeded",
            tool_calls_completed=5,
            last_tool_name="Bash",
            recoverable=True,
        )

        assert effect.error_type == "buffer_overflow"
        assert effect.error_message == "Maximum buffer size exceeded"
        assert effect.tool_calls_completed == 5
        assert effect.last_tool_name == "Bash"
        assert effect.recoverable is True

    def test_execution_failed_effect_defaults(self):
        """ExecutionFailed effect should have sensible defaults."""
        effect = ExecutionFailed()

        assert effect.error_type == ""
        assert effect.error_message == ""
        assert effect.tool_calls_completed == 0
        assert effect.last_tool_name is None
        assert effect.recoverable is False


class TestRecoveryAttemptedEffect:
    """Tests for RecoveryAttempted effect structure."""

    def test_recovery_attempted_effect_fields(self):
        """RecoveryAttempted effect should have all required fields."""
        effect = RecoveryAttempted(
            task_name="test_task",
            provider_id="provider:claude:test",
            original_session_id="session-123",
            error_type="buffer_overflow",
            last_tool_name="Bash",
            recovery_strategy="fork_and_retry",
        )

        assert effect.original_session_id == "session-123"
        assert effect.error_type == "buffer_overflow"
        assert effect.last_tool_name == "Bash"
        assert effect.recovery_strategy == "fork_and_retry"

    def test_recovery_attempted_effect_defaults(self):
        """RecoveryAttempted effect should have sensible defaults."""
        effect = RecoveryAttempted()

        assert effect.original_session_id == ""
        assert effect.error_type == ""
        assert effect.last_tool_name is None
        assert effect.recovery_strategy == "fork_and_retry"


class TestBufferOverflowRecovery:
    """Tests for execute_sdk_with_recovery() retry logic."""

    @pytest.fixture
    def provider(self):
        from shepherd_providers.claude.provider import ClaudeProvider

        return ClaudeProvider(name="test", model="claude-haiku-4-5-20251001")

    @pytest.fixture
    def mock_scope(self):
        scope = MagicMock()
        scope.emit = MagicMock()
        return scope

    @pytest.mark.asyncio
    async def test_recovery_not_attempted_on_success(self, provider, mock_scope):
        """Recovery should not be attempted when execution succeeds."""
        success_result = ExecutionResult(
            success=True,
            output_text="Task completed",
            tool_calls=(),
            tool_results=(),
            session_id="session-123",
            structured_output={},
            metadata={"model": "claude-haiku-4-5-20251001"},
        )

        with patch.object(provider, "execute_sdk", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = success_result

            result = await provider.execute_sdk_with_recovery(
                prompt="Do something",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
            )

            # Should only call execute_sdk once (no recovery)
            assert mock_execute.call_count == 1
            assert result.success is True

    @pytest.mark.asyncio
    async def test_recovery_not_attempted_without_session_id(self, provider, mock_scope):
        """Recovery should not be attempted without session_id."""
        failed_result = ExecutionResult(
            success=False,
            output_text="Buffer overflow",
            tool_calls=(),
            tool_results=(),
            session_id=None,  # No session ID
            structured_output={},
            metadata={
                "error_type": "buffer_overflow",
                "recoverable": False,
            },
        )

        with patch.object(provider, "execute_sdk", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = failed_result

            result = await provider.execute_sdk_with_recovery(
                prompt="Do something",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
            )

            # Should only call execute_sdk once (no recovery without session_id)
            assert mock_execute.call_count == 1
            assert result.success is False

    @pytest.mark.asyncio
    async def test_recovery_attempted_on_buffer_overflow(self, provider, mock_scope):
        """Recovery should be attempted on buffer overflow with session_id."""
        failed_result = ExecutionResult(
            success=False,
            output_text="Buffer overflow",
            tool_calls=(ToolCall(id="tc1", name="Bash", params={"command": "find /"}),),
            tool_results=(),
            session_id="session-123",
            structured_output={},
            metadata={
                "error_type": "buffer_overflow",
                "recoverable": True,
                "last_tool_name": "Bash",
                "last_tool_params": {"command": "find /"},
            },
        )

        success_result = ExecutionResult(
            success=True,
            output_text="Recovered successfully",
            tool_calls=(),
            tool_results=(),
            session_id="session-456",
            structured_output={},
            metadata={"model": "claude-haiku-4-5-20251001"},
        )

        with patch.object(provider, "execute_sdk", new_callable=AsyncMock) as mock_execute:
            # First call fails, second succeeds
            mock_execute.side_effect = [failed_result, success_result]

            result = await provider.execute_sdk_with_recovery(
                prompt="Do something",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
            )

            # Should call execute_sdk twice (initial + recovery)
            assert mock_execute.call_count == 2
            assert result.success is True

            # Verify RecoveryAttempted effect was emitted
            recovery_effects = [
                call.args[0] for call in mock_scope.emit.call_args_list if isinstance(call.args[0], RecoveryAttempted)
            ]
            assert len(recovery_effects) == 1
            assert recovery_effects[0].original_session_id == "session-123"

    @pytest.mark.asyncio
    async def test_recovery_respects_max_attempts(self, provider, mock_scope):
        """Recovery should stop after max_recovery_attempts."""
        failed_result = ExecutionResult(
            success=False,
            output_text="Buffer overflow",
            tool_calls=(),
            tool_results=(),
            session_id="session-123",
            structured_output={},
            metadata={
                "error_type": "buffer_overflow",
                "recoverable": True,
            },
        )

        with patch.object(provider, "execute_sdk", new_callable=AsyncMock) as mock_execute:
            # All calls fail
            mock_execute.return_value = failed_result

            result = await provider.execute_sdk_with_recovery(
                prompt="Do something",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
                max_recovery_attempts=2,
            )

            # Should call execute_sdk 3 times (initial + 2 recovery attempts)
            assert mock_execute.call_count == 3
            assert result.success is False

    @pytest.mark.asyncio
    async def test_recovery_not_attempted_for_other_errors(self, provider, mock_scope):
        """Recovery should not be attempted for non-buffer-overflow errors."""
        failed_result = ExecutionResult(
            success=False,
            output_text="API error",
            tool_calls=(),
            tool_results=(),
            session_id="session-123",
            structured_output={},
            metadata={
                "error_type": "api_error",  # Not buffer_overflow
                "recoverable": False,
            },
        )

        with patch.object(provider, "execute_sdk", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = failed_result

            result = await provider.execute_sdk_with_recovery(
                prompt="Do something",
                binding=None,
                runtime=DefaultProviderRuntime.from_emitter(mock_scope, task_name="test"),
            )

            # Should only call execute_sdk once (no recovery for other errors)
            assert mock_execute.call_count == 1
            assert result.success is False
