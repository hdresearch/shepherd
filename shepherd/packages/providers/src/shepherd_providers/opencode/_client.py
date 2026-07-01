"""OpenCode SDK wrapper with bug workarounds.

Encapsulates the opencode-ai SDK's alpha-stage quirks behind a clean async
interface. Uses the SDK for typed operations and a raw httpx client for
endpoints not yet exposed by the SDK.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_sdk() -> Any:
    """Lazily import the opencode SDK."""
    try:
        from opencode_ai import AsyncOpencode

        return AsyncOpencode
    except ImportError as e:
        raise ImportError("opencode-ai SDK not found. Install with: pip install opencode-ai") from e


class OpenCodeClient:
    """Async wrapper around the opencode-ai SDK with workarounds for known bugs.

    Uses dual-client pattern: the SDK for typed operations and a raw httpx
    client for endpoints not exposed by the SDK (fork, MCP management).
    """

    def __init__(self, base_url: str) -> None:
        # Tool-using tasks can take several minutes; the SDK's default timeout
        # (60s) is far too short.  Use 10 minutes for both SDK and raw HTTP.
        _TIMEOUT = 600.0

        self._base_url = base_url
        AsyncOpencode = _get_sdk()
        self._sdk = AsyncOpencode(base_url=base_url, timeout=_TIMEOUT)

        import httpx

        self._http = httpx.AsyncClient(base_url=base_url, timeout=_TIMEOUT)

    async def create_session(self) -> Any:
        """Create a new session.

        Workaround: SDK's session.create() sends no JSON body, causing
        "Malformed JSON in request body" error. Passing extra_body={}
        forces the SDK to send an empty JSON object.
        """
        return await self._sdk.session.create(extra_body={})

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        provider_id: str,
        model_id: str,
        system: str | None = None,
        tools: dict[str, bool] | None = None,
    ) -> Any:
        """Send a message and wait for the response (blocking chat).

        Args:
            session_id: Session to send the message to.
            message: The user message text.
            provider_id: OpenCode provider ID (e.g., "anthropic"). Required.
            model_id: Model ID within that provider. Required.
            system: System prompt to use for this chat call.
            tools: Dict of tool_name -> enabled for tool gating.

        Returns:
            The chat response from the SDK (AssistantMessage).
        """
        kwargs: dict[str, Any] = {
            "id": session_id,
            "parts": [{"type": "text", "text": message}],
            "provider_id": provider_id,
            "model_id": model_id,
        }

        if system is not None:
            kwargs["system"] = system
        if tools is not None:
            kwargs["tools"] = tools

        return await self._sdk.session.chat(**kwargs)

    async def get_messages(self, session_id: str) -> list[Any]:
        """Get all messages for a session.

        Workaround: SDK returns an unstructured response. This method
        normalizes it to a flat list.
        """
        result = await self._sdk.session.messages(id=session_id)
        if isinstance(result, list):
            return result
        # Some SDK versions return a wrapper object
        if hasattr(result, "messages"):
            return list(result.messages)
        return [result]

    async def fork_session(self, session_id: str) -> Any:
        """Fork a session (raw HTTP — not exposed by SDK).

        Returns:
            The fork response containing the new session ID.

        Raises:
            httpx.HTTPStatusError: If the fork fails (e.g., session not found).
        """
        resp = await self._http.post(f"/api/session/{session_id}/fork")
        resp.raise_for_status()
        return resp.json()

    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        await self._sdk.session.delete(id=session_id)

    async def register_mcp_server(self, name: str, config: dict[str, Any]) -> Any:
        """Register an MCP server with the OpenCode server.

        Args:
            name: Unique name for the MCP server.
            config: Server configuration. For stdio servers:
                {"command": "...", "args": [...], "env": {...}}
                For remote servers:
                {"type": "remote", "url": "...", "headers": {...}}

        Returns:
            Registration response.
        """
        resp = await self._http.post(
            "/api/mcp",
            json={name: config},
        )
        resp.raise_for_status()
        return resp.json()

    async def remove_mcp_server(self, name: str) -> None:
        """Remove a registered MCP server.

        Args:
            name: Name of the MCP server to remove.
        """
        resp = await self._http.delete(f"/api/mcp/{name}")
        # Response may be HTML (known SDK quirk) — just check status
        if resp.status_code >= 400:
            logger.warning(f"Failed to remove MCP server {name}: HTTP {resp.status_code}")

    async def subscribe_events(self) -> Any:
        """Subscribe to the SSE event stream.

        Returns an AsyncStream that yields typed event objects.
        The stream is global (all sessions) — callers must filter by session_id.

        Returns:
            AsyncStream[EventListResponse] from the SDK.
        """
        return await self._sdk.event.list()

    async def close(self) -> None:
        """Close both SDK and HTTP clients."""
        try:
            await self._sdk.close()
        except (OSError, RuntimeError) as e:
            logger.debug(f"Error closing OpenCode SDK client: {e}")
        try:
            await self._http.aclose()
        except (OSError, RuntimeError) as e:
            logger.debug(f"Error closing HTTP client: {e}")


__all__ = ["OpenCodeClient"]
