"""MCP stdio bridge -- manages a single MCP server session over stdio.

Self-contained module with no dependency on the provider class.
Imports only from the ``mcp`` SDK and the Python standard library.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import os
from typing import TYPE_CHECKING, Any, Self

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

if TYPE_CHECKING:
    from mcp.types import CallToolResult, Tool


class StdioMCPBridge:
    """Manages a single MCP server process over stdio transport.

    Usage::

        bridge = StdioMCPBridge()
        await bridge.start("uvx", ["my-mcp-server"])
        tools = bridge.tools
        result = await bridge.call_tool("echo", {"message": "hi"})
        await bridge.stop()

    Or as an async context manager::

        async with StdioMCPBridge() as bridge:
            await bridge.start("uvx", ["my-mcp-server"])
            ...
    """

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._tools: list[Tool] = []
        self._cm_stack: list[Any] = []
        self._errlog: Any = None
        self._stopped = False

    # -- lifecycle ------------------------------------------------------------

    async def start(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Spawn the MCP server subprocess and initialise the session."""
        params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
        )

        # errlog must be a real file object with fileno() -- not io.StringIO.
        self._errlog = open(os.devnull, "w")  # noqa: SIM115, ASYNC230

        # Enter the stdio_client context manager manually so we can keep the
        # session alive across multiple calls.
        stdio_cm = stdio_client(params, errlog=self._errlog)
        read_stream, write_stream = await stdio_cm.__aenter__()
        self._cm_stack.append(stdio_cm)

        session_cm = ClientSession(read_stream, write_stream)
        self._session = await session_cm.__aenter__()
        self._cm_stack.append(session_cm)

        await self._session.initialize()

        tools_result = await self._session.list_tools()
        self._tools = list(tools_result.tools)

    @property
    def tools(self) -> list[Tool]:
        """Return the discovered MCP tools."""
        return list(self._tools)

    @property
    def is_alive(self) -> bool:
        """Return True if the session appears usable."""
        return self._session is not None and not self._stopped

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        """Route a tool call to the MCP session."""
        if self._session is None:
            raise RuntimeError("Bridge not started -- call start() first")
        return await self._session.call_tool(name, arguments or {})

    async def stop(self) -> None:
        """Cleanly shut down the session and subprocess."""
        self._stopped = True
        # Exit context managers in reverse order.
        for cm in reversed(self._cm_stack):
            with contextlib.suppress(BaseException):
                await cm.__aexit__(None, None, None)
        self._cm_stack.clear()
        self._session = None
        if self._errlog is not None:
            with contextlib.suppress(Exception):
                self._errlog.close()
            self._errlog = None

    # -- async context manager ------------------------------------------------

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.stop()


# ---------------------------------------------------------------------------
# Schema translation: MCP Tool -> OpenAI function tool format
# ---------------------------------------------------------------------------

_MAX_REF_DEPTH = 20


def _resolve_refs(
    schema: dict[str, Any],
    defs: dict[str, Any],
    _depth: int = 0,
) -> dict[str, Any]:
    """Recursively resolve ``$ref`` pointers using the provided ``$defs``.

    A depth limit prevents infinite loops on self-referencing schemas (e.g., a
    tree node whose ``children`` field references itself).  When the limit is
    hit the ``$ref`` is dropped and an ``{"type": "object"}`` placeholder is
    returned.
    """
    if _depth > _MAX_REF_DEPTH:
        return {"type": "object"}

    if "$ref" in schema:
        ref_path = schema["$ref"]  # e.g. "#/$defs/MyType"
        ref_name = ref_path.rsplit("/", 1)[-1]
        if ref_name in defs:
            resolved = _resolve_refs(copy.deepcopy(defs[ref_name]), defs, _depth + 1)
            merged = {k: v for k, v in schema.items() if k != "$ref"}
            merged.update(resolved)
            return merged
        return {k: v for k, v in schema.items() if k != "$ref"}

    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "$defs":
            continue
        if isinstance(value, dict):
            result[key] = _resolve_refs(value, defs, _depth + 1)
        elif isinstance(value, list):
            result[key] = [_resolve_refs(item, defs, _depth + 1) if isinstance(item, dict) else item for item in value]
        else:
            result[key] = value
    return result


_UNSUPPORTED_KEYS = frozenset({"default", "examples", "$schema", "$id"})


def _strip_unsupported(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove keys unsupported by OpenAI function calling."""
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_KEYS:
            continue
        if isinstance(value, dict):
            result[key] = _strip_unsupported(value)
        elif isinstance(value, list):
            result[key] = [_strip_unsupported(item) if isinstance(item, dict) else item for item in value]
        else:
            result[key] = value
    return result


def _normalise_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Full normalisation pipeline for a single tool's input schema."""
    # 1. Resolve $ref / $defs
    defs = schema.get("$defs", {})
    resolved = _resolve_refs(schema, defs)
    # 2. Strip unsupported fields
    return _strip_unsupported(resolved)


def mcp_tools_to_function_schemas(
    tools: list[Tool],
    server_name: str,
) -> list[dict[str, Any]]:
    """Convert MCP tools to OpenAI function tool format.

    Tool names follow ``mcp__{server_name}__{tool_name}`` convention so the
    provider can route calls back to the correct MCP server.

    Returns a list of dicts matching OpenAI's tool schema::

        {"type": "function", "function": {"name": "mcp__myserver__echo", "description": "...", "parameters": {...}}}
    """
    result: list[dict[str, Any]] = []
    for tool in tools:
        params = (
            _normalise_schema(tool.inputSchema)
            if tool.inputSchema is not None
            else {"type": "object", "properties": {}}
        )

        fn_schema: dict[str, Any] = {
            "name": f"mcp__{server_name}__{tool.name}",
            "parameters": params,
        }
        if tool.description:
            fn_schema["description"] = tool.description

        result.append(
            {
                "type": "function",
                "function": fn_schema,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Session pool -- reusable MCP stdio sessions keyed by server name
# ---------------------------------------------------------------------------


class StdioSessionPool:
    """Pool of reusable MCP stdio sessions, keyed by server name.

    Provides cached access to :class:`StdioMCPBridge` instances so that
    multiple ``execute_sdk()`` calls can reuse the same server subprocess
    instead of paying cold-start overhead each time.

    Thread-safe for concurrent access from multiple asyncio tasks -- each
    server name gets its own :class:`asyncio.Lock`.

    Usage::

        pool = StdioSessionPool()
        bridge = await pool.get("my-server", "uvx", ["my-mcp-server"])
        result = await bridge.call_tool("echo", {"message": "hi"})
        # ... later ...
        await pool.close_all()
    """

    def __init__(self) -> None:
        self._sessions: dict[str, StdioMCPBridge] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_lock(self, server_name: str) -> asyncio.Lock:
        """Return a per-server lock, creating it if necessary."""
        async with self._global_lock:
            if server_name not in self._locks:
                self._locks[server_name] = asyncio.Lock()
            return self._locks[server_name]

    async def get(
        self,
        server_name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> StdioMCPBridge:
        """Return a cached session or start a new one.

        If a cached session exists but its process is dead, it is replaced
        with a freshly started bridge.
        """
        lock = await self._get_lock(server_name)
        async with lock:
            bridge = self._sessions.get(server_name)

            if bridge is not None and bridge.is_alive:
                return bridge

            # Existing bridge is dead or missing -- clean up if needed.
            if bridge is not None:
                with contextlib.suppress(Exception):
                    await bridge.stop()

            # Start a fresh session.
            new_bridge = StdioMCPBridge()
            await new_bridge.start(command, args, env)
            self._sessions[server_name] = new_bridge
            return new_bridge

    async def close_all(self) -> None:
        """Shut down all cached sessions."""
        async with self._global_lock:
            for name in list(self._sessions):
                bridge = self._sessions.pop(name)
                with contextlib.suppress(BaseException):
                    await bridge.stop()
            self._locks.clear()

    @property
    def active_sessions(self) -> dict[str, StdioMCPBridge]:
        """Currently active sessions (read-only snapshot)."""
        return dict(self._sessions)
