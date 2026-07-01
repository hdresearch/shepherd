"""Integration tests for OpenCode provider against a live server.

These tests require `opencode serve` to be available and a model provider
API key to be configured. Skip with: pytest -m "not integration"
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from shepherd_providers.opencode._client import OpenCodeClient
from shepherd_providers.opencode._server import OpenCodeServer

# Skip all tests if no API key or opencode CLI is available
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("opencode") is None,
        reason="opencode CLI not found",
    ),
    pytest.mark.skipif(
        not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")),
        reason="No model provider API key available",
    ),
]


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def server():
    """Start a server for the test module."""
    srv = OpenCodeServer(cwd=str(Path.cwd()))
    await srv.start(timeout=15.0)
    yield srv
    await srv.stop()


@pytest.fixture
async def client(server: OpenCodeServer):
    """Create a client connected to the test server."""
    c = OpenCodeClient(server.base_url)
    yield c
    await c.close()


class TestServerLifecycle:
    @pytest.mark.asyncio
    async def test_server_starts_and_has_url(self, server: OpenCodeServer) -> None:
        assert server.base_url.startswith("http://")

    @pytest.mark.asyncio
    async def test_health_check(self, server: OpenCodeServer) -> None:
        assert await server.health_check() is True


class TestSessionCRUD:
    @pytest.mark.asyncio
    async def test_create_session(self, client: OpenCodeClient) -> None:
        session = await client.create_session()
        session_id = session.id if hasattr(session, "id") else str(session)
        assert session_id

    @pytest.mark.asyncio
    async def test_get_messages_empty(self, client: OpenCodeClient) -> None:
        session = await client.create_session()
        session_id = session.id if hasattr(session, "id") else str(session)
        messages = await client.get_messages(session_id)
        assert isinstance(messages, list)
