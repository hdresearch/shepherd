"""Unit tests for the OpenCode provider.

Tests cover: initialization, capabilities, binding translation, serialization,
tool map computation, effect emission, validation, and session fork fallback.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.effects import (
    AgentMessage,
    AgentThinking,
    ExecutionFailed,
    LLMResponseReceived,
    PromptSent,
    ToolCallBatch,
)
from shepherd_core.errors import BindingValidationError
from shepherd_core.types import ProviderBinding
from shepherd_providers.opencode.provider import OpenCodeProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> OpenCodeProvider:
    return OpenCodeProvider(name="test", model="anthropic/claude-sonnet-4-20250514", streaming=False)


@pytest.fixture
def runtime() -> MagicMock:
    rt = MagicMock()
    rt.task_name = "test"
    rt.effects = MagicMock()
    rt.effects.emit = MagicMock()
    return rt


def _emitted_effects(runtime: MagicMock) -> list[Any]:
    """Extract all effects from runtime.effects.emit calls."""
    return [call.args[0] for call in runtime.effects.emit.call_args_list]


def _emitted_of_type(runtime: MagicMock, effect_type: type) -> list[Any]:
    return [e for e in _emitted_effects(runtime) if isinstance(e, effect_type)]


# ---------------------------------------------------------------------------
# TestProviderInit
# ---------------------------------------------------------------------------


class TestProviderInit:
    def test_defaults(self) -> None:
        p = OpenCodeProvider()
        assert p.name == "opencode"
        assert p.model == "anthropic/claude-sonnet-4-20250514"
        assert p.max_turns == 30
        assert p.server_port is None
        assert p.container_env == ()

    def test_custom_fields(self) -> None:
        p = OpenCodeProvider(
            name="my-oc",
            model="google/gemini-2.5-pro",
            max_turns=10,
            server_port=5000,
            container_env=("GOOGLE_API_KEY",),
        )
        assert p.name == "my-oc"
        assert p.model == "google/gemini-2.5-pro"
        assert p.server_port == 5000
        assert p.container_env == ("GOOGLE_API_KEY",)

    def test_provider_id_format(self, provider: OpenCodeProvider) -> None:
        pid = provider.provider_id
        assert pid.startswith("provider:opencode:")
        assert "anthropic/claude-sonnet-4-20250514" in pid
        assert "test" in pid

    def test_provider_id_unique(self) -> None:
        p1 = OpenCodeProvider(name="a")
        p2 = OpenCodeProvider(name="a")
        assert p1.provider_id != p2.provider_id


# ---------------------------------------------------------------------------
# TestCapabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_provider_type(self, provider: OpenCodeProvider) -> None:
        caps = provider.capabilities
        assert caps.provider_type == "opencode"

    def test_streaming_and_tools(self, provider: OpenCodeProvider) -> None:
        caps = provider.capabilities
        assert caps.supports_streaming is True
        assert caps.supports_tools is True
        assert caps.supports_structured_output is True

    def test_session_support(self, provider: OpenCodeProvider) -> None:
        caps = provider.capabilities
        assert caps.supports_session is True
        assert caps.supports_fork_session is True

    def test_available_tools(self, provider: OpenCodeProvider) -> None:
        tools = provider.capabilities.available_tools
        assert tools is not None
        assert "bash" in tools
        assert "write" in tools
        assert "read" in tools
        assert "webfetch" in tools


# ---------------------------------------------------------------------------
# TestBindingTranslation
# ---------------------------------------------------------------------------


class TestBindingTranslation:
    def test_no_binding(self, provider: OpenCodeProvider) -> None:
        params = provider._translate_binding(None)
        assert params["provider_id"] == "anthropic"
        assert params["model_id"] == "claude-sonnet-4-20250514"

    def test_system_prompt_from_binding(self, provider: OpenCodeProvider) -> None:
        binding = ProviderBinding(
            context_description="You are a helpful assistant.",
            system_prompt_additions=("Always be concise.",),
        )
        params = provider._translate_binding(binding)
        assert "You are a helpful assistant." in params["system"]
        assert "Always be concise." in params["system"]

    def test_model_split(self) -> None:
        p = OpenCodeProvider(model="google/gemini-2.5-pro")
        params = p._translate_binding(None)
        assert params["provider_id"] == "google"
        assert params["model_id"] == "gemini-2.5-pro"

    def test_model_no_slash(self) -> None:
        p = OpenCodeProvider(model="local-model")
        params = p._translate_binding(None)
        assert params["provider_id"] == ""
        assert params["model_id"] == "local-model"


# ---------------------------------------------------------------------------
# TestToolMap
# ---------------------------------------------------------------------------


class TestToolMap:
    def test_all_enabled_without_binding(self, provider: OpenCodeProvider) -> None:
        tool_map = provider._compute_tool_map(None)
        assert all(tool_map.values())

    def test_blocked_tools(self, provider: OpenCodeProvider) -> None:
        binding = ProviderBinding(
            capabilities=frozenset({"read", "write", "bash", "web", "task"}),
            blocked_tools=frozenset({"bash", "write"}),
        )
        tool_map = provider._compute_tool_map(binding)
        assert tool_map["bash"] is False
        assert tool_map["write"] is False
        assert tool_map["read"] is True

    def test_capability_gating(self, provider: OpenCodeProvider) -> None:
        binding = ProviderBinding(
            capabilities=frozenset({"read"}),  # Only read capability
        )
        tool_map = provider._compute_tool_map(binding)
        assert tool_map["read"] is True
        assert tool_map["grep"] is True
        assert tool_map["write"] is False
        assert tool_map["bash"] is False
        assert tool_map["webfetch"] is False

    def test_empty_capabilities(self, provider: OpenCodeProvider) -> None:
        binding = ProviderBinding(capabilities=frozenset())
        tool_map = provider._compute_tool_map(binding)
        # Read tools are ungated (no capability_for_tool entry)
        assert tool_map["read"] is True
        # All capability-gated tools are disabled
        assert tool_map["write"] is False
        assert tool_map["bash"] is False


# ---------------------------------------------------------------------------
# TestSerialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_round_trip(self) -> None:
        p = OpenCodeProvider(
            name="test",
            model="google/gemini-2.5-pro",
            max_turns=15,
            server_port=5000,
            container_env=("GOOGLE_API_KEY", "OPENCODE_SERVER_PASSWORD"),
        )
        config = p.to_config()
        p2 = OpenCodeProvider.from_config(config)

        assert p2.name == p.name
        assert p2.model == p.model
        assert p2.max_turns == p.max_turns
        assert p2.server_port == p.server_port
        assert p2.container_env == p.container_env

    def test_default_omission(self) -> None:
        p = OpenCodeProvider(name="test")
        config = p.to_config()
        assert "max_turns" not in config
        assert "server_port" not in config
        assert "container_env" not in config

    def test_provider_type(self) -> None:
        p = OpenCodeProvider()
        config = p.to_config()
        assert config["provider_type"] == "opencode"

    def test_from_config_defaults(self) -> None:
        p = OpenCodeProvider.from_config({"provider_type": "opencode"})
        assert p.name == "opencode"
        assert p.model == "anthropic/claude-sonnet-4-20250514"
        assert p.max_turns == 30
        assert p.container_env == ()

    def test_container_env_list_to_tuple(self) -> None:
        """from_config receives JSON lists, should convert to tuple."""
        p = OpenCodeProvider.from_config(
            {
                "provider_type": "opencode",
                "container_env": ["A", "B"],
            }
        )
        assert p.container_env == ("A", "B")

    def test_to_config_purity(self, provider: OpenCodeProvider) -> None:
        """to_config() must have no side effects."""
        config1 = provider.to_config()
        config2 = provider.to_config()
        assert config1 == config2


# ---------------------------------------------------------------------------
# TestValidation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_rejects_validate_tool_with_bash(self, provider: OpenCodeProvider) -> None:
        """validate_tool + bash capability -> reject (uncoverable)."""
        binding = ProviderBinding(
            context_id="test",
            capabilities=frozenset({"read", "bash"}),
            validate_tool=lambda t: MagicMock(allowed=True, tool=t),
        )
        with pytest.raises(BindingValidationError, match="validate_tool"):
            provider.validate_binding(binding)

    def test_accepts_validate_tool_with_write(self, provider: OpenCodeProvider) -> None:
        """validate_tool + write capability -> accept (cwd scoping covers it)."""
        binding = ProviderBinding(
            context_id="write-workspace",
            capabilities=frozenset({"read", "write"}),
            validate_tool=lambda t: MagicMock(allowed=True, tool=t),
        )
        provider.validate_binding(binding)  # Should not raise

    def test_accepts_validate_tool_readonly(self, provider: OpenCodeProvider) -> None:
        """validate_tool + read-only -> accept."""
        binding = ProviderBinding(
            context_id="readonly-workspace",
            capabilities=frozenset({"read"}),
            validate_tool=lambda t: MagicMock(allowed=True, tool=t),
        )
        provider.validate_binding(binding)  # Should not raise

    def test_accepts_no_validate_tool(self, provider: OpenCodeProvider) -> None:
        binding = ProviderBinding(context_id="test", trust_level="standard")
        provider.validate_binding(binding)  # Should not raise

    def test_rejects_invalid_trust_level(self, provider: OpenCodeProvider) -> None:
        # Pydantic validates the Literal before our code runs, so we
        # construct a binding with object.__setattr__ to bypass validation
        binding = ProviderBinding(context_id="test", trust_level="standard")
        object.__setattr__(binding, "trust_level", "invalid")
        with pytest.raises(BindingValidationError, match="trust_level"):
            provider.validate_binding(binding)

    def test_accepts_all_valid_trust_levels(self, provider: OpenCodeProvider) -> None:
        for level in ("sandbox", "restricted", "standard", "elevated"):
            binding = ProviderBinding(context_id="test", trust_level=level)  # type: ignore[arg-type]
            provider.validate_binding(binding)


# ---------------------------------------------------------------------------
# TestExecuteSdk
# ---------------------------------------------------------------------------


class TestExecuteSdk:
    """Tests for execute_sdk with mocked server and client."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        # create_session returns an object with .id
        session = MagicMock()
        session.id = "test-session-123"
        client.create_session.return_value = session
        # send_message returns AssistantMessage with parts
        chat_result = MagicMock()
        text_part = MagicMock()
        text_part.type = "text"
        text_part.text = "Hello, world!"
        chat_result.parts = [text_part]
        client.send_message.return_value = chat_result
        return client

    @pytest.mark.asyncio
    async def test_basic_execution(
        self, provider: OpenCodeProvider, runtime: MagicMock, mock_client: AsyncMock
    ) -> None:
        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=mock_client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")

            result = await provider.execute_sdk("Say hello", None, runtime)

        assert result.success is True
        assert result.output_text == "Hello, world!"
        assert result.session_id == "test-session-123"

    @pytest.mark.asyncio
    async def test_emits_prompt_sent(
        self, provider: OpenCodeProvider, runtime: MagicMock, mock_client: AsyncMock
    ) -> None:
        runtime.task_name = "t1"
        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=mock_client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")

            await provider.execute_sdk("Say hello", None, runtime)

        prompt_effects = _emitted_of_type(runtime, PromptSent)
        assert len(prompt_effects) == 1
        assert prompt_effects[0].user_prompt == "Say hello"
        assert prompt_effects[0].task_name == "t1"

    @pytest.mark.asyncio
    async def test_emits_agent_message(
        self, provider: OpenCodeProvider, runtime: MagicMock, mock_client: AsyncMock
    ) -> None:
        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=mock_client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")

            await provider.execute_sdk("Say hello", None, runtime)

        msg_effects = _emitted_of_type(runtime, AgentMessage)
        assert len(msg_effects) == 1
        assert msg_effects[0].content == "Hello, world!"
        assert msg_effects[0].is_partial is False

    @pytest.mark.asyncio
    async def test_emits_tool_call_batch(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        """When messages contain tool invocations, emit ToolCallBatch."""
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session

        # AssistantMessage with a tool invocation part (use dict format
        # matching what the real SDK returns from AssistantMessage.parts)
        chat_result = MagicMock()
        tool_part = {
            "type": "tool-invocation",
            "tool": "bash",
            "tool_name": "bash",
            "call_id": "tc1",
            "id": "tc1",
            "input": "echo hello",
            "output": "hello",
        }
        text_part = MagicMock()
        text_part.type = "text"
        text_part.text = "Done."
        chat_result.parts = [tool_part, text_part]
        client.send_message.return_value = chat_result

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")

            result = await provider.execute_sdk("Run echo", None, runtime)

        assert result.success is True
        batch_effects = _emitted_of_type(runtime, ToolCallBatch)
        assert len(batch_effects) == 1
        batch = batch_effects[0]
        assert len(batch.tool_calls) == 1
        assert batch.tool_calls[0].tool_name == "bash"
        assert batch.batch_id.startswith("opencode-batch-")

    @pytest.mark.asyncio
    async def test_server_start_failure(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        with patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls:
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(
                side_effect=RuntimeError("port in use")
            )

            result = await provider.execute_sdk("Say hello", None, runtime)

        assert result.success is False
        assert "Failed to start" in result.output_text
        fail_effects = _emitted_of_type(runtime, ExecutionFailed)
        assert len(fail_effects) == 1


# ---------------------------------------------------------------------------
# TestSessionForkFallback
# ---------------------------------------------------------------------------


class TestSessionForkFallback:
    """When forking fails (e.g., session doesn't exist on fresh server),
    the provider should fall back to creating a new session."""

    @pytest.mark.asyncio
    async def test_fork_failure_creates_new_session(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        client = AsyncMock()
        # Fork fails — session doesn't exist
        client.fork_session.side_effect = Exception("session not found")
        # Fallback: create new session
        session = MagicMock()
        session.id = "new-session"
        client.create_session.return_value = session

        chat_result = MagicMock()
        text_part = MagicMock()
        text_part.type = "text"
        text_part.text = "OK"
        chat_result.parts = [text_part]
        client.send_message.return_value = chat_result

        binding = ProviderBinding(
            session_id="old-session",
            session_isolation="forked",
            capabilities=frozenset({"read", "write", "bash"}),
        )

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")

            result = await provider.execute_sdk("Continue", binding, runtime)

        assert result.success is True
        assert result.session_id == "new-session"
        client.create_session.assert_called_once()


# ---------------------------------------------------------------------------
# TestEffectEmission
# ---------------------------------------------------------------------------


class TestEffectEmission:
    """Verify that the provider emits thinking effects correctly."""

    @pytest.mark.asyncio
    async def test_emits_thinking(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session

        chat_result = MagicMock()
        reasoning_part = MagicMock()
        reasoning_part.type = "reasoning"
        reasoning_part.text = "Let me think..."
        text_part = MagicMock()
        text_part.type = "text"
        text_part.text = "Result."
        chat_result.parts = [reasoning_part, text_part]
        client.send_message.return_value = chat_result

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")

            await provider.execute_sdk("Think", None, runtime)

        thinking_effects = _emitted_of_type(runtime, AgentThinking)
        assert len(thinking_effects) == 1
        assert thinking_effects[0].content == "Let me think..."

    @pytest.mark.asyncio
    async def test_no_tool_batch_when_no_tools(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        """ToolCallBatch should not be emitted if no tools were called."""
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session

        chat_result = MagicMock()
        text_part = MagicMock()
        text_part.type = "text"
        text_part.text = "Just text."
        chat_result.parts = [text_part]
        client.send_message.return_value = chat_result

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")

            await provider.execute_sdk("Hello", None, runtime)

        batch_effects = _emitted_of_type(runtime, ToolCallBatch)
        assert len(batch_effects) == 0


# ---------------------------------------------------------------------------
# TestLLMResponseReceived
# ---------------------------------------------------------------------------


def _make_assistant_message(
    *,
    input_tokens: float = 100,
    output_tokens: float = 50,
    reasoning_tokens: float = 0,
    cache_read: float = 10,
    cache_write: float = 5,
    cost: float = 0.003,
    created: float = 1000.0,
    completed: float = 1002.5,
    api_model_id: str = "claude-sonnet-4-20250514",
) -> MagicMock:
    """Build a mock AssistantMessage matching the real server response shape.

    The OpenCode server returns ``{"info": {...metadata...}, "parts": [...]}``.
    The SDK's ``model_construct()`` stores ``info`` as an extra field and leaves
    all typed fields (``tokens``, ``cost``, ``time``, ``api_model_id``) as None.

    This mock reproduces that shape so tests exercise the actual code path.
    """
    msg = MagicMock()
    msg.parts = []

    # Typed fields are None (real SDK behavior due to model_construct).
    msg.tokens = None
    msg.cost = None
    msg.time = None
    msg.api_model_id = None

    # Real metadata lives in the ``info`` dict (extra field from model_construct).
    msg.info = {
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "reasoning": reasoning_tokens,
            "total": input_tokens + output_tokens + reasoning_tokens,
            "cache": {"read": cache_read, "write": cache_write},
        },
        "cost": cost,
        "time": {"created": created, "completed": completed},
        "modelID": api_model_id,
        "providerID": "anthropic",
        "role": "assistant",
    }

    msg.role = "assistant"
    return msg


def _make_assistant_message_typed(
    *,
    input_tokens: float = 100,
    output_tokens: float = 50,
    reasoning_tokens: float = 0,
    cache_read: float = 10,
    cache_write: float = 5,
    cost: float = 0.003,
    created: float = 1000.0,
    completed: float = 1002.5,
    api_model_id: str = "claude-sonnet-4-20250514",
) -> MagicMock:
    """Build a mock AssistantMessage with typed fields populated (future SDK fix)."""
    msg = MagicMock()
    msg.parts = []
    msg.info = None  # No info dict in the typed case

    tokens = MagicMock()
    tokens.input = input_tokens
    tokens.output = output_tokens
    tokens.reasoning = reasoning_tokens
    cache = MagicMock()
    cache.read = cache_read
    cache.write = cache_write
    tokens.cache = cache
    msg.tokens = tokens

    msg.cost = cost

    time_info = MagicMock()
    time_info.created = created
    time_info.completed = completed
    msg.time = time_info

    msg.api_model_id = api_model_id
    msg.role = "assistant"

    return msg


class TestLLMResponseReceived:
    """Verify that the provider emits LLMResponseReceived with token metadata."""

    @pytest.mark.asyncio
    async def test_sync_path_emits_llm_response(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session
        client.send_message.return_value = _make_assistant_message()

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("test", None, runtime)

        llm_effects = _emitted_of_type(runtime, LLMResponseReceived)
        assert len(llm_effects) == 1
        effect = llm_effects[0]
        assert effect.input_tokens == 100
        assert effect.output_tokens == 50
        assert effect.total_tokens == 150
        assert effect.cost_usd == pytest.approx(0.003)
        assert effect.cache_read_input_tokens == 10
        assert effect.cache_creation_input_tokens == 5
        assert effect.model_id == "claude-sonnet-4-20250514"
        assert effect.is_error is False
        # API duration: (1002.5 - 1000.0) * 1000 = 2500ms
        assert effect.duration_api_ms == pytest.approx(2500.0)
        # Wall duration should be positive
        assert effect.duration_ms > 0

    @pytest.mark.asyncio
    async def test_sync_path_emits_llm_response_with_reasoning(
        self, provider: OpenCodeProvider, runtime: MagicMock
    ) -> None:
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session
        client.send_message.return_value = _make_assistant_message(output_tokens=50, reasoning_tokens=200)

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("test", None, runtime)

        effect = _emitted_of_type(runtime, LLMResponseReceived)[0]
        # Reasoning tokens are folded into output_tokens
        assert effect.output_tokens == 250  # 50 + 200
        assert effect.total_tokens == 350  # 100 + 250
        assert effect.usage_details == {"reasoning_tokens": 200}

    @pytest.mark.asyncio
    async def test_sync_path_emits_llm_response_on_error(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session
        client.send_message.side_effect = RuntimeError("connection lost")

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            result = await provider.execute_sdk("test", None, runtime)

        assert result.success is False
        llm_effects = _emitted_of_type(runtime, LLMResponseReceived)
        assert len(llm_effects) == 1
        assert llm_effects[0].is_error is True
        # No token data when the call failed before returning
        assert llm_effects[0].input_tokens == 0

    @pytest.mark.asyncio
    async def test_sync_path_defaults_model_to_provider_model(
        self, provider: OpenCodeProvider, runtime: MagicMock
    ) -> None:
        """When modelID is missing from info, fall back to the provider's model."""
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session
        msg = _make_assistant_message()
        del msg.info["modelID"]  # Not set in server response
        client.send_message.return_value = msg

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("test", None, runtime)

        effect = _emitted_of_type(runtime, LLMResponseReceived)[0]
        assert effect.model_id == "anthropic/claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_sync_path_extracts_from_typed_fields(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        """If a future SDK version populates typed fields, extraction still works."""
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session
        client.send_message.return_value = _make_assistant_message_typed()

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("test", None, runtime)

        effect = _emitted_of_type(runtime, LLMResponseReceived)[0]
        assert effect.input_tokens == 100
        assert effect.output_tokens == 50
        assert effect.total_tokens == 150
        assert effect.cost_usd == pytest.approx(0.003)
        assert effect.model_id == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_sync_path_handles_epoch_ms_timestamps(self, provider: OpenCodeProvider, runtime: MagicMock) -> None:
        """Server sends epoch-ms timestamps; verify correct duration calculation."""
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session
        # Real server timestamps are epoch milliseconds
        client.send_message.return_value = _make_assistant_message(created=1774852485340, completed=1774852488412)

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_registry_cls.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("test", None, runtime)

        effect = _emitted_of_type(runtime, LLMResponseReceived)[0]
        # 1774852488412 - 1774852485340 = 3072ms
        assert effect.duration_api_ms == pytest.approx(3072.0)


# ---------------------------------------------------------------------------
# Model config and registry tests
# ---------------------------------------------------------------------------


class TestModelConfig:
    """Verify that the registry writes opencode.json for model routing."""

    def test_write_model_config_creates_file(self, tmp_path: Any) -> None:
        from shepherd_providers.opencode._server import _write_model_config

        _write_model_config(str(tmp_path), "groq/llama-3.3-70b-versatile")

        import json

        config = json.loads((tmp_path / "opencode.json").read_text())
        assert config["agent"]["build"]["model"] == "groq/llama-3.3-70b-versatile"
        assert "groq" in config["provider"]
        # Permissions are NOT in the config file — they're passed via
        # OPENCODE_PERMISSION env var at server startup time.
        assert "permission" not in config["agent"]["build"]

    def test_write_model_config_preserves_existing_settings(self, tmp_path: Any) -> None:
        import json

        from shepherd_providers.opencode._server import _write_model_config

        # Pre-existing config with user settings
        existing = {
            "$schema": "https://opencode.ai/config.json",
            "agent": {"build": {"model": "anthropic/claude-sonnet-4-6", "temperature": 0.7}},
            "provider": {"anthropic": {}},
            "command": {"my-cmd": {"description": "user command"}},
        }
        (tmp_path / "opencode.json").write_text(json.dumps(existing))

        _write_model_config(str(tmp_path), "groq/llama-3.3-70b-versatile")

        config = json.loads((tmp_path / "opencode.json").read_text())
        # Model updated
        assert config["agent"]["build"]["model"] == "groq/llama-3.3-70b-versatile"
        # New provider registered
        assert "groq" in config["provider"]
        # Existing settings preserved
        assert config["agent"]["build"]["temperature"] == 0.7
        assert "anthropic" in config["provider"]
        assert config["command"]["my-cmd"]["description"] == "user command"
        assert config["$schema"] == "https://opencode.ai/config.json"

    def test_write_model_config_noop_when_already_set(self, tmp_path: Any) -> None:
        import json

        from shepherd_providers.opencode._server import _write_model_config

        config = {
            "agent": {"build": {"model": "groq/llama-3.3-70b-versatile"}},
            "provider": {"groq": {}},
        }
        (tmp_path / "opencode.json").write_text(json.dumps(config))
        mtime_before = (tmp_path / "opencode.json").stat().st_mtime_ns

        _write_model_config(str(tmp_path), "groq/llama-3.3-70b-versatile")

        # File should not be rewritten
        mtime_after = (tmp_path / "opencode.json").stat().st_mtime_ns
        assert mtime_before == mtime_after

    def test_write_model_config_handles_corrupt_file(self, tmp_path: Any) -> None:
        import json

        from shepherd_providers.opencode._server import _write_model_config

        (tmp_path / "opencode.json").write_text("not valid json{{{")

        _write_model_config(str(tmp_path), "groq/llama-3.3-70b-versatile")

        config = json.loads((tmp_path / "opencode.json").read_text())
        assert config["agent"]["build"]["model"] == "groq/llama-3.3-70b-versatile"

    @pytest.mark.asyncio
    async def test_registry_keys_by_cwd_and_model(self) -> None:
        """Different models in the same cwd get different servers."""
        from shepherd_providers.opencode._server import OpenCodeServerRegistry

        registry = OpenCodeServerRegistry()

        server_a = AsyncMock()
        server_a.health_check = AsyncMock(return_value=True)
        server_a.base_url = "http://127.0.0.1:1111"

        server_b = AsyncMock()
        server_b.health_check = AsyncMock(return_value=True)
        server_b.base_url = "http://127.0.0.1:2222"

        # Inject two servers with different models
        registry._servers[("/project", "groq/llama-3.3-70b")] = server_a
        registry._servers[("/project", "deepseek/deepseek-chat")] = server_b

        url_a = await registry.get_or_start("/project", model="groq/llama-3.3-70b")
        url_b = await registry.get_or_start("/project", model="deepseek/deepseek-chat")

        assert url_a == "http://127.0.0.1:1111"
        assert url_b == "http://127.0.0.1:2222"

    @pytest.mark.asyncio
    async def test_registry_writes_config_before_start(self, tmp_path: Any) -> None:
        """Registry writes opencode.json before starting the server."""
        import json

        from shepherd_providers.opencode._server import OpenCodeServerRegistry

        registry = OpenCodeServerRegistry()

        with patch("shepherd_providers.opencode._server.OpenCodeServer") as mock_server_cls:
            mock_server = AsyncMock()
            mock_server.health_check = AsyncMock(return_value=True)
            mock_server.base_url = "http://127.0.0.1:9999"
            mock_server_cls.return_value = mock_server

            await registry.get_or_start(str(tmp_path), model="together/Qwen/Qwen2.5-Coder-32B")

        # Config was written
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert config["agent"]["build"]["model"] == "together/Qwen/Qwen2.5-Coder-32B"
        assert "together" in config["provider"]

        # Server was started with headless permissions via env var
        mock_server.start.assert_called_once()
        call_kwargs = mock_server.start.call_args
        extra_env = call_kwargs[1].get("extra_env", {}) if call_kwargs[1] else {}
        assert "OPENCODE_PERMISSION" in extra_env
        import json as _json

        perm = _json.loads(extra_env["OPENCODE_PERMISSION"])
        assert perm["external_directory"] == "allow"
        assert perm["doom_loop"] == "allow"

    @pytest.mark.asyncio
    async def test_registry_no_config_write_without_model(self, tmp_path: Any) -> None:
        """When model is None, registry does not write opencode.json."""
        from shepherd_providers.opencode._server import OpenCodeServerRegistry

        registry = OpenCodeServerRegistry()

        with patch("shepherd_providers.opencode._server.OpenCodeServer") as mock_server_cls:
            mock_server = AsyncMock()
            mock_server.health_check = AsyncMock(return_value=True)
            mock_server.base_url = "http://127.0.0.1:9999"
            mock_server_cls.return_value = mock_server

            await registry.get_or_start(str(tmp_path), model=None)

        assert not (tmp_path / "opencode.json").exists()

    @pytest.mark.asyncio
    async def test_provider_passes_model_to_registry(self) -> None:
        """The provider passes self.model to registry.get_or_start()."""
        provider = OpenCodeProvider(
            name="test",
            model="groq/llama-3.3-70b-versatile",
        )
        rt = MagicMock()
        rt.task_name = "test"
        rt.effects = MagicMock()
        rt.effects.emit = MagicMock()
        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session
        client.send_message.return_value = _make_assistant_message()

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_registry_cls,
            patch(
                "shepherd_providers.opencode._client.OpenCodeClient",
                return_value=client,
            ),
        ):
            mock_get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            mock_registry_cls.get_instance.return_value.get_or_start = mock_get_or_start

            await provider.execute_sdk("test", None, rt)

        # Verify model was passed
        mock_get_or_start.assert_called_once()
        call_kwargs = mock_get_or_start.call_args
        assert call_kwargs[1].get("model") == "groq/llama-3.3-70b-versatile"
