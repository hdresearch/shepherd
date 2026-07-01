"""OpenAI Responses API provider implementation.

This module provides OpenAIProvider, which translates ProviderBinding to
OpenAI Responses API parameters and executes a bounded agent loop with
tool validation, dispatch, and structured output extraction.

Provider-specific settings are configured here, NOT in contexts.
Contexts express abstract needs via trust_level, session_isolation, etc.

Usage:
    provider = OpenAIProvider(
        name="fetcher",
        model="gpt-4o",
    )
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from shepherd_core.effects import (
    AgentMessage,
    AgentThinking,
    PromptSent,
    ToolCallCompleted,
    ToolCallStarted,
)
from shepherd_core.errors import SDKExecutionError
from shepherd_core.provider import Provider, ProviderRuntime
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from shepherd_runtime.registry import register_provider_factory

from shepherd_providers._shared.error_patterns import suggest_fixes
from shepherd_providers.verbose import VerboseConfig, VerboseFormatter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy SDK import
# ---------------------------------------------------------------------------

_async_client_cache: dict[str, Any] = {}


def _get_client(api_key: str | None = None, base_url: str | None = None) -> Any:
    """Lazily import openai and return an AsyncOpenAI client."""
    cache_key = f"{api_key or ''}:{base_url or ''}"
    if cache_key in _async_client_cache:
        return _async_client_cache[cache_key]

    try:
        import openai
    except ImportError as e:
        raise ImportError(
            "The openai package is required for OpenAIProvider. "
            "Install it with: pip install 'shepherd-providers[openai]'"
        ) from e

    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    client = openai.AsyncOpenAI(**kwargs)
    _async_client_cache[cache_key] = client
    return client


# ---------------------------------------------------------------------------
# Built-in tool schemas (Responses API flat format)
# ---------------------------------------------------------------------------

_BASH_TOOL = {
    "type": "function",
    "name": "bash",
    "description": "Execute a shell command. Returns stdout, stderr, and exit code.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
        },
        "required": ["command"],
    },
}

_READ_TOOL = {
    "type": "function",
    "name": "read_file",
    "description": "Read the contents of a file.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read",
            },
        },
        "required": ["path"],
    },
}

_WRITE_TOOL = {
    "type": "function",
    "name": "write_file",
    "description": "Write content to a file. Creates the file if it doesn't exist.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["path", "content"],
    },
}

_SEARCH_FILES_TOOL = {
    "type": "function",
    "name": "search_files",
    "description": "Search for files matching a glob pattern. Returns matching file paths.",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match (e.g., '**/*.py', 'src/**/*.ts')",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: current working directory)",
            },
        },
        "required": ["pattern"],
    },
}

_SEARCH_CONTENT_TOOL = {
    "type": "function",
    "name": "search_content",
    "description": "Search file contents for a regex pattern. Returns matching lines with file paths and line numbers.",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in (default: current working directory)",
            },
        },
        "required": ["pattern"],
    },
}

_EDIT_FILE_TOOL = {
    "type": "function",
    "name": "edit_file",
    "description": "Edit a file by replacing an exact string match. The old_text must appear exactly once in the file.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to edit",
            },
            "old_text": {
                "type": "string",
                "description": "The exact text to find and replace (must be unique in the file)",
            },
            "new_text": {
                "type": "string",
                "description": "The text to replace it with",
            },
        },
        "required": ["path", "old_text", "new_text"],
    },
}

_CAPABILITY_TOOL_MAP: dict[str, list[dict[str, Any]]] = {
    "bash": [_BASH_TOOL],
    "read": [_READ_TOOL, _SEARCH_FILES_TOOL, _SEARCH_CONTENT_TOOL],
    "write": [_WRITE_TOOL, _EDIT_FILE_TOOL],
    "web": [{"type": "web_search_preview"}],
}

_BUILTIN_TOOL_NAMES = frozenset(
    {
        "bash",
        "read_file",
        "write_file",
        "search_files",
        "search_content",
        "edit_file",
    }
)


@dataclass
class StreamResult:
    """Result of consuming a streaming API response.

    Separates function_call items (local tool dispatch) from MCP output
    items (server-side tool execution) so the agent loop can handle each
    transport appropriately.
    """

    func_calls: list[Any] = field(default_factory=list)
    mcp_tool_calls: list[Any] = field(default_factory=list)  # type "mcp_call" in the API
    mcp_list_tools: list[Any] = field(default_factory=list)
    mcp_approval_requests: list[Any] = field(default_factory=list)
    text: str = ""
    thinking: str = ""
    response_id: str = ""


# ---------------------------------------------------------------------------
# MCP server classification and translation helpers
# ---------------------------------------------------------------------------


def _classify_mcp_server(config: dict[str, Any]) -> str:
    """Classify an MCP server config as ``"remote"`` or ``"stdio"``.

    Remote servers have a ``url`` key or an explicit ``type`` of ``"sse"``
    or ``"http"``.  Everything else (including configs with ``command``) is
    classified as ``"stdio"``.

    Returns:
        ``"remote"`` or ``"stdio"``.
    """
    transport_type = config.get("type", "")
    if transport_type in ("sse", "http"):
        return "remote"
    if "url" in config:
        return "remote"
    return "stdio"


def _extract_bearer_token(headers: dict[str, str]) -> str:
    """Extract a Bearer token from an Authorization header, if present.

    Returns:
        The token string (``Bearer `` prefix stripped), or ``""`` if no
        Bearer token is found (either no Authorization header, or a
        non-Bearer scheme like Basic/API-Key).
    """
    if not headers:
        return ""

    for key, value in headers.items():
        if key.lower() == "authorization" and value.lower().startswith("bearer "):
            return value[7:]  # Strip "Bearer " prefix
    return ""


def _translate_mcp_servers(
    mcp_servers: dict[str, dict[str, Any]],
    blocked_tools: frozenset[str],
) -> list[dict[str, Any]]:
    """Translate validated remote MCP server configs to Responses API format.

    Only remote servers should be passed here (stdio servers must be rejected
    during validation before this point).

    Each server is translated to::

        {
            "type": "mcp",
            "server_label": "<server_name>",
            "server_url": "<url>",
            "authorization": "<token>",  # set if Bearer token found
            "headers": {"X-Api-Key": "..."},  # set if non-auth headers present
            "allowed_tools": [...],  # only if filtering needed
            "require_approval": "never",
        }

    Args:
        mcp_servers: Map of server_name -> transport config (remote only).
        blocked_tools: Binding-level blocked tools (prefixed ``mcp__{name}__``).

    Returns:
        List of Responses API MCP tool dicts.
    """
    result: list[dict[str, Any]] = []

    for server_name, config in mcp_servers.items():
        server_url = config.get("url", "")
        headers = config.get("headers", {})

        entry: dict[str, Any] = {
            "type": "mcp",
            "server_label": server_name,
            "server_url": server_url,
            "require_approval": "never",
        }

        # Auth: use "authorization" for Bearer tokens (the canonical path),
        # and pass all headers through the "headers" field for any other
        # auth scheme (API-Key, Basic, custom headers).
        token = _extract_bearer_token(headers)
        if token:
            entry["authorization"] = token
        if headers:
            entry["headers"] = dict(headers)

        # Compute allowed_tools: start from config's allowed_tools,
        # subtract any blocked_tools targeting this server.
        prefix = f"mcp__{server_name}__"
        server_blocked = {t[len(prefix) :] for t in blocked_tools if t.startswith(prefix)}

        config_allowed = config.get("allowed_tools")
        if config_allowed is not None:
            # Config explicitly declares allowed tools -- subtract blocked
            computed = [t for t in config_allowed if t not in server_blocked]
            entry["allowed_tools"] = computed
        # else: no allowed_tools in config.  If server_blocked is non-empty,
        # validate_binding should have already rejected this combination.

        result.append(entry)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_schema_name(name: str | None) -> str:
    """Sanitize a name for use as text.format JSON schema name field."""
    if not name:
        return "task_output"
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]
    return sanitized or "task_output"


def _try_parse_json(text: str) -> dict[str, Any]:
    """Parse JSON from text, with markdown fence stripping fallback."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try stripping markdown fences
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3].rstrip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse structured output JSON, returning empty dict")
    return {}


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


@dataclass
class OpenAIProvider(Provider):
    """OpenAI Responses API provider implementation.

    Executes a bounded agent loop against the OpenAI Responses API with
    pre-execution tool validation, built-in and custom tool dispatch,
    session continuity via previous_response_id, and structured output
    extraction.

    Attributes:
        name: Human-readable name for this provider instance
        model: OpenAI model to use (default: gpt-4o)
        max_turns: Maximum tool-call turns before forced exit (default: 30)
        api_key: Optional API key override (default: OPENAI_API_KEY env var)
        base_url: Optional base URL override for API-compatible endpoints
        verbose: Verbose output configuration
    """

    name: str
    model: str = "gpt-4o"
    max_turns: int = 30
    api_key: str | None = None
    base_url: str | None = None
    verbose: VerboseConfig | None = None

    _id: str = field(default_factory=lambda: uuid4().hex[:8])
    _tool_handlers: dict[str, ToolDefinition] = field(default_factory=dict)
    _formatter: VerboseFormatter | None = field(default=None, init=False)
    _mcp_pool: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialize verbose formatter if verbose output is enabled."""
        if self.verbose and self.verbose.enabled:
            self._formatter = VerboseFormatter(self.verbose)

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    async def __aenter__(self) -> OpenAIProvider:  # noqa: PYI034
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Shut down any MCP stdio sessions held by this provider."""
        if self._mcp_pool is not None:
            await self._mcp_pool.close_all()
            self._mcp_pool = None

    # -----------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------

    @property
    def provider_id(self) -> str:
        return f"provider:openai:{self.model}:{self.name}:{self._id}"

    @property
    def formatter(self) -> VerboseFormatter | None:
        return self._formatter

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="openai",
            supports_streaming=True,
            supports_tools=True,
            supports_structured_output=True,
            supports_session=True,
            supports_fork_session=True,  # previous_response_id supports fork semantics
            supports_images=True,
        )

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def validate_binding(self, binding: ProviderBinding) -> None:
        """Validate binding against OpenAI provider capabilities.

        MCP server validation is transport-specific:
        - **Remote** servers (SSE/HTTP with URL) are accepted.  Auth headers
          are passed through to the API (Bearer via ``authorization`` field,
          all others via ``headers`` field).
        - **Remote + blocked_tools** targeting the server requires
          ``allowed_tools`` in the server config so the framework can compute
          the allowlist without needing the full tool catalog.
        - **stdio** servers are accepted when the ``mcp`` SDK is installed,
          rejected with an install suggestion otherwise.
        """
        from shepherd_core.errors import BindingValidationError

        issues: list[str] = []

        # --- MCP server validation (transport-specific) ---
        for server_name, config in (binding.mcp_servers or {}).items():
            transport = _classify_mcp_server(config)

            if transport == "stdio":
                try:
                    import mcp  # noqa: F401
                except ImportError:
                    issues.append(
                        f"mcp_servers['{server_name}']: stdio MCP servers require the mcp SDK. "
                        "Install with: pip install 'shepherd-providers[openai-mcp]'"
                    )
                continue

            # Remote server -- check blocked_tools vs allowed_tools
            prefix = f"mcp__{server_name}__"
            server_blocked = {t for t in binding.blocked_tools if t.startswith(prefix)}
            if server_blocked and config.get("allowed_tools") is None:
                blocked_names = ", ".join(sorted(t[len(prefix) :] for t in server_blocked))
                issues.append(
                    f"mcp_servers['{server_name}']: blocked_tools targets this server "
                    f"({blocked_names}) but no allowed_tools is configured. The OpenAI "
                    "Responses API executes MCP tools server-side, so blocked_tools cannot "
                    "be enforced without knowing the full tool catalog. Add allowed_tools "
                    "to the server config to enable denylist-to-allowlist translation."
                )

        # --- Trust level ---
        supported_trust = {"sandbox", "restricted", "standard", "elevated"}
        if binding.trust_level not in supported_trust:
            issues.append(f"trust_level='{binding.trust_level}' (supported: {sorted(supported_trust)})")

        if issues:
            raise BindingValidationError(
                context_id=binding.context_id,
                unsatisfied_requirements=issues,
                provider_capabilities=self.capabilities,
            )

    # -----------------------------------------------------------------
    # Binding translation
    # -----------------------------------------------------------------

    def _translate_binding(self, binding: ProviderBinding | None) -> dict[str, Any]:
        """Translate ProviderBinding to Responses API parameters."""
        if binding is None:
            return {"model": self.model}

        # Instructions
        instructions_parts = []
        if binding.context_description:
            instructions_parts.append(f"## Context\n\n{binding.context_description}")
        instructions_parts.extend(binding.system_prompt_additions)
        instructions = "\n\n".join(instructions_parts) if instructions_parts else None

        # Tools
        tools = self._build_tool_schemas(binding)

        # Structured output (text.format)
        text_format = None
        if binding.output_format and binding.output_format.get("type") == "json_schema":
            schema_name = _sanitize_schema_name(
                binding.context_id or (f"task_{binding.context_type}" if binding.context_type else None)
            )
            text_format = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": binding.output_format["schema"],
                }
            }

        # Session
        previous_response_id = None
        if binding.session_id and binding.session_isolation != "isolated":
            previous_response_id = binding.session_id

        return {
            "model": self.model,
            "instructions": instructions,
            "tools": tools,
            "text": text_format,
            "previous_response_id": previous_response_id,
        }

    # -----------------------------------------------------------------
    # Tool schemas
    # -----------------------------------------------------------------

    def _build_tool_schemas(self, binding: ProviderBinding) -> list[dict[str, Any]]:
        """Build tool schemas from binding capabilities, custom tools, and MCP servers."""
        tools: list[dict[str, Any]] = []

        for cap in binding.capabilities:
            if cap in _CAPABILITY_TOOL_MAP:
                tools.extend(_CAPABILITY_TOOL_MAP[cap])

        # Custom tools — always reset handlers to match current binding
        self._tool_handlers = {t.name: t for t in binding.custom_tools} if binding.custom_tools else {}
        for t in binding.custom_tools:
            tools.append(
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.parameters_schema or {"type": "object", "properties": {}},
                }
            )

        # Remote MCP servers — translate to Responses API native MCP tool format
        if binding.mcp_servers:
            remote = {k: v for k, v in binding.mcp_servers.items() if _classify_mcp_server(v) == "remote"}
            if remote:
                tools.extend(_translate_mcp_servers(remote, binding.blocked_tools))

        return tools

    # -----------------------------------------------------------------
    # Built-in tool dispatch
    # -----------------------------------------------------------------

    def _dispatch_builtin_tool(self, name: str, args: dict[str, Any], cwd: str | None) -> tuple[str, bool]:
        """Execute a built-in tool call. Returns (result_string, success)."""
        try:
            if name == "bash":
                return self._exec_bash(args.get("command", ""), cwd), True
            if name == "read_file":
                return self._exec_read(args.get("path", "")), True
            if name == "write_file":
                return self._exec_write(args.get("path", ""), args.get("content", "")), True
            if name == "search_files":
                return self._exec_search_files(args.get("pattern", ""), args.get("path"), cwd), True
            if name == "search_content":
                return self._exec_search_content(args.get("pattern", ""), args.get("path"), cwd), True
            if name == "edit_file":
                return self._exec_edit_file(
                    args.get("path", ""), args.get("old_text", ""), args.get("new_text", "")
                ), True
        except Exception as e:  # noqa: BLE001
            return f"Error: {e}", False
        return f"Unknown built-in tool: {name}", False

    def _exec_bash(self, command: str, cwd: str | None) -> str:
        """Execute a bash command via local subprocess."""
        try:
            r = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=cwd,
                check=False,
            )
            parts = []
            if r.stdout:
                parts.append(f"stdout:\n{r.stdout}")
            if r.stderr:
                parts.append(f"stderr:\n{r.stderr}")
            parts.append(f"exit_code: {r.returncode}")
            return "\n".join(parts)
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 120s"

    def _exec_read(self, path: str) -> str:
        """Read a file."""
        try:
            with open(path) as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            return f"Error reading {path}: {e}"

    def _exec_write(self, path: str, content: str) -> str:
        """Write a file."""
        try:
            with open(path, "w") as f:
                f.write(content)
            return f"Written to {path}"
        except Exception as e:  # noqa: BLE001
            return f"Error writing {path}: {e}"

    def _exec_search_files(self, pattern: str, path: str | None, cwd: str | None) -> str:
        """Search for files matching a glob pattern."""
        from pathlib import Path as _Path

        search_dir = _Path(path or cwd or ".")
        matches = sorted(str(m) for m in search_dir.rglob(pattern))
        if not matches:
            return f"No files matching '{pattern}' in {search_dir}"
        return "\n".join(matches[:100])  # Cap at 100 results

    def _exec_search_content(self, pattern: str, path: str | None, cwd: str | None) -> str:
        """Search file contents for a regex pattern using grep."""
        search_path = path or cwd or "."
        try:
            r = subprocess.run(
                ["grep", "-rn", "--include=*", pattern, search_path],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if r.returncode == 0 and r.stdout:
                lines = r.stdout.strip().split("\n")
                if len(lines) > 100:
                    return "\n".join(lines[:100]) + f"\n... ({len(lines) - 100} more matches)"
                return r.stdout.strip()
            if r.returncode == 1:
                return f"No matches for '{pattern}' in {search_path}"
            return f"Error: {r.stderr}" if r.stderr else f"No matches for '{pattern}'"
        except subprocess.TimeoutExpired:
            return "Error: search timed out after 30s"

    def _exec_edit_file(self, path: str, old_text: str, new_text: str) -> str:
        """Edit a file by replacing exact text."""
        try:
            with open(path) as f:
                content = f.read()
        except Exception as e:  # noqa: BLE001
            return f"Error reading {path}: {e}"

        count = content.count(old_text)
        if count == 0:
            return f"Error: old_text not found in {path}"
        if count > 1:
            return f"Error: old_text appears {count} times in {path} (must be unique)"

        new_content = content.replace(old_text, new_text, 1)
        try:
            with open(path, "w") as f:
                f.write(new_content)
            return f"Edited {path}: replaced 1 occurrence"
        except Exception as e:  # noqa: BLE001
            return f"Error writing {path}: {e}"

    # -----------------------------------------------------------------
    # Streaming
    # -----------------------------------------------------------------

    async def _consume_stream(
        self,
        client: Any,
        kwargs: dict[str, Any],
        runtime: ProviderRuntime,
    ) -> StreamResult:
        """Consume a streaming API response, returning state for the agent loop.

        Calls ``client.responses.create(**kwargs, stream=True)`` and processes
        the SSE event stream in two tiers:

        - **Delta events** are forwarded to VerboseFormatter for real-time
          console output and emitted as partial effects.
        - **Done events** (``output_item.done``, ``response.completed``) are
          used for provider state: tool dispatch items, final text, reasoning,
          and the response ID for session chaining.

        Returns:
            StreamResult with func_calls, MCP items, text, thinking, and response_id.
        """
        stream = await client.responses.create(**kwargs, stream=True)

        func_calls: list[Any] = []
        mcp_tool_calls: list[Any] = []
        mcp_list_tools: list[Any] = []
        mcp_approval_requests: list[Any] = []
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        response_id = ""

        async for event in stream:
            etype = event.type
            task_name = runtime.task_name

            # --- Delta tier: real-time display ---
            if etype == "response.output_text.delta":
                runtime.effects.emit(
                    AgentMessage(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        content=event.delta,
                        is_partial=True,
                    )
                )
                if self._formatter and self.verbose and self.verbose.show_text:
                    self._formatter.on_text_delta(event.delta)

            elif etype == "response.reasoning_summary_text.delta":
                runtime.effects.emit(
                    AgentThinking(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        content=event.delta,
                        is_partial=True,
                    )
                )
                if self._formatter and self.verbose and self.verbose.show_thinking:
                    self._formatter.on_thinking_delta(event.delta)

            # --- Done tier: state for agent loop ---
            elif etype == "response.output_item.done":
                item = event.item
                if hasattr(item, "type"):
                    if item.type == "function_call":
                        func_calls.append(item)
                    elif item.type in ("mcp_call", "mcp_tool_call"):
                        mcp_tool_calls.append(item)
                    elif item.type == "mcp_list_tools":
                        mcp_list_tools.append(item)
                        server_label = getattr(item, "server_label", "unknown")
                        tools = getattr(item, "tools", [])
                        logger.debug(
                            "MCP server '%s' listed %d tools",
                            server_label,
                            len(tools) if isinstance(tools, list) else 0,
                        )
                    elif item.type == "mcp_approval_request":
                        mcp_approval_requests.append(item)
                    elif item.type == "message":
                        for cb in getattr(item, "content", []):
                            if hasattr(cb, "text") and cb.text:
                                text_parts.append(cb.text)
                    elif item.type == "reasoning" and hasattr(item, "summary") and item.summary:
                        for sb in item.summary:
                            if hasattr(sb, "text") and sb.text:
                                thinking_parts.append(sb.text)
                    else:
                        logger.warning("Unrecognized output item type: %s", item.type)

            elif etype == "response.completed":
                response_id = event.response.id

        if not response_id:
            logger.warning("Stream ended without response.completed event — session chaining may fail")

        last_text = "\n".join(text_parts) if text_parts else ""
        thinking = "\n".join(thinking_parts)
        return StreamResult(
            func_calls=func_calls,
            mcp_tool_calls=mcp_tool_calls,
            mcp_list_tools=mcp_list_tools,
            mcp_approval_requests=mcp_approval_requests,
            text=last_text,
            thinking=thinking,
            response_id=response_id,
        )

    # -----------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------

    def to_config(self) -> dict[str, Any]:
        """Serialize provider to config dict for container transfer."""
        config: dict[str, Any] = {
            "provider_type": "openai",
            "name": self.name,
            "model": self.model,
        }
        if self.max_turns != 30:
            config["max_turns"] = self.max_turns
        # Note: api_key is intentionally omitted — the container should
        # pick it up from the OPENAI_API_KEY env var, matching Claude provider.
        if self.base_url is not None:
            config["base_url"] = self.base_url
        return config

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OpenAIProvider:
        """Reconstruct provider from config dict."""
        return cls(
            name=config.get("name", "container"),
            model=config.get("model", "gpt-4o"),
            max_turns=config.get("max_turns", 30),
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            verbose=None,
        )

    # -----------------------------------------------------------------
    # Agent loop
    # -----------------------------------------------------------------

    async def _start_stdio_mcp_servers(
        self,
        binding: ProviderBinding,
    ) -> tuple[ProviderBinding, bool]:
        """Start stdio MCP servers and return an augmented binding.

        Detects stdio entries in ``binding.mcp_servers``, starts each via the
        session pool, discovers tools, and creates ``ToolDefinition`` objects
        with async handlers that route to ``bridge.call_tool()``.  Returns a
        new binding with these definitions appended to ``custom_tools``.

        Returns:
            (augmented_binding, has_stdio) — the binding to use for the rest
            of ``execute_sdk()``, and whether any stdio servers were started.
        """
        if not binding.mcp_servers:
            return binding, False

        stdio_servers = {name: cfg for name, cfg in binding.mcp_servers.items() if _classify_mcp_server(cfg) == "stdio"}
        if not stdio_servers:
            return binding, False

        # Lazy-init the session pool on the provider instance.
        if self._mcp_pool is None:
            from shepherd_providers.openai._mcp_stdio_bridge import StdioSessionPool

            self._mcp_pool = StdioSessionPool()

        from shepherd_providers.openai._mcp_stdio_bridge import (
            _normalise_schema,
        )

        mcp_tool_defs: list[ToolDefinition] = []
        for server_name, config in stdio_servers.items():
            command = config.get("command", "")
            args = config.get("args", [])
            env = config.get("env")

            bridge = await self._mcp_pool.get(server_name, command, args, env)

            for tool in bridge.tools:
                prefixed_name = f"mcp__{server_name}__{tool.name}"
                raw_schema = tool.inputSchema if tool.inputSchema is not None else {"type": "object", "properties": {}}
                params = _normalise_schema(raw_schema)

                # Capture bridge and tool.name in closure defaults
                async def _mcp_handler(
                    handler_args: dict[str, Any],
                    _context_id: str | None = None,
                    *,
                    _bridge: Any = bridge,
                    _tool_name: str = tool.name,
                ) -> str:
                    result = await _bridge.call_tool(_tool_name, handler_args)
                    # MCP CallToolResult has .content (list of content blocks)
                    texts = [block.text for block in result.content if hasattr(block, "text")]
                    return "\n".join(texts) if texts else str(result.content)

                # ToolDefinition needs a sync handler (required field) and
                # an async_handler (preferred by _invoke_tool_handler).
                mcp_tool_defs.append(
                    ToolDefinition(
                        name=prefixed_name,
                        description=tool.description or f"MCP tool {tool.name} from {server_name}",
                        parameters_schema=params,
                        handler=lambda _args: "error: sync fallback should not be called",
                        async_handler=_mcp_handler,
                    )
                )

        augmented = binding.model_copy(update={"custom_tools": binding.custom_tools + tuple(mcp_tool_defs)})
        return augmented, True

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        hooks: dict | None = None,
    ) -> ExecutionResult:
        """Execute via OpenAI Responses API with bounded agent loop."""
        import openai as openai_mod

        client = _get_client(api_key=self.api_key, base_url=self.base_url)
        task_name = runtime.task_name

        # Start stdio MCP servers and augment the binding with their tools
        if binding is not None:
            binding, _has_stdio = await self._start_stdio_mcp_servers(binding)
        translated = self._translate_binding(binding)
        validator = self._build_composite_validator(
            binding,
            runtime,
            binding_name=binding.context_id if binding else None,
        )

        tools = translated.get("tools") or []
        text_format = translated.get("text")
        instructions = translated.get("instructions")
        previous_response_id = translated.get("previous_response_id")
        cwd = binding.cwd if binding else None
        context_id = binding.context_id if binding else ""

        input_items: str | list[dict[str, Any]] = prompt
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        collected_thinking: list[str] = []
        last_text = ""
        last_tool_call: ToolCall | None = None

        # Emit PromptSent effect + verbose
        runtime.effects.emit(
            PromptSent(
                task_name=task_name,
                provider_id=self.provider_id,
                system_prompt=instructions or "",
                user_prompt=prompt,
            )
        )
        if self._formatter and self.verbose and self.verbose.show_prompts:
            self._formatter.on_prompt_sent(instructions or "", prompt)

        original_instructions = instructions
        for _turn in range(self.max_turns):
            # Build request kwargs — re-pass tools, text, truncation every turn
            kwargs: dict[str, Any] = {"model": self.model, "input": input_items}
            if tools:
                kwargs["tools"] = tools
            if text_format:
                kwargs["text"] = text_format
            if instructions:
                kwargs["instructions"] = instructions
            kwargs["truncation"] = "auto"
            if previous_response_id:
                kwargs["previous_response_id"] = previous_response_id

            try:
                sr = await self._consume_stream(client, kwargs, runtime)
            except openai_mod.BadRequestError as e:
                # Handle expired previous_response_id — cold restart
                body = getattr(e, "body", None) or {}
                error_body = body.get("error", body) if isinstance(body, dict) else {}
                code = error_body.get("code", "") if isinstance(error_body, dict) else ""
                if code == "previous_response_not_found" and previous_response_id:
                    logger.warning("Session expired (previous_response_id not found), restarting fresh")
                    previous_response_id = None
                    instructions = original_instructions
                    kwargs.pop("previous_response_id", None)
                    if instructions:
                        kwargs["instructions"] = instructions
                    try:
                        sr = await self._consume_stream(client, kwargs, runtime)
                    except openai_mod.APIError as retry_err:
                        raise SDKExecutionError(
                            f"OpenAI API error on session restart: {retry_err}",
                            original_error=retry_err,
                            prompt_preview=prompt,
                            sdk_options=kwargs,
                            last_tool_name=last_tool_call.name if last_tool_call else None,
                            last_tool_params=last_tool_call.params if last_tool_call else None,
                            suggestions=suggest_fixes(retry_err, provider="openai", last_tool=last_tool_call),
                        ) from retry_err
                else:
                    raise SDKExecutionError(
                        f"OpenAI API error: {e}",
                        original_error=e,
                        prompt_preview=prompt,
                        session_id=previous_response_id,
                        sdk_options=kwargs,
                        last_tool_name=last_tool_call.name if last_tool_call else None,
                        last_tool_params=last_tool_call.params if last_tool_call else None,
                        suggestions=suggest_fixes(
                            e, provider="openai", session_id=previous_response_id, last_tool=last_tool_call
                        ),
                    ) from e
            except openai_mod.APIError as e:
                raise SDKExecutionError(
                    f"OpenAI API error: {e}",
                    original_error=e,
                    prompt_preview=prompt,
                    session_id=previous_response_id,
                    sdk_options=kwargs,
                    last_tool_name=last_tool_call.name if last_tool_call else None,
                    last_tool_params=last_tool_call.params if last_tool_call else None,
                    suggestions=suggest_fixes(
                        e, provider="openai", session_id=previous_response_id, last_tool=last_tool_call
                    ),
                ) from e

            previous_response_id = sr.response_id
            # instructions only needed on first turn when chaining via previous_response_id
            instructions = None

            # Process text and thinking from the stream
            if sr.text:
                last_text = sr.text
                runtime.effects.emit(
                    AgentMessage(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        content=sr.text,
                    )
                )
                if self._formatter:
                    self._formatter.on_text_complete(sr.text)

            if sr.thinking:
                collected_thinking.append(sr.thinking)
                runtime.effects.emit(
                    AgentThinking(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        content=sr.thinking,
                        is_partial=False,
                    )
                )
                if self._formatter and self.verbose and self.verbose.show_thinking:
                    self._formatter.on_thinking_complete(sr.thinking)

            # --- MCP item processing ---

            # Log mcp_list_tools discoveries
            for mcp_lt in sr.mcp_list_tools:
                server_label = getattr(mcp_lt, "server_label", "unknown")
                mcp_discovered = getattr(mcp_lt, "tools", [])
                tool_count = len(mcp_discovered) if isinstance(mcp_discovered, list) else 0
                logger.info("MCP server '%s' discovered %d tools", server_label, tool_count)

            # Process mcp_tool_calls — API handled the round trip, just record
            for mcp_tc in sr.mcp_tool_calls:
                server_label = getattr(mcp_tc, "server_label", "unknown")
                tool_name = getattr(mcp_tc, "name", "unknown")
                normalized_name = f"mcp__{server_label}__{tool_name}"
                call_id = getattr(mcp_tc, "id", uuid4().hex[:8])
                mcp_args = getattr(mcp_tc, "arguments", "{}")
                if isinstance(mcp_args, str):
                    try:
                        mcp_args_dict = json.loads(mcp_args)
                    except json.JSONDecodeError:
                        mcp_args_dict = {}
                else:
                    mcp_args_dict = mcp_args if isinstance(mcp_args, dict) else {}
                mcp_output = getattr(mcp_tc, "output", "")
                mcp_error = getattr(mcp_tc, "error", None)
                mcp_success = mcp_error is None

                runtime.effects.emit(
                    ToolCallStarted(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        tool_call_id=call_id,
                        tool_name=normalized_name,
                        params=mcp_args_dict,
                    )
                )
                runtime.effects.emit(
                    ToolCallCompleted(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        tool_call_id=call_id,
                        tool_name=normalized_name,
                        success=mcp_success,
                        output_preview=str(mcp_error or mcp_output)[:100],
                    )
                )

                tool_call = ToolCall(id=call_id, name=normalized_name, params=mcp_args_dict)
                all_tool_calls.append(tool_call)
                last_tool_call = tool_call
                all_tool_results.append(
                    ToolResult(
                        tool_call_id=call_id,
                        success=mcp_success,
                        output=str(mcp_error or mcp_output or ""),
                    )
                )
                # NOTE: No function_call_output added to input_items — API handled the round trip

            # Auto-approve mcp_approval_requests (defensive handler)
            # The API pauses and waits for approval — we must respond or it hangs.
            approval_responses: list[dict[str, Any]] = []
            for mcp_ar in sr.mcp_approval_requests:
                approval_id = getattr(mcp_ar, "id", "unknown")
                tool_name = getattr(mcp_ar, "name", "unknown")
                server_label = getattr(mcp_ar, "server_label", "unknown")
                logger.warning(
                    "Auto-approving MCP approval request %s for tool '%s' on server '%s' (defensive handler)",
                    approval_id,
                    tool_name,
                    server_label,
                )
                approval_responses.append(
                    {
                        "type": "mcp_approval_response",
                        "approve": True,
                        "approval_request_id": approval_id,
                    }
                )

            # Determine loop continuation:
            # Break if no func_calls AND no mcp_approval_requests
            # (mcp_tool_calls alone don't require continuation — API handled the round trip)
            if not sr.func_calls and not sr.mcp_approval_requests:
                break

            # Process each function call: validate, dispatch, collect results
            input_items: list[dict[str, Any]] = list(approval_responses)
            for fc in sr.func_calls:
                try:
                    args = json.loads(fc.arguments)
                except json.JSONDecodeError:
                    args = {}

                tool_call = ToolCall(id=fc.call_id, name=fc.name, params=args)

                # Validate before dispatch
                validation = validator(tool_call)
                if not validation.allowed:
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": fc.call_id,
                            "output": f"Error: Tool '{fc.name}' rejected: {validation.rejection_reason}",
                        }
                    )
                    continue

                runtime.effects.emit(
                    ToolCallStarted(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        tool_call_id=fc.call_id,
                        tool_name=fc.name,
                        params=args,
                    )
                )
                if self._formatter and self.verbose and self.verbose.show_tool_calls:
                    self._formatter.on_tool_call_started(fc.name, args)

                # Dispatch: built-in or custom
                if fc.name in _BUILTIN_TOOL_NAMES:
                    result_str, success = self._dispatch_builtin_tool(fc.name, args, cwd)
                elif fc.name in self._tool_handlers:
                    try:
                        result = await self._invoke_tool_handler(
                            self._tool_handlers[fc.name],
                            tool_call,
                            context_id,
                        )
                        if isinstance(result, (dict, list)):
                            result_str = json.dumps(result, indent=2)
                        else:
                            result_str = str(result)
                        success = True
                    except Exception as e:  # noqa: BLE001
                        result_str = f"Error: {e}"
                        success = False
                else:
                    result_str = f"Unknown tool: {fc.name}"
                    success = False

                runtime.effects.emit(
                    ToolCallCompleted(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        tool_call_id=fc.call_id,
                        tool_name=fc.name,
                        success=success,
                        output_preview=result_str[:100],
                    )
                )
                if self._formatter and self.verbose and self.verbose.show_tool_results:
                    self._formatter.on_tool_call_completed(fc.name, result_str, not success)

                all_tool_calls.append(tool_call)
                last_tool_call = tool_call
                all_tool_results.append(
                    ToolResult(
                        tool_call_id=fc.call_id,
                        success=success,
                        output=result_str,
                    )
                )

                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": fc.call_id,
                        "output": result_str,
                    }
                )
        else:
            # max_turns exceeded
            return ExecutionResult(
                success=False,
                output_text=last_text,
                tool_calls=tuple(all_tool_calls),
                tool_results=tuple(all_tool_results),
                session_id=previous_response_id,
                metadata={
                    "model": self.model,
                    "turns": self.max_turns,
                    "error_type": "max_turns",
                    "thinking_length": len("\n".join(collected_thinking)),
                },
            )

        # Parse structured output
        structured: dict[str, Any] = {}
        if binding and binding.output_format and last_text:
            structured = _try_parse_json(last_text)

        return ExecutionResult(
            success=True,
            output_text=last_text,
            structured_output=structured,
            tool_calls=tuple(all_tool_calls),
            tool_results=tuple(all_tool_results),
            session_id=previous_response_id,
            metadata={
                "model": self.model,
                "turns": _turn + 1,
                "thinking_length": len("\n".join(collected_thinking)),
            },
        )


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------


def _register_provider_factory() -> None:
    """Register OpenAIProvider factory with the provider registry."""
    try:
        register_provider_factory("openai", OpenAIProvider.from_config)
    except ImportError:
        logger.debug("Skipping container provider factory registration (device module unavailable)")


_register_provider_factory()


__all__ = [
    "OpenAIProvider",
    "StreamResult",
    "_classify_mcp_server",
    "_extract_bearer_token",
    "_translate_mcp_servers",
]
