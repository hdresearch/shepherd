"""Spike 5: Session reuse feasibility tests for StdioMCPBridge.

Validates that a single StdioMCPBridge session can be held open and reused
across multiple call sequences, simulating multiple execute_sdk() invocations
against a persistent MCP server subprocess.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

try:
    from shepherd_providers.openai._mcp_stdio_bridge import StdioMCPBridge, StdioSessionPool

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not installed")

# ---------------------------------------------------------------------------
# Inline MCP server scripts
# ---------------------------------------------------------------------------

_ECHO_SERVER_SCRIPT = textwrap.dedent("""\
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("reuse-echo-server")

    @mcp.tool()
    def echo(message: str) -> str:
        \"\"\"Echo the message back.\"\"\"
        return f"echo: {message}"

    @mcp.tool()
    def add(a: int, b: int) -> str:
        \"\"\"Add two numbers.\"\"\"
        return str(a + b)

    if __name__ == "__main__":
        mcp.run(transport="stdio")
""")

_STATEFUL_SERVER_SCRIPT = textwrap.dedent("""\
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("stateful-server")

    _counter = 0

    @mcp.tool()
    def increment() -> str:
        \"\"\"Increment the counter and return its value.\"\"\"
        global _counter
        _counter += 1
        return str(_counter)

    @mcp.tool()
    def get_count() -> str:
        \"\"\"Return the current counter value.\"\"\"
        return str(_counter)

    if __name__ == "__main__":
        mcp.run(transport="stdio")
""")


@pytest.fixture
def echo_script(tmp_path) -> str:
    script = tmp_path / "echo_server.py"
    script.write_text(_ECHO_SERVER_SCRIPT)
    return str(script)


@pytest.fixture
def stateful_script(tmp_path) -> str:
    script = tmp_path / "stateful_server.py"
    script.write_text(_STATEFUL_SERVER_SCRIPT)
    return str(script)


# ---------------------------------------------------------------------------
# Test 1: Session reuse across multiple call sequences
# ---------------------------------------------------------------------------


class TestSessionReuse:
    """Verify a bridge session survives across multiple call sequences."""

    async def test_call_wait_call_same_session(self, echo_script):
        """Start bridge, call tool, wait, call tool again -- same session."""
        async with StdioMCPBridge() as bridge:
            await bridge.start(sys.executable, [echo_script])

            # First call sequence
            r1 = await bridge.call_tool("echo", {"message": "first"})
            assert not r1.isError
            assert r1.content[0].text == "echo: first"

            # Simulate idle gap between execute_sdk() calls
            await asyncio.sleep(0.5)

            # Second call sequence -- same session
            assert bridge.is_alive
            r2 = await bridge.call_tool("echo", {"message": "second"})
            assert not r2.isError
            assert r2.content[0].text == "echo: second"

            # Third call to a different tool
            r3 = await bridge.call_tool("add", {"a": 10, "b": 20})
            assert not r3.isError
            assert r3.content[0].text == "30"

    async def test_many_sequential_calls(self, echo_script):
        """20 sequential calls on the same session all succeed."""
        async with StdioMCPBridge() as bridge:
            await bridge.start(sys.executable, [echo_script])

            for i in range(20):
                result = await bridge.call_tool("echo", {"message": f"iter-{i}"})
                assert not result.isError
                assert result.content[0].text == f"echo: iter-{i}"

            assert bridge.is_alive


# ---------------------------------------------------------------------------
# Test 2: Stateful server -- state persists across calls
# ---------------------------------------------------------------------------


class TestStatePersistence:
    """Verify that server-side state persists across calls within a session."""

    async def test_counter_increments_across_calls(self, stateful_script):
        """Counter state in the MCP server persists across call_tool calls."""
        async with StdioMCPBridge() as bridge:
            await bridge.start(sys.executable, [stateful_script])

            # Increment three times
            for expected in range(1, 4):
                result = await bridge.call_tool("increment", {})
                assert not result.isError
                assert result.content[0].text == str(expected)

            # Read back the count
            result = await bridge.call_tool("get_count", {})
            assert not result.isError
            assert result.content[0].text == "3"

    async def test_state_persists_after_idle(self, stateful_script):
        """State survives an idle gap between call sequences."""
        async with StdioMCPBridge() as bridge:
            await bridge.start(sys.executable, [stateful_script])

            # First sequence
            await bridge.call_tool("increment", {})
            await bridge.call_tool("increment", {})

            # Idle gap
            await asyncio.sleep(0.3)

            # Second sequence -- counter should continue from 2
            result = await bridge.call_tool("increment", {})
            assert not result.isError
            assert result.content[0].text == "3"


# ---------------------------------------------------------------------------
# Test 3: Bridge as a field on a class instance (simulating provider)
# ---------------------------------------------------------------------------


class TestBridgeAsClassField:
    """Verify bridge can be held as a field on a class, like a provider would."""

    async def test_bridge_on_provider_like_class(self, echo_script):
        """Bridge stored as instance attribute survives multiple method calls."""

        class FakeProvider:
            def __init__(self):
                self.bridge = StdioMCPBridge()

            async def setup(self, command: str, args: list[str]):
                await self.bridge.start(command, args)

            async def execute(self, message: str) -> str:
                result = await self.bridge.call_tool("echo", {"message": message})
                return result.content[0].text

            async def teardown(self):
                await self.bridge.stop()

        provider = FakeProvider()
        try:
            await provider.setup(sys.executable, [echo_script])

            # Simulate multiple execute_sdk() calls
            assert await provider.execute("call-1") == "echo: call-1"
            await asyncio.sleep(0.1)
            assert await provider.execute("call-2") == "echo: call-2"
            await asyncio.sleep(0.1)
            assert await provider.execute("call-3") == "echo: call-3"

            assert provider.bridge.is_alive
        finally:
            await provider.teardown()

        assert not provider.bridge.is_alive


# ---------------------------------------------------------------------------
# Test 4: Subprocess death mid-session
# ---------------------------------------------------------------------------


class TestSubprocessDeath:
    """Verify clean error when the server subprocess dies mid-session.

    Note: Detailed subprocess kill/recover tests are in test_mcp_stdio_lifecycle.py.
    This test verifies the session pool replaces dead sessions (tested below).
    """


# ---------------------------------------------------------------------------
# Test 5: StdioSessionPool
# ---------------------------------------------------------------------------


class TestStdioSessionPool:
    """Verify the session pool caches and health-checks sessions."""

    async def test_pool_returns_same_bridge(self, echo_script):
        """Consecutive get() calls for the same server return the same bridge."""
        pool = StdioSessionPool()
        try:
            b1 = await pool.get("echo", sys.executable, [echo_script])
            b2 = await pool.get("echo", sys.executable, [echo_script])
            assert b1 is b2
            assert b1.is_alive

            # Verify it actually works
            r = await b1.call_tool("echo", {"message": "pooled"})
            assert r.content[0].text == "echo: pooled"
        finally:
            await pool.close_all()

    async def test_pool_different_servers(self, echo_script, stateful_script):
        """Different server names get different bridges."""
        pool = StdioSessionPool()
        try:
            b1 = await pool.get("echo", sys.executable, [echo_script])
            b2 = await pool.get("stateful", sys.executable, [stateful_script])
            assert b1 is not b2
            assert len(pool.active_sessions) == 2

            # Both work
            r1 = await b1.call_tool("echo", {"message": "a"})
            r2 = await b2.call_tool("increment", {})
            assert r1.content[0].text == "echo: a"
            assert r2.content[0].text == "1"
        finally:
            await pool.close_all()

    async def test_pool_close_all(self, echo_script):
        """close_all() stops all sessions."""
        pool = StdioSessionPool()
        b = await pool.get("echo", sys.executable, [echo_script])
        assert b.is_alive
        await pool.close_all()
        assert not b.is_alive
        assert len(pool.active_sessions) == 0

    async def test_pool_replaces_dead_session(self, echo_script):
        """Pool replaces a dead session with a fresh one on get()."""
        pool = StdioSessionPool()
        try:
            b1 = await pool.get("echo", sys.executable, [echo_script])
            # Manually kill the bridge to simulate process death
            await b1.stop()
            assert not b1.is_alive

            # get() should detect the dead session and start a new one
            b2 = await pool.get("echo", sys.executable, [echo_script])
            assert b2 is not b1
            assert b2.is_alive

            r = await b2.call_tool("echo", {"message": "revived"})
            assert r.content[0].text == "echo: revived"
        finally:
            await pool.close_all()

    async def test_pool_concurrent_access(self, echo_script):
        """Multiple tasks can concurrently request the same server safely."""
        pool = StdioSessionPool()
        try:
            bridges: list[StdioMCPBridge] = []

            async def get_and_call(i: int):
                b = await pool.get("echo", sys.executable, [echo_script])
                bridges.append(b)
                r = await b.call_tool("echo", {"message": f"concurrent-{i}"})
                assert not r.isError
                return r.content[0].text

            tasks = [asyncio.create_task(get_and_call(i)) for i in range(5)]
            results = await asyncio.gather(*tasks)

            # All should have gotten the same bridge instance
            assert all(b is bridges[0] for b in bridges)

            # All calls succeeded
            assert len(results) == 5
            for i, text in enumerate(sorted(results)):
                # Just verify they all returned valid results
                assert text.startswith("echo: concurrent-")
        finally:
            await pool.close_all()
