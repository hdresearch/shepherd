"""Tests for MCP server registration and passthrough.

Tests cover: external MCP server registration, cleanup, isolation via
execution-scoped names, and graceful handling of registration failures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shepherd_core.types import ProviderBinding, ToolDefinition
from shepherd_providers.opencode.provider import OpenCodeProvider


def _make_mock_client(session_id: str = "s1") -> AsyncMock:
    client = AsyncMock()
    session = MagicMock()
    session.id = session_id
    client.create_session.return_value = session

    chat_result = MagicMock()
    text_part = MagicMock()
    text_part.type = "text"
    text_part.text = "Done."
    chat_result.parts = [text_part]
    client.send_message.return_value = chat_result

    # MCP methods
    client.register_mcp_server = AsyncMock(return_value={"status": "connected"})
    client.remove_mcp_server = AsyncMock()

    return client


class TestMCPPassthrough:
    """External MCP servers from binding.mcp_servers should be registered."""

    @pytest.mark.asyncio
    async def test_registers_external_mcp_servers(self) -> None:
        provider = OpenCodeProvider(name="test", streaming=False)
        client = _make_mock_client()
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()

        binding = ProviderBinding(
            capabilities=frozenset({"read", "write", "bash"}),
            mcp_servers={
                "filesystem": {"command": "npx", "args": ["-y", "mcp-filesystem"]},
                "search": {"type": "remote", "url": "https://search.example.com/mcp"},
            },
        )

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("Hello", binding, runtime)

        # Both servers should be registered with execution-scoped names
        assert client.register_mcp_server.call_count == 2
        registered_names = [call.args[0] for call in client.register_mcp_server.call_args_list]
        # Names should be scoped with execution ID suffix
        assert any(name.startswith("filesystem-") for name in registered_names)
        assert any(name.startswith("search-") for name in registered_names)

    @pytest.mark.asyncio
    async def test_cleans_up_mcp_servers(self) -> None:
        provider = OpenCodeProvider(name="test", streaming=False)
        client = _make_mock_client()
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()

        binding = ProviderBinding(
            capabilities=frozenset({"read"}),
            mcp_servers={"myserver": {"command": "echo"}},
        )

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("Hello", binding, runtime)

        # Server should be cleaned up
        assert client.remove_mcp_server.call_count == 1

    @pytest.mark.asyncio
    async def test_cleanup_on_execution_error(self) -> None:
        """MCP servers should be cleaned up even if execution fails."""
        provider = OpenCodeProvider(name="test", streaming=False)
        client = _make_mock_client()
        client.send_message.side_effect = RuntimeError("chat failed")
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()

        binding = ProviderBinding(
            capabilities=frozenset({"read"}),
            mcp_servers={"myserver": {"command": "echo"}},
        )

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            result = await provider.execute_sdk("Hello", binding, runtime)

        assert result.success is False
        # Cleanup should still happen
        assert client.remove_mcp_server.call_count == 1

    @pytest.mark.asyncio
    async def test_registration_failure_is_non_fatal(self) -> None:
        """Failed registration should log warning but not abort execution."""
        provider = OpenCodeProvider(name="test", streaming=False)
        client = _make_mock_client()
        client.register_mcp_server.side_effect = RuntimeError("connection refused")
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()

        binding = ProviderBinding(
            capabilities=frozenset({"read"}),
            mcp_servers={"myserver": {"command": "echo"}},
        )

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            result = await provider.execute_sdk("Hello", binding, runtime)

        # Execution should still succeed (server just isn't available)
        assert result.success is True


class TestMCPIsolation:
    """MCP server names should be scoped per-execution."""

    @pytest.mark.asyncio
    async def test_names_are_execution_scoped(self) -> None:
        """Two executions should use different MCP server names."""
        provider = OpenCodeProvider(name="test", streaming=False)
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()
        binding = ProviderBinding(
            capabilities=frozenset({"read"}),
            mcp_servers={"shared-server": {"command": "echo"}},
        )

        names_seen: list[str] = []

        async def capture_register(name: str, config: dict) -> dict:
            names_seen.append(name)
            return {"status": "connected"}

        for _ in range(2):
            client = _make_mock_client()
            client.register_mcp_server = AsyncMock(side_effect=capture_register)

            with (
                patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
                patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
            ):
                mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
                await provider.execute_sdk("Hello", binding, runtime)

        assert len(names_seen) == 2
        assert names_seen[0] != names_seen[1]  # Different execution IDs
        assert names_seen[0].startswith("shared-server-")
        assert names_seen[1].startswith("shared-server-")


class TestCustomToolsWarning:
    """Custom tools with handlers should warn (not yet implemented)."""

    @pytest.mark.asyncio
    async def test_custom_tools_log_warning(self) -> None:
        provider = OpenCodeProvider(name="test", streaming=False)
        client = _make_mock_client()
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()

        tool_def = ToolDefinition(
            name="my_tool",
            description="A test tool",
            parameters_schema={"type": "object", "properties": {}},
            handler=lambda params: "result",
        )
        binding = ProviderBinding(
            capabilities=frozenset({"read"}),
            custom_tools=(tool_def,),
        )

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
            patch("shepherd_providers.opencode.provider.logger") as mock_logger,
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            result = await provider.execute_sdk("Hello", binding, runtime)

        assert result.success is True
        # Should have logged a warning about unimplemented custom tools
        mock_logger.warning.assert_any_call(
            "Ignoring 1 custom_tools — MCP wrapping for ToolDefinition handlers is not yet implemented"
        )


class TestNoMCPServers:
    """No MCP operations when binding has no servers."""

    @pytest.mark.asyncio
    async def test_no_mcp_when_empty(self) -> None:
        provider = OpenCodeProvider(name="test", streaming=False)
        client = _make_mock_client()
        runtime = MagicMock()
        runtime.task_name = "test"
        runtime.effects = MagicMock()
        runtime.effects.emit = MagicMock()

        binding = ProviderBinding(capabilities=frozenset({"read"}))

        with (
            patch("shepherd_providers.opencode._server.OpenCodeServerRegistry") as mock_reg,
            patch("shepherd_providers.opencode._client.OpenCodeClient", return_value=client),
        ):
            mock_reg.get_instance.return_value.get_or_start = AsyncMock(return_value="http://127.0.0.1:4096")
            await provider.execute_sdk("Hello", binding, runtime)

        client.register_mcp_server.assert_not_called()
        client.remove_mcp_server.assert_not_called()
