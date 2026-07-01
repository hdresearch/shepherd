"""OpenCode provider implementation.

Delegates both the agent loop and tool dispatch to an OpenCode server,
using the opencode-ai SDK for API calls. The server runs inside whatever
process calls execute_sdk() — host for local, container for container.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from shepherd_core.effects import (
    AgentMessage,
    AgentThinking,
    ExecutionFailed,
    LLMResponseReceived,
    PromptSent,
    ToolCallBatch,
    ToolCallInfo,
)
from shepherd_core.errors import BindingValidationError
from shepherd_core.provider import Provider, ProviderRuntime
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    capability_for_tool,
)

from shepherd_providers.verbose import VerboseConfig, VerboseFormatter

logger = logging.getLogger(__name__)

# OpenCode tool names and their required capabilities.
# Read tools (read, grep, glob, list) are ungated by design.
_OPENCODE_WRITE_TOOLS = frozenset({"write", "edit", "patch"})
_OPENCODE_BASH_TOOLS = frozenset({"bash"})
_OPENCODE_WEB_TOOLS = frozenset({"webfetch", "websearch"})
_OPENCODE_TASK_TOOLS = frozenset({"todowrite"})
_OPENCODE_READ_TOOLS = frozenset({"read", "grep", "glob", "list"})

_ALL_OPENCODE_TOOLS = (
    _OPENCODE_WRITE_TOOLS | _OPENCODE_BASH_TOOLS | _OPENCODE_WEB_TOOLS | _OPENCODE_TASK_TOOLS | _OPENCODE_READ_TOOLS
)


def _try_parse_json(text: str) -> dict[str, Any]:
    """Parse JSON from text, stripping markdown fences if needed."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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
    logger.warning("Failed to parse structured output JSON from OpenCode response")
    return {}


def _build_structured_output_instruction(output_format: dict[str, Any]) -> str:
    """Build a system prompt instruction requesting JSON output for a schema."""
    schema = output_format.get("schema", output_format)
    schema_json = json.dumps(schema, indent=2)
    return (
        "IMPORTANT: After completing all required tool operations, your FINAL "
        "text response MUST be a valid JSON object (no markdown fences, no "
        "surrounding text) conforming to this JSON schema:\n\n"
        f"{schema_json}\n\n"
        "You may use tools and provide explanations during your work, but your "
        "very last message must contain ONLY the raw JSON object with your results."
    )


def _last_text_from_parts(parts: list[Any]) -> str:
    """Extract the last text part from a list of message parts."""
    for part in reversed(parts or []):
        if isinstance(part, dict):
            if part.get("type") == "text":
                return part.get("text", "")
        elif getattr(part, "type", None) == "text":
            return getattr(part, "text", "") or ""
    return ""


def _normalize_message_metadata(message: Any) -> dict[str, Any]:
    """Normalize an AssistantMessage (or dict-like response) into a flat metadata dict.

    The OpenCode server returns metadata nested under an ``info`` key::

        {"info": {"tokens": {...}, "cost": 0.005, "modelID": "...", ...}, "parts": [...]}

    The opencode-ai SDK's ``AssistantMessage`` model expects these fields at the
    top level, but ``model_construct()`` (used by the SDK for loose coercion) leaves
    them all as ``None`` and stashes the raw ``info`` dict as an extra field.

    In the SSE streaming path, ``message.updated`` events carry the metadata directly
    in ``event.properties.info`` — a dict or dict-like object, not an ``AssistantMessage``.

    This function resolves the metadata from whichever shape it arrives in, returning
    a plain dict with canonical keys: ``tokens``, ``cost``, ``time``, ``modelID``.
    """
    if message is None:
        return {}

    # Strategy: try the ``info`` dict first (covers both the SDK's extra-field case
    # and the raw SSE event case), then fall back to top-level attributes (in case a
    # future SDK version fixes the schema mismatch).
    info: dict[str, Any] | None = None

    # Case 1: message has an ``info`` attribute that is a populated dict.
    raw_info = getattr(message, "info", None)
    if isinstance(raw_info, dict) and raw_info:
        info = raw_info
    # Case 2: message is itself a dict (e.g. SSE event properties.info).
    elif isinstance(message, dict):
        info = message

    if info is not None:
        return info

    # Case 3: typed AssistantMessage with populated fields (future SDK fix).
    # Read top-level attributes and build the dict ourselves.
    result: dict[str, Any] = {}
    for attr, key in [
        ("tokens", "tokens"),
        ("cost", "cost"),
        ("time", "time"),
        ("api_model_id", "modelID"),
    ]:
        val = getattr(message, attr, None)
        if val is not None:
            result[key] = val
    return result


def _get_nested(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Retrieve a value from a nested dict, returning *default* if any key is missing."""
    current: Any = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
        if current is None:
            return default
    return current


def _extract_llm_response_effect(
    message: Any,
    *,
    task_name: str | None,
    provider_id: str,
    model: str,
    wall_duration_ms: float,
    is_error: bool = False,
) -> LLMResponseReceived:
    """Build an LLMResponseReceived effect from an AssistantMessage or metadata dict.

    Normalizes the response first (see ``_normalize_message_metadata``), then reads
    from the canonical dict shape::

        {
            "tokens": {"input": N, "output": N, "reasoning": N, "cache": {"read": N, "write": N}},
            "cost": float,
            "time": {"created": epoch_ms, "completed": epoch_ms},
            "modelID": str,
        }

    Any missing field is silently defaulted to zero/None so that a partial response
    still produces a usable effect.
    """
    meta = _normalize_message_metadata(message)

    input_tokens = int(_get_nested(meta, "tokens", "input", default=0) or 0)
    output_tokens = int(_get_nested(meta, "tokens", "output", default=0) or 0)
    reasoning_tokens = int(_get_nested(meta, "tokens", "reasoning", default=0) or 0)

    cache_read = int(_get_nested(meta, "tokens", "cache", "read", default=0) or 0)
    cache_write = int(_get_nested(meta, "tokens", "cache", "write", default=0) or 0)

    raw_cost = meta.get("cost")
    cost_usd = float(raw_cost) if raw_cost is not None else None

    # API-side duration from time.created → time.completed (epoch ms from server).
    api_duration_ms = 0.0
    time_info = meta.get("time")
    if isinstance(time_info, dict):
        created = time_info.get("created")
        completed = time_info.get("completed")
    else:
        created = getattr(time_info, "created", None)
        completed = getattr(time_info, "completed", None)
    if created is not None and completed is not None:
        delta = float(completed) - float(created)
        # The server sends epoch milliseconds (e.g. 1774852485340); detect by
        # checking if *created* exceeds 1e10 (any epoch-ms timestamp after 1970
        # is > 1e12; epoch-seconds won't exceed 1e10 until the year 2286).
        if float(created) > 1e10:
            api_duration_ms = delta  # already milliseconds
        else:
            api_duration_ms = delta * 1000.0  # seconds → milliseconds

    raw_model_id = meta.get("modelID")
    model_id = str(raw_model_id) if raw_model_id and isinstance(raw_model_id, str) else model

    return LLMResponseReceived(
        task_name=task_name,
        provider_id=provider_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens + reasoning_tokens,
        total_tokens=input_tokens + output_tokens + reasoning_tokens,
        cost_usd=cost_usd,
        duration_ms=wall_duration_ms,
        duration_api_ms=api_duration_ms,
        num_turns=1,  # OpenCode manages multi-turn internally; we see one round-trip
        model_id=model_id,
        is_error=is_error,
        usage_details={
            "reasoning_tokens": reasoning_tokens,
        }
        if reasoning_tokens
        else None,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
    )


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string with ellipsis."""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


@dataclass
class OpenCodeProvider(Provider):
    """OpenCode provider — delegates to a local opencode serve process.

    The provider starts an OpenCode server on first use (via the global
    OpenCodeServerRegistry) and communicates with it over HTTP. The server
    manages the agent loop and tool dispatch; the provider translates
    bindings, emits effects, and returns results.

    Attributes:
        name: Human-readable name for this provider instance.
        model: "provider_id/model_id" format (e.g., "anthropic/claude-sonnet-4-20250514").
        max_turns: Maximum agent loop turns (default 30).
        server_port: Fixed port override, or None for OS auto-assignment.
        verbose: Verbose output configuration.
        container_env: Env var names to forward to containers.
    """

    name: str = "opencode"
    model: str = "anthropic/claude-sonnet-4-20250514"
    max_turns: int = 30
    server_port: int | None = None
    verbose: VerboseConfig | None = None
    container_env: tuple[str, ...] = ()
    streaming: bool = True  # Use SSE streaming for real-time effects

    _id: str = field(default_factory=lambda: uuid4().hex[:8])
    _formatter: VerboseFormatter | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.verbose and self.verbose.enabled:
            self._formatter = VerboseFormatter(self.verbose)

    # -----------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------

    @property
    def provider_id(self) -> str:
        return f"provider:opencode:{self.model}:{self.name}:{self._id}"

    @property
    def formatter(self) -> VerboseFormatter | None:
        return self._formatter

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="opencode",
            supports_streaming=True,
            supports_tools=True,
            supports_structured_output=True,
            supports_session=True,
            supports_fork_session=True,
            supports_images=True,
            available_tools=_ALL_OPENCODE_TOOLS,
        )

    @property
    def _provider_model_ids(self) -> tuple[str, str]:
        """Split model string into (provider_id, model_id)."""
        parts = self.model.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "", self.model

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    _CAPS_REQUIRING_RUNTIME_VALIDATION = frozenset({"bash"})

    def validate_binding(self, binding: ProviderBinding) -> None:
        """Validate that this provider can satisfy the binding.

        Raises BindingValidationError if:
        - validate_tool does uncoverable checks (bash enabled)
        - trust_level is invalid

        Accepts (with a warning) validate_tool on read-only and write-only
        bindings where the tool map + cwd-level scoping cover the main
        restrictions.
        """
        issues: list[str] = []

        if binding.validate_tool is not None:
            uncoverable_caps = self._CAPS_REQUIRING_RUNTIME_VALIDATION & binding.capabilities
            if uncoverable_caps:
                issues.append(
                    "validate_tool callbacks cannot be honored — OpenCode executes "
                    "tools server-side with no interception point.  This binding "
                    f"enables capabilities {sorted(uncoverable_caps)} which may "
                    "require runtime tool validation that the pre-execution tool "
                    "map cannot replicate."
                )
            else:
                logger.warning(
                    "OpenCode provider accepting validate_tool on binding "
                    "'%s' — tool-map gating + cwd scoping cover capability "
                    "restrictions, but fine-grained checks (path boundaries) "
                    "are downgraded to cwd-level scoping.",
                    binding.context_id,
                )

        valid_trust = {"sandbox", "restricted", "standard", "elevated"}
        if binding.trust_level not in valid_trust:
            issues.append(f"trust_level='{binding.trust_level}' (supported: {sorted(valid_trust)})")

        if issues:
            raise BindingValidationError(
                context_id=binding.context_id,
                unsatisfied_requirements=issues,
                provider_capabilities=self.capabilities,
            )

    # -----------------------------------------------------------------
    # Binding Translation
    # -----------------------------------------------------------------

    def _compute_tool_map(self, binding: ProviderBinding | None) -> dict[str, bool]:
        """Compute the OpenCode tools parameter from binding capabilities.

        Returns a dict mapping tool names to enabled/disabled. Tools not
        in the binding's capabilities or in blocked_tools are disabled.
        """
        if binding is None:
            return dict.fromkeys(_ALL_OPENCODE_TOOLS, True)

        tool_map: dict[str, bool] = {}
        for tool_name in _ALL_OPENCODE_TOOLS:
            # Check if tool is blocked
            if tool_name in binding.blocked_tools:
                tool_map[tool_name] = False
                continue

            # Check capability requirement
            required_cap = capability_for_tool(tool_name)
            if required_cap and required_cap not in binding.capabilities:
                tool_map[tool_name] = False
                continue

            tool_map[tool_name] = True

        return tool_map

    def _translate_binding(self, binding: ProviderBinding | None) -> dict[str, Any]:
        """Convert a ProviderBinding to OpenCode chat() parameters."""
        if binding is None:
            provider_id, model_id = self._provider_model_ids
            return {"provider_id": provider_id, "model_id": model_id}

        # Assemble system prompt
        parts: list[str] = []
        if binding.context_description:
            parts.append(binding.context_description)
        parts.extend(binding.system_prompt_additions)

        # Structured output fallback: inject JSON schema instruction into
        # the system prompt since OpenCode has no response_format parameter.
        if binding.output_format:
            parts.append(_build_structured_output_instruction(binding.output_format))

        system = "\n\n".join(parts) if parts else None

        provider_id, model_id = self._provider_model_ids
        tool_map = self._compute_tool_map(binding)

        result: dict[str, Any] = {
            "provider_id": provider_id,
            "model_id": model_id,
            "tools": tool_map,
        }
        if system:
            result["system"] = system

        return result

    # -----------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------

    def to_config(self) -> dict[str, Any]:
        """Serialize provider to config dict for container transfer."""
        config: dict[str, Any] = {
            "provider_type": "opencode",
            "name": self.name,
            "model": self.model,
        }
        if self.max_turns != 30:
            config["max_turns"] = self.max_turns
        if self.server_port is not None:
            config["server_port"] = self.server_port
        if self.container_env:
            config["container_env"] = list(self.container_env)
        if not self.streaming:
            config["streaming"] = False
        return config

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OpenCodeProvider:
        """Reconstruct provider from config dict."""
        container_env = config.get("container_env", ())
        if isinstance(container_env, list):
            container_env = tuple(container_env)
        return cls(
            name=config.get("name", "opencode"),
            model=config.get("model", "anthropic/claude-sonnet-4-20250514"),
            max_turns=config.get("max_turns", 30),
            server_port=config.get("server_port"),
            container_env=container_env,
            streaming=config.get("streaming", True),
            verbose=None,  # Verbose disabled in container (no console)
        )

    # -----------------------------------------------------------------
    # Session Management
    # -----------------------------------------------------------------

    async def _resolve_session(self, client: Any, binding: ProviderBinding | None) -> str:
        """Resolve the session ID based on binding isolation mode.

        | binding state                          | action                    |
        |----------------------------------------|---------------------------|
        | session_id=None or isolation="isolated" | create new session        |
        | session_id="X", isolation="shared"     | resume existing session X |
        | session_id="X", isolation="forked"     | fork session X            |

        Fork failures (e.g., session doesn't exist on a fresh container
        server) fall back to creating a new session with a warning.
        """
        session_id = binding.session_id if binding else None
        isolation = binding.session_isolation if binding else "shared"

        # Isolated always creates a new session
        if isolation == "isolated" or session_id is None:
            session = await client.create_session()
            return session.id if hasattr(session, "id") else str(session)

        # Forked: try to fork, fall back to new session
        if isolation == "forked":
            try:
                fork_result = await client.fork_session(session_id)
                new_id = fork_result.get("id") or fork_result.get("session_id")
                if new_id:
                    return str(new_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Fork of session {session_id} failed ({e}), creating new session")
            session = await client.create_session()
            return session.id if hasattr(session, "id") else str(session)

        # Shared: resume existing session
        return session_id

    # -----------------------------------------------------------------
    # MCP Server Management
    # -----------------------------------------------------------------

    async def _register_mcp_servers(
        self,
        client: Any,
        binding: ProviderBinding | None,
        execution_id: str,
    ) -> list[str]:
        """Register MCP servers for custom tools and external servers.

        Args:
            client: OpenCodeClient to register with.
            binding: Provider binding with custom_tools and mcp_servers.
            execution_id: Unique ID for this execution (for name isolation).

        Returns:
            List of registered server names (for cleanup).
        """
        if binding is None:
            return []

        registered: list[str] = []

        # Register external MCP servers from binding.mcp_servers
        for name, config in (binding.mcp_servers or {}).items():
            # Use execution-scoped name for isolation
            scoped_name = f"{name}-{execution_id}"
            try:
                await client.register_mcp_server(scoped_name, config)
                registered.append(scoped_name)
                logger.debug(f"Registered external MCP server: {scoped_name}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to register MCP server {scoped_name}: {e}")

        # Custom tools require wrapping in an MCP server process.
        # This is deferred — custom_tools with Python handlers need a local
        # MCP stdio server process, which is not yet implemented. The
        # validate_binding() check already rejects bindings with validate_tool,
        # but custom_tools without validators could still arrive here.
        if binding.custom_tools:
            logger.warning(
                f"Ignoring {len(binding.custom_tools)} custom_tools — "
                "MCP wrapping for ToolDefinition handlers is not yet implemented"
            )

        return registered

    async def _cleanup_mcp_servers(
        self,
        client: Any,
        server_names: list[str],
    ) -> None:
        """Remove registered MCP servers after execution."""
        for name in server_names:
            try:
                await client.remove_mcp_server(name)
                logger.debug(f"Removed MCP server: {name}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to remove MCP server {name}: {e}")

    # -----------------------------------------------------------------
    # Execute SDK
    # -----------------------------------------------------------------

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        hooks: dict | None = None,
    ) -> ExecutionResult:
        """Execute via OpenCode server and return result.

        1. Get/start a server from the registry
        2. Create or resume a session
        3. Translate binding to chat() parameters
        4. Call session.chat() (blocks until complete)
        5. Emit effects from message history (ToolCallBatch)
        6. Return ExecutionResult
        """
        from shepherd_providers.opencode._client import OpenCodeClient
        from shepherd_providers.opencode._server import OpenCodeServerRegistry

        task_name = runtime.task_name

        # Determine working directory
        cwd = "."
        if binding and binding.cwd:
            cwd = binding.cwd

        # Get or start server (registry writes model config to opencode.json)
        registry = OpenCodeServerRegistry.get_instance()
        try:
            base_url = await registry.get_or_start(cwd, self.server_port, model=self.model)
        except Exception as e:  # noqa: BLE001
            runtime.effects.emit(
                ExecutionFailed(
                    task_name=task_name,
                    provider_id=self.provider_id,
                    error_type="server_start",
                    error_message=str(e),
                    recoverable=False,
                )
            )
            return ExecutionResult(
                success=False,
                output_text=f"Failed to start OpenCode server: {e}",
                metadata={"error_type": "server_start", "error": str(e)},
            )

        client = OpenCodeClient(base_url)
        execution_id = uuid4().hex[:8]
        mcp_servers: list[str] = []
        try:
            # Register MCP servers for custom tools and external servers
            mcp_servers = await self._register_mcp_servers(client, binding, execution_id)

            if self.streaming:
                return await self._execute_streaming(client, prompt, binding, runtime, task_name)
            return await self._execute_with_client(client, prompt, binding, runtime, task_name)
        finally:
            # Clean up MCP servers before closing client (best-effort)
            if mcp_servers:
                try:
                    await self._cleanup_mcp_servers(client, mcp_servers)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"MCP cleanup failed: {e}")
            await client.close()

    async def _execute_with_client(
        self,
        client: Any,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        task_name: str | None,
    ) -> ExecutionResult:
        """Execute with an already-connected client."""
        params = self._translate_binding(binding)

        # Session management
        session_id = await self._resolve_session(client, binding)

        # Emit PromptSent
        system_prompt = params.get("system", "")
        runtime.effects.emit(
            PromptSent(
                task_name=task_name,
                provider_id=self.provider_id,
                system_prompt=system_prompt,
                user_prompt=prompt,
                model_id=self.model,
            )
        )
        if self._formatter:
            self._formatter.on_prompt_sent(system_prompt, prompt)

        # Call chat (blocking) — returns an AssistantMessage with parts
        import time as _time

        chat_start = _time.perf_counter()
        try:
            chat_result = await client.send_message(
                session_id=session_id,
                message=prompt,
                provider_id=params["provider_id"],
                model_id=params["model_id"],
                system=params.get("system"),
                tools=params.get("tools"),
            )
        except Exception as e:  # noqa: BLE001
            wall_ms = (_time.perf_counter() - chat_start) * 1000.0
            runtime.effects.emit(
                ExecutionFailed(
                    task_name=task_name,
                    provider_id=self.provider_id,
                    error_type="chat_error",
                    error_message=str(e),
                    recoverable=False,
                )
            )
            runtime.effects.emit(
                _extract_llm_response_effect(
                    None,
                    task_name=task_name,
                    provider_id=self.provider_id,
                    model=self.model,
                    wall_duration_ms=wall_ms,
                    is_error=True,
                )
            )
            return ExecutionResult(
                success=False,
                output_text=f"OpenCode chat failed: {e}",
                session_id=session_id,
                metadata={"error_type": "chat_error", "error": str(e)},
            )
        wall_ms = (_time.perf_counter() - chat_start) * 1000.0

        # Extract from the assistant's response directly (not from get_messages,
        # which returns all messages including user messages with ambiguous roles)
        output_text, thinking, tool_call_infos = self._extract_from_parts(getattr(chat_result, "parts", []))

        # Emit ToolCallBatch if any tools were called
        if tool_call_infos:
            batch_id = f"opencode-batch-{uuid4().hex[:8]}"
            runtime.effects.emit(
                ToolCallBatch(
                    task_name=task_name,
                    provider_id=self.provider_id,
                    batch_id=batch_id,
                    tool_calls=tuple(tool_call_infos),
                )
            )
            if self._formatter:
                for tc in tool_call_infos:
                    self._formatter.on_tool_call_started(tc.tool_name, tc.input_preview)
                    self._formatter.on_tool_call_completed(tc.tool_name, tc.output_preview, False)

        # Emit AgentThinking
        if thinking:
            runtime.effects.emit(
                AgentThinking(
                    task_name=task_name,
                    provider_id=self.provider_id,
                    content=thinking,
                    is_partial=False,
                )
            )
            if self._formatter:
                self._formatter.on_thinking_complete(thinking)

        # Emit AgentMessage
        if output_text:
            runtime.effects.emit(
                AgentMessage(
                    task_name=task_name,
                    provider_id=self.provider_id,
                    content=output_text,
                    is_partial=False,
                )
            )
            if self._formatter:
                self._formatter.on_text_complete(output_text)

        # Emit LLM response metadata for profiling
        runtime.effects.emit(
            _extract_llm_response_effect(
                chat_result,
                task_name=task_name,
                provider_id=self.provider_id,
                model=self.model,
                wall_duration_ms=wall_ms,
            )
        )

        # Parse structured output from the last text part when requested.
        structured: dict[str, Any] = {}
        if binding and binding.output_format and output_text:
            last_text = _last_text_from_parts(getattr(chat_result, "parts", []))
            structured = _try_parse_json(last_text) if last_text else _try_parse_json(output_text)

        return ExecutionResult(
            success=True,
            output_text=output_text,
            structured_output=structured,
            session_id=session_id,
            metadata={
                "provider_type": "opencode",
                "model": self.model,
                "tool_call_count": len(tool_call_infos),
            },
        )

    async def _execute_streaming(
        self,
        client: Any,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        task_name: str | None,
    ) -> ExecutionResult:
        """Execute with real-time SSE streaming.

        Subscribes to the event stream, calls chat() in a background task,
        and emits real-time ToolCallStarted/Completed and partial AgentMessage
        effects as events arrive. Completes on session.idle.
        """
        import asyncio
        import time as _time

        from shepherd_providers.opencode._streaming import SSEConsumer

        params = self._translate_binding(binding)
        session_id = await self._resolve_session(client, binding)

        # Emit PromptSent
        system_prompt = params.get("system", "")
        runtime.effects.emit(
            PromptSent(
                task_name=task_name,
                provider_id=self.provider_id,
                system_prompt=system_prompt,
                user_prompt=prompt,
                model_id=self.model,
            )
        )
        if self._formatter:
            self._formatter.on_prompt_sent(system_prompt, prompt)

        # Subscribe to SSE event stream
        chat_start = _time.perf_counter()
        try:
            event_stream = await client.subscribe_events()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to subscribe to events, falling back to sync: {e}")
            return await self._execute_with_client(client, prompt, binding, runtime, task_name)

        # Create consumer for our session
        consumer = SSEConsumer(
            session_id=session_id,
            runtime=runtime,
            task_name=task_name,
            provider_id=self.provider_id,
            formatter=self._formatter,
        )

        # Start chat in background — it blocks until the agent loop completes
        chat_task = asyncio.create_task(
            client.send_message(
                session_id=session_id,
                message=prompt,
                provider_id=params["provider_id"],
                model_id=params["model_id"],
                system=params.get("system"),
                tools=params.get("tools"),
            )
        )

        # Consume events until session.idle or timeout
        try:
            streaming_result = await consumer.consume(event_stream, timeout=300.0)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"SSE consumption error: {e}")
            streaming_result = None
        finally:
            # Ensure chat task completes (it should already be done by session.idle)
            try:
                await asyncio.wait_for(chat_task, timeout=10.0)
            except (TimeoutError, Exception):  # noqa: BLE001
                chat_task.cancel()
                import contextlib

                with contextlib.suppress(asyncio.CancelledError):
                    await chat_task

        wall_ms = (_time.perf_counter() - chat_start) * 1000.0

        # Extract AssistantMessage from streaming result for LLM metadata
        assistant_msg = streaming_result.assistant_message if streaming_result else None

        if streaming_result and streaming_result.error:
            runtime.effects.emit(
                ExecutionFailed(
                    task_name=task_name,
                    provider_id=self.provider_id,
                    error_type="streaming_error",
                    error_message=streaming_result.error,
                    recoverable=False,
                )
            )
            runtime.effects.emit(
                _extract_llm_response_effect(
                    assistant_msg,
                    task_name=task_name,
                    provider_id=self.provider_id,
                    model=self.model,
                    wall_duration_ms=wall_ms,
                    is_error=True,
                )
            )
            return ExecutionResult(
                success=False,
                output_text=streaming_result.output_text or f"Streaming error: {streaming_result.error}",
                session_id=session_id,
                metadata={"error_type": "streaming_error", "error": streaming_result.error},
            )

        output_text = streaming_result.output_text if streaming_result else ""

        # Emit final complete AgentMessage (non-partial)
        if output_text:
            runtime.effects.emit(
                AgentMessage(
                    task_name=task_name,
                    provider_id=self.provider_id,
                    content=output_text,
                    is_partial=False,
                )
            )
            if self._formatter:
                self._formatter.on_text_complete(output_text)

        # Emit LLM response metadata for profiling
        runtime.effects.emit(
            _extract_llm_response_effect(
                assistant_msg,
                task_name=task_name,
                provider_id=self.provider_id,
                model=self.model,
                wall_duration_ms=wall_ms,
            )
        )

        # Parse structured output from response text when requested.
        structured: dict[str, Any] = {}
        if binding and binding.output_format and output_text:
            last_text = output_text
            if assistant_msg and hasattr(assistant_msg, "parts"):
                last_text = _last_text_from_parts(assistant_msg.parts) or output_text
            structured = _try_parse_json(last_text)

        return ExecutionResult(
            success=True,
            output_text=output_text,
            structured_output=structured,
            session_id=session_id,
            metadata={
                "provider_type": "opencode",
                "model": self.model,
                "streaming": True,
                "tool_calls_started": streaming_result.tool_calls_started if streaming_result else 0,
                "tool_calls_completed": streaming_result.tool_calls_completed if streaming_result else 0,
            },
        )

    def _extract_from_parts(self, parts: list[Any]) -> tuple[str, str, list[ToolCallInfo]]:
        """Extract output text, thinking, and tool call info from message parts.

        Parts may be dicts (from get_messages API) or SDK typed objects
        (from AssistantMessage.parts). This method handles both formats.

        Returns:
            (output_text, thinking, tool_call_infos)
        """
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_call_infos: list[ToolCallInfo] = []

        for part in parts:
            # Parts may be dicts (from get_messages) or objects (from SDK types)
            if isinstance(part, dict):
                part_type = part.get("type")
                part_text = part.get("text", "")
                part_tool = part.get("tool", "") or part.get("tool_name", "")
                part_id = part.get("id", "")
                part_call_id = part.get("call_id", "") or part.get("tool_call_id", "")
                part_input = part.get("input", "")
                part_output = part.get("output", "")
            else:
                part_type = getattr(part, "type", None)
                part_text = getattr(part, "text", "") or ""
                part_tool = getattr(part, "tool", "") or getattr(part, "tool_name", "") or ""
                part_id = getattr(part, "id", "")
                part_call_id = getattr(part, "call_id", "") or getattr(part, "tool_call_id", "") or ""
                part_input = getattr(part, "input", "") or ""
                part_output = getattr(part, "output", "") or ""

            if part_type == "text":
                if part_text:
                    text_parts.append(part_text)

            elif part_type == "reasoning":
                if part_text:
                    thinking_parts.append(part_text)

            elif part_type in ("tool-invocation", "tool"):
                tool_call_infos.append(
                    ToolCallInfo(
                        tool_name=part_tool,
                        tool_call_id=part_call_id or part_id or None,
                        input_preview=_truncate(str(part_input), 200),
                        output_preview=_truncate(str(part_output), 200),
                    )
                )

        output_text = "\n".join(text_parts)
        thinking = "\n".join(thinking_parts)
        return output_text, thinking, tool_call_infos


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------


def _register_provider_factory() -> None:
    """Register OpenCodeProvider factory with the container provider registry."""
    try:
        from shepherd_runtime.registry import (
            register_provider_factory,
        )

        register_provider_factory("opencode", OpenCodeProvider.from_config)
    except ImportError:
        logger.debug("Skipping container provider factory registration (device module unavailable)")


_register_provider_factory()


__all__ = ["OpenCodeProvider"]
