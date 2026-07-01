"""Phase 2 tests: Session management and verbose output.

Tests cover: session isolation modes (shared/forked/isolated),
verbose formatter integration, and MCP-related validation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.types import ProviderBinding
from shepherd_providers.opencode.provider import OpenCodeProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(session_id: str = "s1", response_text: str = "OK") -> AsyncMock:
    """Create a mock OpenCodeClient with standard responses."""
    client = AsyncMock()
    session = MagicMock()
    session.id = session_id
    client.create_session.return_value = session

    chat_result = MagicMock()
    text_part = MagicMock()
    text_part.type = "text"
    text_part.text = response_text
    chat_result.parts = [text_part]
    client.send_message.return_value = chat_result
    return client


def _patch_server_and_client(client: AsyncMock):
    """Return context managers that patch the registry and client."""
    return (
        patch("shepherd_providers.opencode._server.OpenCodeServerRegistry"),
        patch(
            "shepherd_providers.opencode._client.OpenCodeClient",
            return_value=client,
        ),
    )


async def _execute(
    provider: OpenCodeProvider,
    binding: ProviderBinding | None,
    client: AsyncMock,
    prompt: str = "Hello",
) -> Any:
    """Execute with patched server and client."""
    runtime = MagicMock()
    runtime.task_name = "test"
    runtime.effects = MagicMock()
    runtime.effects.emit = MagicMock()
    with (
        patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
        patch(
            "shepherd_providers.opencode._client.OpenCodeClient",
            return_value=client,
        ),
    ):
        mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
        result = await provider.execute_sdk(prompt, binding, runtime)
    return result, runtime


# ---------------------------------------------------------------------------
# TestSessionIsolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    """Test all three session isolation modes."""

    @pytest.mark.asyncio
    async def test_shared_resumes_existing_session(self) -> None:
        """shared + session_id → resume (no create, no fork)."""
        client = _make_mock_client()
        provider = OpenCodeProvider(name="test", streaming=False)
        binding = ProviderBinding(
            session_id="existing-session",
            session_isolation="shared",
            capabilities=frozenset({"read"}),
        )

        result, _ = await _execute(provider, binding, client)

        assert result.success is True
        assert result.session_id == "existing-session"
        client.create_session.assert_not_called()
        client.fork_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_isolated_always_creates_new(self) -> None:
        """isolated → always create new, even if session_id is provided."""
        client = _make_mock_client(session_id="new-session")
        provider = OpenCodeProvider(name="test", streaming=False)
        binding = ProviderBinding(
            session_id="old-session",
            session_isolation="isolated",
            capabilities=frozenset({"read"}),
        )

        result, _ = await _execute(provider, binding, client)

        assert result.success is True
        assert result.session_id == "new-session"
        client.create_session.assert_called_once()
        client.fork_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_forked_calls_fork(self) -> None:
        """forked + session_id → fork the session."""
        client = _make_mock_client()
        client.fork_session.return_value = {"id": "forked-session"}
        provider = OpenCodeProvider(name="test", streaming=False)
        binding = ProviderBinding(
            session_id="parent-session",
            session_isolation="forked",
            capabilities=frozenset({"read"}),
        )

        result, _ = await _execute(provider, binding, client)

        assert result.success is True
        assert result.session_id == "forked-session"
        client.fork_session.assert_called_once_with("parent-session")
        client.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_forked_fallback_on_failure(self) -> None:
        """forked but fork fails → fall back to create."""
        client = _make_mock_client(session_id="fallback-session")
        client.fork_session.side_effect = Exception("not found")
        provider = OpenCodeProvider(name="test", streaming=False)
        binding = ProviderBinding(
            session_id="nonexistent",
            session_isolation="forked",
            capabilities=frozenset({"read"}),
        )

        result, _ = await _execute(provider, binding, client)

        assert result.success is True
        assert result.session_id == "fallback-session"
        client.create_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_session_id_creates_new(self) -> None:
        """No session_id → create new regardless of isolation mode."""
        client = _make_mock_client(session_id="fresh")
        provider = OpenCodeProvider(name="test", streaming=False)
        binding = ProviderBinding(
            capabilities=frozenset({"read"}),
        )

        result, _ = await _execute(provider, binding, client)

        assert result.session_id == "fresh"
        client.create_session.assert_called_once()


# ---------------------------------------------------------------------------
# TestVerboseOutput
# ---------------------------------------------------------------------------


class TestVerboseOutput:
    """Test verbose formatter integration."""

    def _make_verbose_provider(self) -> OpenCodeProvider:
        from shepherd_providers.verbose import VerboseConfig

        return OpenCodeProvider(
            name="verbose-test",
            verbose=VerboseConfig(enabled=True),
            streaming=False,
        )

    @pytest.mark.asyncio
    async def test_formatter_on_prompt_sent(self) -> None:
        provider = self._make_verbose_provider()
        assert provider._formatter is not None

        # Mock the formatter
        provider._formatter.on_prompt_sent = MagicMock()  # type: ignore[method-assign]

        client = _make_mock_client()
        await _execute(provider, None, client, prompt="Hello world")

        provider._formatter.on_prompt_sent.assert_called_once()
        args = provider._formatter.on_prompt_sent.call_args
        assert args[0][1] == "Hello world"  # user_prompt

    @pytest.mark.asyncio
    async def test_formatter_on_text_complete(self) -> None:
        provider = self._make_verbose_provider()
        provider._formatter.on_text_complete = MagicMock()  # type: ignore[method-assign]

        client = _make_mock_client(response_text="The answer is 42.")
        await _execute(provider, None, client)

        provider._formatter.on_text_complete.assert_called_once_with("The answer is 42.")

    @pytest.mark.asyncio
    async def test_formatter_on_thinking_complete(self) -> None:
        provider = self._make_verbose_provider()
        provider._formatter.on_thinking_complete = MagicMock()  # type: ignore[method-assign]

        client = AsyncMock()
        session = MagicMock()
        session.id = "s1"
        client.create_session.return_value = session

        chat_result = MagicMock()
        reasoning = MagicMock()
        reasoning.type = "reasoning"
        reasoning.text = "Let me think..."
        text = MagicMock()
        text.type = "text"
        text.text = "Done."
        chat_result.parts = [reasoning, text]
        client.send_message.return_value = chat_result

        await _execute(provider, None, client)

        provider._formatter.on_thinking_complete.assert_called_once_with("Let me think...")

    @pytest.mark.asyncio
    async def test_formatter_on_tool_calls(self) -> None:
        provider = self._make_verbose_provider()
        provider._formatter.on_tool_call_started = MagicMock()  # type: ignore[method-assign]
        provider._formatter.on_tool_call_completed = MagicMock()  # type: ignore[method-assign]

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

        await _execute(provider, None, client)

        provider._formatter.on_tool_call_started.assert_called_once_with("bash", "echo hi")
        provider._formatter.on_tool_call_completed.assert_called_once_with("bash", "hi", False)

    @pytest.mark.asyncio
    async def test_no_formatter_when_verbose_disabled(self) -> None:
        """When verbose is disabled, no formatter calls should happen."""
        provider = OpenCodeProvider(name="quiet", streaming=False)
        assert provider._formatter is None

        client = _make_mock_client()
        result, _ = await _execute(provider, None, client)
        assert result.success is True  # Should work without formatter
