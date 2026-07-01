"""Claude Agent SDK provider implementation.

This module provides ClaudeProvider, which translates ProviderBinding to
ClaudeAgentOptions and executes via the Claude Agent SDK.

Provider-specific settings (like permission_mode, transcript_dir) are
configured here, NOT in contexts. Contexts express abstract needs via
trust_level, session_isolation, etc. and this provider translates.

Usage:
    provider = ClaudeProvider(
        name="analyst",
        model="claude-sonnet-4-20250514",
        default_permission_mode="acceptEdits",
    )
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

logger = logging.getLogger(__name__)

from shepherd_core.effects import (
    AgentMessage,
    AgentThinking,
    ExecutionFailed,
    LLMResponseReceived,
    PromptSent,
    RecoveryAttempted,
    ToolCallCompleted,
    ToolCallRejected,
    ToolCallStarted,
)
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

if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# INTERNAL HELPERS
# =============================================================================


@dataclass
class _LLMResponseMetadata:
    """Bag of response-side metadata extracted from the SDK's ResultMessage."""

    duration_ms: float = 0.0
    duration_api_ms: float = 0.0
    num_turns: int = 0
    is_error: bool = False
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    usage_raw: dict[str, Any] | None = None
    model_id: str = ""


# =============================================================================
# LAZY SDK IMPORT
# =============================================================================


class _SDKStatus(Enum):
    """Status of SDK import attempt."""

    NOT_ATTEMPTED = auto()
    UNAVAILABLE = auto()
    AVAILABLE = auto()


class _SDKCache(NamedTuple):
    """Type-safe container for SDK import cache."""

    status: _SDKStatus
    sdk: dict[str, Any] | None = None


_sdk_cache: _SDKCache = _SDKCache(_SDKStatus.NOT_ATTEMPTED)
_sdk_cache_lock = threading.Lock()


def _get_sdk() -> dict[str, Any] | None:
    """Lazily import Claude Agent SDK, caching the result.

    Thread-safe: Uses a lock to ensure atomic check-and-set.

    Returns:
        Dict with SDK classes/functions, or None if SDK not available.
    """
    global _sdk_cache

    # Fast path: return cached result if available
    if _sdk_cache.status == _SDKStatus.AVAILABLE:
        return _sdk_cache.sdk
    if _sdk_cache.status == _SDKStatus.UNAVAILABLE:
        return None

    # Slow path: acquire lock and try import
    with _sdk_cache_lock:
        # Double-check after acquiring lock
        if _sdk_cache.status == _SDKStatus.AVAILABLE:
            return _sdk_cache.sdk
        if _sdk_cache.status == _SDKStatus.UNAVAILABLE:
            return None

        # Status is NOT_ATTEMPTED - try to import
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                TextBlock,
                ThinkingBlock,
                ToolResultBlock,
                ToolUseBlock,
                UserMessage,
                create_sdk_mcp_server,
                query,
                tool,
            )

            sdk = {
                "query": query,
                "tool": tool,
                "create_sdk_mcp_server": create_sdk_mcp_server,
                "ClaudeAgentOptions": ClaudeAgentOptions,
                "AssistantMessage": AssistantMessage,
                "ResultMessage": ResultMessage,
                "UserMessage": UserMessage,
                "TextBlock": TextBlock,
                "ThinkingBlock": ThinkingBlock,
                "ToolUseBlock": ToolUseBlock,
                "ToolResultBlock": ToolResultBlock,
            }
            _sdk_cache = _SDKCache(_SDKStatus.AVAILABLE, sdk)
            return sdk
        except ImportError:
            _sdk_cache = _SDKCache(_SDKStatus.UNAVAILABLE)
            return None


def _sdk_available() -> bool:
    """Check if Claude Agent SDK is available."""
    return _get_sdk() is not None


def _reset_sdk_cache() -> None:
    """Reset the SDK cache for testing purposes.

    This allows tests to re-trigger SDK import detection, useful for:
    - Testing SDK availability detection
    - Mocking SDK imports
    - Resetting state between tests

    Example:
        def test_sdk_not_available(monkeypatch):
            _reset_sdk_cache()
            monkeypatch.setattr("claude_agent_sdk", None)
            assert not _sdk_available()
    """
    global _sdk_cache
    _sdk_cache = _SDKCache(_SDKStatus.NOT_ATTEMPTED)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Try to parse JSON from text, handling markdown code fences.

    LLMs often return JSON wrapped in markdown code blocks like:
        ```json
        {"key": "value"}
        ```

    This function handles:
    1. Plain JSON strings
    2. JSON wrapped in ```json ... ``` fences
    3. JSON wrapped in ``` ... ``` fences (no language tag)

    Args:
        text: The text that might contain JSON

    Returns:
        Parsed dict if successful, None otherwise
    """
    import re

    # First, try direct JSON parsing
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to extract JSON from markdown code fences
    # Pattern matches ```json or ``` followed by content and closing ```
    fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(fence_pattern, text, re.DOTALL)
    if match:
        json_content = match.group(1).strip()
        try:
            return json.loads(json_content)
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# =============================================================================
# CLAUDE PROVIDER
# =============================================================================


@dataclass
class ClaudeProvider(Provider):
    """Claude Agent SDK provider implementation.

    Translates ProviderBinding to ClaudeAgentOptions and executes via SDK.

    Provider-specific settings (like permission_mode) are configured here,
    NOT in contexts. Contexts express abstract needs via trust_level,
    session_isolation, etc. and this provider translates.

    Attributes:
        name: Human-readable name for this provider instance
        model: Claude model to use (default: claude-sonnet-4-20250514)
        default_permission_mode: Default permission mode for tool calls
            - "default": CLI prompts for dangerous tools
            - "acceptEdits": Auto-accept file edits
            - "plan": Planning mode - no execution
            - "bypassPermissions": All tools allowed
        max_turns: Maximum number of shepherd turns (None = unlimited)
        max_thinking_tokens: Max tokens for extended thinking (None = default)
        cwd: Working directory for the agent (None = current directory)
        verbose: Verbose output configuration (enables real-time console output)
    """

    name: str
    model: str = "claude-sonnet-4-20250514"
    # Provider-specific defaults (NOT in contexts)
    default_permission_mode: str = "default"
    max_turns: int | None = None
    max_thinking_tokens: int | None = None
    cwd: str | Path | None = None
    # Verbose output configuration
    verbose: VerboseConfig | None = None

    _id: str = field(default_factory=lambda: uuid4().hex[:8])
    _tool_handlers: dict[str, ToolDefinition] = field(default_factory=dict)
    _formatter: VerboseFormatter | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize verbose formatter if verbose output is enabled."""
        if self.verbose and self.verbose.enabled:
            self._formatter = VerboseFormatter(self.verbose)

    # === Serialization for Container Transfer ===

    def to_config(self) -> dict[str, Any]:
        """Serialize provider to config dict for container transfer.

        Returns a dict that can be passed to from_config() to reconstruct
        an equivalent provider in a container.

        Returns:
            Config dict with provider_type and all configuration.
        """
        config: dict[str, Any] = {
            "provider_type": "claude",
            "name": self.name,
            "model": self.model,
            "default_permission_mode": self.default_permission_mode,
        }

        # Optional fields
        if self.max_turns is not None:
            config["max_turns"] = self.max_turns
        if self.max_thinking_tokens is not None:
            config["max_thinking_tokens"] = self.max_thinking_tokens
        if self.cwd is not None:
            config["cwd"] = str(self.cwd)

        # Note: verbose config is not transferred to containers
        # (no console to write to)

        return config

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ClaudeProvider:
        """Reconstruct provider from config dict.

        Used by the container task runner to instantiate providers.

        Args:
            config: Config dict from to_config()

        Returns:
            ClaudeProvider instance
        """
        return cls(
            name=config.get("name", "container"),
            model=config.get("model", "claude-sonnet-4-20250514"),
            default_permission_mode=config.get("default_permission_mode", "default"),
            max_turns=config.get("max_turns"),
            max_thinking_tokens=config.get("max_thinking_tokens"),
            cwd=config.get("cwd"),
            # Verbose disabled in container (no console)
            verbose=None,
        )

    @property
    def provider_id(self) -> str:
        return f"provider:claude:{self.model}:{self.name}:{self._id}"

    @property
    def formatter(self) -> VerboseFormatter | None:
        """Return the verbose formatter if verbose output is enabled."""
        return self._formatter

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="claude",
            supports_streaming=True,
            supports_tools=True,
            supports_structured_output=True,
            supports_session=True,
            supports_fork_session=True,
            supports_images=True,
            available_tools=frozenset(
                {
                    "Read",
                    "Write",
                    "Edit",
                    "Glob",
                    "Grep",
                    "Bash",
                    "WebSearch",
                    "WebFetch",
                    "Task",
                    "NotebookEdit",
                }
            ),
        )

    def validate_binding(self, binding: ProviderBinding) -> None:
        """Validate binding against Claude provider capabilities.

        Claude supports all common binding requirements. This method validates
        trust_level values and warns about potentially unsupported configurations.

        Raises:
            BindingValidationError: If binding has unsupported requirements
        """
        from shepherd_core.errors import BindingValidationError

        issues: list[str] = []

        # Validate trust_level
        supported_trust = {"sandbox", "restricted", "standard", "elevated"}
        if binding.trust_level not in supported_trust:
            issues.append(f"trust_level='{binding.trust_level}' (supported: {sorted(supported_trust)})")

        # Claude supports all session isolation modes
        # Claude supports MCP servers
        # Claude supports custom tools (via in-process MCP)

        if issues:
            raise BindingValidationError(
                context_id=binding.context_id,
                unsatisfied_requirements=issues,
                provider_capabilities=self.capabilities,
            )

    def _translate_binding(self, binding: ProviderBinding | None) -> dict[str, Any]:
        """Translate to ClaudeAgentOptions format.

        Translates abstract binding fields to Claude-specific configuration:
        - trust_level -> permission_mode
        - session_isolation -> fork_session
        - custom_tools -> MCP server configuration
        - mcp_servers -> external MCP servers (passed through)
        """
        if binding is None:
            return {"model": self.model}

        # Build system prompt
        system_parts = []
        if binding.context_description:
            system_parts.append(f"## Context\n\n{binding.context_description}")
        system_parts.extend(binding.system_prompt_additions)
        system_prompt = "\n\n".join(system_parts) if system_parts else None

        # Compute allowed tools
        allowed_tools: list[str] | None = None
        if binding.capabilities:
            available = self.capabilities.available_tools or frozenset()
            allowed = binding.allowed_tools(available)
            if allowed:
                allowed_tools = list(allowed)

        # Translate abstract trust_level to Claude's permission_mode
        permission_mode = self._trust_to_permission(binding.trust_level)

        # Translate abstract session_isolation to fork_session
        fork_session = binding.session_isolation == "forked"

        # Build MCP servers: merge external servers with in-process tool servers
        mcp_servers: dict[str, Any] = dict(binding.mcp_servers)

        # Add in-process server for custom_tools if any
        if binding.custom_tools:
            in_process = self._create_mcp_server_from_tools(
                binding.custom_tools,
                binding.context_id,
            )
            mcp_servers.update(in_process)

        # Determine working directory
        cwd = binding.cwd or self.cwd
        # original_cwd is the pre-sandbox workspace path (for session tracking)
        # If not set, falls back to cwd (no sandbox wiring occurred)
        original_cwd = binding.original_cwd or cwd

        return {
            "system_prompt": system_prompt,
            "cwd": str(cwd) if cwd else None,
            "original_cwd": str(original_cwd) if original_cwd else None,
            "allowed_tools": allowed_tools,
            "resume": binding.session_id,
            "fork_session": fork_session,
            "permission_mode": permission_mode,
            "mcp_servers": mcp_servers or None,
            "model": self.model,
            "max_turns": self.max_turns,
            "max_thinking_tokens": self.max_thinking_tokens,
            "output_format": binding.output_format,
        }

    def _trust_to_permission(self, trust_level: str) -> str:
        """Translate abstract trust level to Claude's permission mode."""
        mapping = {
            "sandbox": "plan",  # Most restrictive: plan mode
            "restricted": "default",  # Default: ask for each action
            "standard": self.default_permission_mode,  # Use provider default
            "elevated": "bypassPermissions",  # Most permissive: auto-approve
        }
        return mapping.get(trust_level, self.default_permission_mode)

    def _build_recovery_prompt(self, result: ExecutionResult) -> str:
        """Build concise recovery prompt with failure context."""
        last_tool = result.metadata.get("last_tool_name")
        last_params = result.metadata.get("last_tool_params", {})

        parts = ["Previous command output exceeded 1MB buffer limit."]
        if last_tool:
            params_str = str(last_params)[:100]
            parts.append(f"Failed: {last_tool}({params_str})")
        parts.append(
            "Continue with targeted commands: exclude .git/.venv/.mypy_cache, "
            "use head/tail, prefer specific paths over recursive searches."
        )
        return " ".join(parts)

    def _create_recovery_binding(
        self,
        original_binding: ProviderBinding | None,
        session_id: str,
    ) -> ProviderBinding:
        """Create binding configured to fork from failed session."""
        if original_binding is None:
            return ProviderBinding(
                context_id="recovery",
                session_id=session_id,
                session_isolation="forked",
            )

        # Copy binding with fork settings
        return ProviderBinding(
            context_id=original_binding.context_id,
            context_type=original_binding.context_type,
            visible=original_binding.visible,
            context_description=original_binding.context_description,
            system_prompt_additions=original_binding.system_prompt_additions,
            capabilities=original_binding.capabilities,
            blocked_tools=original_binding.blocked_tools,
            custom_tools=original_binding.custom_tools,
            mcp_servers=original_binding.mcp_servers,
            validate_tool=original_binding.validate_tool,
            trust_level=original_binding.trust_level,
            cwd=original_binding.cwd,
            environment=original_binding.environment,
            output_format=original_binding.output_format,
            session_id=session_id,
            session_isolation="forked",
        )

    def _create_mcp_server_from_tools(
        self,
        custom_tools: tuple[ToolDefinition, ...],
        context_id: str,
    ) -> dict[str, Any]:
        """Create an in-process MCP server from ToolDefinitions.

        This method wraps ToolDefinition handlers in an MCP server that the
        Claude Agent SDK can invoke. The SDK routes tool calls to our handlers.
        """
        if not custom_tools:
            return {}

        sdk = _get_sdk()
        if not sdk:
            return {}

        tool_decorator = sdk["tool"]
        create_server = sdk["create_sdk_mcp_server"]

        # Auto-generate server name from context_id
        safe_name = context_id.replace(":", "_").replace("/", "_")[:32]
        server_name = f"ctx_{safe_name}"

        # Store handler references for routing (still useful for debugging)
        self._tool_handlers = {t.name: t for t in custom_tools}

        # Create executable tool wrappers
        sdk_tools = []
        for t in custom_tools:

            async def wrapper(args: dict[str, Any], _t=t) -> dict[str, Any]:
                # Generate a temporary ID for internal tracking
                tc = ToolCall(id=f"call_{uuid4().hex[:8]}", name=_t.name, params=args)
                try:
                    result = await self._invoke_tool_handler(_t, tc, context_id)

                    # Serialize result
                    if isinstance(result, (dict, list)):
                        text = json.dumps(result, indent=2)
                    else:
                        text = str(result)

                    return {"content": [{"type": "text", "text": text}]}
                except Exception as e:  # noqa: BLE001
                    logger.warning("Tool %r raised an exception: %s", _t.name, e, exc_info=True)
                    return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}

            # Apply SDK decorator
            wrapped = tool_decorator(t.name, t.description, t.parameters_schema)(wrapper)
            sdk_tools.append(wrapped)

        # Create SDK MCP server configuration
        server_config = create_server(
            name=server_name,
            version="1.0.0",
            tools=sdk_tools,
        )

        return {server_name: server_config}

    # Note: _invoke_tool_handler is inherited from Provider base class

    def _build_execution_options(
        self,
        binding: ProviderBinding | None,
        collected_stderr: list[str],
        hooks: dict | None = None,
    ) -> tuple[dict[str, Any], Any]:
        """Build SDK execution options from binding.

        Translates the binding into ClaudeAgentOptions format and creates
        the SDK options object.

        Args:
            binding: Provider binding configuration.
            collected_stderr: List to collect stderr lines for debugging.
            hooks: Optional hooks dict for ClaudeAgentOptions. Format:
                {"PreToolUse": [{"matcher": None, "hooks": [callback]}], ...}

        Returns:
            Tuple of (options_dict, sdk_options_object).

        Raises:
            ImportError: If Claude Agent SDK is not installed.
        """
        sdk = _get_sdk()
        if sdk is None:
            raise ImportError("Claude Agent SDK is not installed. Install it with: pip install claude-agent-sdk")

        # Build options from binding
        options_dict = self._translate_binding(binding)

        # Remove None values (SDK doesn't like them)
        options_dict = {k: v for k, v in options_dict.items() if v is not None}

        # Capture stderr for debugging - collect lines via callback
        def stderr_callback(line: str) -> None:
            """Capture stderr lines from SDK subprocess for debugging."""
            collected_stderr.append(line)

        options_dict["stderr"] = stderr_callback

        # Merge hooks into options if provided (for per-tool-call overlay isolation)
        if hooks:
            options_dict["hooks"] = hooks

        # Create SDK options (exclude internal fields not recognized by SDK)
        sdk_options_dict = {k: v for k, v in options_dict.items() if k != "original_cwd"}
        ClaudeAgentOptions = sdk["ClaudeAgentOptions"]
        options = ClaudeAgentOptions(**sdk_options_dict)

        return options_dict, options

    def _process_tool_result_block(
        self,
        block: Any,
        pending_tool_calls: dict[str, ToolCall],
        runtime: ProviderRuntime,
        *,
        seen_tool_use_ids: set[str],
        tool_start_times: dict[str, float],
    ) -> ToolResult | None:
        """Process a ToolResultBlock: emit ToolCallCompleted and return ToolResult.

        Returns None if this tool_use_id has already been processed.
        """
        tool_id = block.tool_use_id
        if tool_id in seen_tool_use_ids:
            return None
        seen_tool_use_ids.add(tool_id)

        # Compute tool call duration (0 if no start time was recorded)
        now = time.perf_counter()
        duration_ms = (now - tool_start_times.pop(tool_id, now)) * 1000

        is_error = block.is_error or False
        content = block.content

        def _content_item_to_str(item: Any) -> str:
            if isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    return str(text)
                return str(item)

            text_attr = getattr(item, "text", None)
            if isinstance(text_attr, str):
                return text_attr

            return str(item)

        # Convert content to string if needed
        if isinstance(content, list):
            content_str = " ".join(part for part in (_content_item_to_str(c) for c in content) if part)
        elif content is None:
            content_str = ""
        else:
            content_str = str(content)

        # Get tool name from pending calls
        pending_call = pending_tool_calls.pop(tool_id, None)
        result_tool_name = pending_call.name if pending_call else "unknown"

        # Emit completion effect
        runtime.effects.emit(
            ToolCallCompleted(
                task_name=runtime.task_name,
                provider_id=self.provider_id,
                tool_call_id=tool_id,
                tool_name=result_tool_name,
                success=not is_error,
                output=content_str or "",
                duration_ms=duration_ms,
            )
        )

        # Verbose output
        if self._formatter and self.verbose and self.verbose.show_tool_results:
            self._formatter.on_tool_call_completed(
                tool_name=result_tool_name,
                result=content_str[:200] if content_str else None,
                is_error=is_error,
            )

        return ToolResult(
            tool_call_id=tool_id,
            success=not is_error,
            output=content_str,
        )

    async def _process_message_stream(
        self,
        prompt: str,
        options: Any,
        options_dict: dict[str, Any],
        validator: Any,
        runtime: ProviderRuntime,
        collected_stderr: list[str],
        binding: ProviderBinding | None = None,
    ) -> tuple[
        list[str],  # collected_text
        list[str],  # collected_thinking
        list[ToolCall],  # tool_calls
        list[ToolResult],  # tool_results
        dict[str, Any] | None,  # structured_output
        str | None,  # session_id
        str,  # final_result
        Exception | None,  # error if one occurred
        _LLMResponseMetadata,  # LLM response metadata
    ]:
        """Process the streaming message response from the SDK.

        Handles AssistantMessage and ResultMessage, emitting effects and
        collecting results as messages arrive. Catches errors and returns
        partial state along with the error for proper handling.

        Args:
            prompt: The user prompt being executed.
            options: The SDK options object.
            options_dict: The options dictionary for reference.
            validator: The tool validator function.
            runtime: Execution runtime for effect emission and attribution.
            collected_stderr: List to collect stderr lines for debugging.
            binding: Provider binding for effect attribution.

        Returns:
            Tuple containing all collected state from the stream, plus any error.
        """
        sdk = _get_sdk()
        if sdk is None:
            raise ImportError("Claude Agent SDK is not installed.")

        query = sdk["query"]
        AssistantMessage = sdk["AssistantMessage"]
        ResultMessage = sdk["ResultMessage"]
        UserMessage = sdk["UserMessage"]
        TextBlock = sdk["TextBlock"]
        ThinkingBlock = sdk["ThinkingBlock"]
        ToolUseBlock = sdk["ToolUseBlock"]
        ToolResultBlock = sdk["ToolResultBlock"]

        # Collect results during streaming
        collected_text: list[str] = []
        collected_thinking: list[str] = []
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []
        structured_output: dict[str, Any] | None = None
        session_id: str | None = None
        final_result: str = ""
        error: Exception | None = None

        # Track tool use blocks for matching with results
        pending_tool_calls: dict[str, ToolCall] = {}
        seen_tool_use_ids: set[str] = set()

        # Profiler metadata tracking
        tool_start_times: dict[str, float] = {}
        last_model_id: str = ""
        llm_metadata = _LLMResponseMetadata()

        try:
            async for message in query(prompt=prompt, options=options):
                # Handle AssistantMessage (contains content blocks)
                if isinstance(message, AssistantMessage):
                    last_model_id = getattr(message, "model", "") or ""
                    if not isinstance(message.content, list):
                        continue
                    for block in message.content:
                        # Text content
                        if isinstance(block, TextBlock):
                            text = block.text
                            collected_text.append(text)

                            # Emit effect
                            runtime.effects.emit(
                                AgentMessage(
                                    task_name=runtime.task_name,
                                    provider_id=self.provider_id,
                                    content=text,
                                    is_partial=False,
                                )
                            )

                            # Verbose output
                            if self._formatter:
                                self._formatter.on_text_complete(text)

                        # Thinking content
                        elif isinstance(block, ThinkingBlock):
                            thinking = block.thinking
                            collected_thinking.append(thinking)

                            # Emit effect
                            runtime.effects.emit(
                                AgentThinking(
                                    task_name=runtime.task_name,
                                    provider_id=self.provider_id,
                                    content=thinking,
                                    is_partial=False,
                                )
                            )

                            # Verbose output
                            if self._formatter and self.verbose and self.verbose.show_thinking:
                                self._formatter.on_thinking_complete(thinking)

                        # Tool use
                        elif isinstance(block, ToolUseBlock):
                            tool_id = block.id
                            tool_name = block.name
                            tool_input = block.input

                            tool_call = ToolCall(
                                id=tool_id,
                                name=tool_name,
                                params=tool_input,
                            )

                            # Validate tool call
                            validation = validator(tool_call)

                            if not validation.allowed:
                                # Tool was rejected
                                runtime.effects.emit(
                                    ToolCallRejected(
                                        task_name=runtime.task_name,
                                        provider_id=self.provider_id,
                                        tool_call_id=tool_id,
                                        tool_name=tool_name,
                                        reason=validation.rejection_reason or "Validation failed",
                                        binding_name=binding.context_id if binding else None,
                                    )
                                )
                                continue

                            # Track for result matching
                            pending_tool_calls[tool_id] = tool_call
                            tool_calls.append(tool_call)

                            # Emit start effect and record timing
                            tool_start_times[tool_id] = time.perf_counter()
                            runtime.effects.emit(
                                ToolCallStarted(
                                    task_name=runtime.task_name,
                                    provider_id=self.provider_id,
                                    tool_call_id=tool_id,
                                    tool_name=tool_name,
                                    params=tool_input,
                                )
                            )

                            # Verbose output
                            if self._formatter and self.verbose and self.verbose.show_tool_calls:
                                self._formatter.on_tool_call_started(
                                    tool_name=tool_name,
                                    tool_input=tool_input,
                                )

                        # Tool result
                        elif isinstance(block, ToolResultBlock):
                            tool_result = self._process_tool_result_block(
                                block,
                                pending_tool_calls,
                                runtime,
                                seen_tool_use_ids=seen_tool_use_ids,
                                tool_start_times=tool_start_times,
                            )
                            if tool_result is not None:
                                tool_results.append(tool_result)

                # Handle UserMessage (contains tool results from SDK)
                elif isinstance(message, UserMessage):
                    if not isinstance(message.content, list):
                        continue
                    for block in message.content:
                        # Tool results are in UserMessage content
                        if isinstance(block, ToolResultBlock):
                            tool_result = self._process_tool_result_block(
                                block,
                                pending_tool_calls,
                                runtime,
                                seen_tool_use_ids=seen_tool_use_ids,
                                tool_start_times=tool_start_times,
                            )
                            if tool_result is not None:
                                tool_results.append(tool_result)

                # Handle ResultMessage (final message with structured output and costs)
                elif isinstance(message, ResultMessage):
                    # Extract session ID
                    session_id = message.session_id

                    # Extract structured output
                    if message.structured_output is not None:
                        structured_output = message.structured_output
                    elif message.result and options_dict.get("output_format"):
                        # Try to parse as JSON (with markdown fence stripping)
                        # Only attempt this if we expected structured output
                        result_text = message.result
                        structured_output = _try_parse_json(result_text)
                        if structured_output is None:
                            final_result = result_text
                    elif message.result:
                        final_result = message.result

                    # Extract LLM response metadata for profiling
                    duration_ms = getattr(message, "duration_ms", 0)
                    duration_api_ms = getattr(message, "duration_api_ms", 0)
                    num_turns = getattr(message, "num_turns", 0)
                    msg_is_error = getattr(message, "is_error", False)
                    cost_usd = getattr(message, "total_cost_usd", None)
                    raw_usage = getattr(message, "usage", None)

                    input_tokens = 0
                    output_tokens = 0
                    cache_creation_input_tokens = 0
                    cache_read_input_tokens = 0
                    if isinstance(raw_usage, dict):
                        input_tokens = raw_usage.get("input_tokens", 0) or 0
                        output_tokens = raw_usage.get("output_tokens", 0) or 0
                        cache_creation_input_tokens = raw_usage.get("cache_creation_input_tokens", 0) or 0
                        cache_read_input_tokens = raw_usage.get("cache_read_input_tokens", 0) or 0

                    llm_metadata = _LLMResponseMetadata(
                        duration_ms=duration_ms,
                        duration_api_ms=duration_api_ms,
                        num_turns=num_turns,
                        is_error=msg_is_error,
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_creation_input_tokens=cache_creation_input_tokens,
                        cache_read_input_tokens=cache_read_input_tokens,
                        usage_raw=raw_usage if isinstance(raw_usage, dict) else None,
                        model_id=last_model_id,
                    )

        except Exception as e:  # noqa: BLE001
            error = e

        return (
            collected_text,
            collected_thinking,
            tool_calls,
            tool_results,
            structured_output,
            session_id,
            final_result,
            error,
            llm_metadata,
        )

    def _handle_execution_error(
        self,
        error: Exception,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
        session_id: str | None,
        collected_stderr: list[str],
        options_dict: dict[str, Any],
        prompt: str,
        runtime: ProviderRuntime,
    ) -> ExecutionResult | None:
        """Handle execution errors, returning a result for buffer overflow.

        Detects buffer overflow errors and returns a recoverable ExecutionResult.
        For other errors, wraps them in SDKExecutionError and re-raises.

        Args:
            error: The exception that occurred.
            tool_calls: Tool calls completed before the error.
            tool_results: Tool results collected before the error.
            session_id: Session ID if available.
            collected_stderr: Collected stderr lines for debugging.
            options_dict: The SDK options dictionary.
            prompt: The original prompt.
            runtime: Execution runtime for effect emission and attribution.

        Returns:
            ExecutionResult for buffer overflow errors, None otherwise (re-raises).

        Raises:
            SDKExecutionError: For non-buffer-overflow errors.
        """
        error_str = str(error).lower()

        # Detect buffer overflow error from SDK transport
        if "buffer size" in error_str or "maximum buffer" in error_str:
            last_tool = tool_calls[-1] if tool_calls else None

            logger.warning(
                f"Buffer overflow in session {session_id}: {error}. "
                f"Tool calls before failure: {len(tool_calls)}. "
                f"See: https://github.com/anthropics/claude-code/issues/17591"
            )

            runtime.effects.emit(
                ExecutionFailed(
                    task_name=runtime.task_name,
                    provider_id=self.provider_id,
                    error_type="buffer_overflow",
                    error_message=str(error),
                    tool_calls_completed=len(tool_calls),
                    last_tool_name=last_tool.name if last_tool else None,
                    recoverable=session_id is not None,
                )
            )

            # Build actionable error message
            error_guidance = "Execution interrupted: tool output exceeded 1MB transport buffer. "
            if last_tool:
                error_guidance += f"Failed command: {last_tool.name}. "
            error_guidance += (
                "Avoid: recursive finds, large directory listings. "
                "Use: --exclude patterns, head/tail limits, specific paths."
            )

            return ExecutionResult(
                success=False,
                output_text=error_guidance,
                tool_calls=tuple(tool_calls),
                tool_results=tuple(tool_results),
                session_id=session_id,
                structured_output={},
                metadata={
                    "model": self.model,
                    "error_type": "buffer_overflow",
                    "error": str(error),
                    "partial": True,
                    "last_tool_name": last_tool.name if last_tool else None,
                    "last_tool_params": last_tool.params if last_tool else None,
                    "recoverable": session_id is not None,
                },
            )

        # Wrap all other errors with debugging context
        from shepherd_core.errors import SDKExecutionError

        last_tool = tool_calls[-1] if tool_calls else None
        suggestions = suggest_fixes(
            error,
            session_id=session_id,
            last_tool=last_tool,
            provider="claude",
        )

        # Extract stderr: prefer exception's stderr, fall back to collected lines
        stderr_output: str | None = None
        if hasattr(error, "stderr") and error.stderr:
            stderr_output = error.stderr
        elif collected_stderr:
            stderr_output = "\n".join(collected_stderr)

        raise SDKExecutionError(
            message=str(error),
            original_error=error,
            stderr=stderr_output,
            prompt_preview=prompt,
            sdk_options=options_dict,
            session_id=session_id,
            last_tool_name=last_tool.name if last_tool else None,
            last_tool_params=last_tool.params if last_tool else {},
            phase="execute",
            suggestions=suggestions,
        ) from error

    def _build_execution_result(
        self,
        collected_text: list[str],
        collected_thinking: list[str],
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
        structured_output: dict[str, Any] | None,
        session_id: str | None,
        final_result: str,
        options_dict: dict[str, Any],
    ) -> ExecutionResult:
        """Build the final ExecutionResult from collected state.

        Handles structured output extraction and transcript path computation.

        Args:
            collected_text: Text blocks collected during streaming.
            collected_thinking: Thinking blocks collected during streaming.
            tool_calls: All tool calls made during execution.
            tool_results: All tool results received.
            structured_output: Structured output if already extracted.
            session_id: Session ID from the SDK.
            final_result: Final result text if no structured output.
            options_dict: The SDK options dictionary.

        Returns:
            Complete ExecutionResult.
        """
        # Build final output text
        output_text = "\n".join(collected_text) if collected_text else final_result

        # If structured_output is still None but we expected it (output_format was set),
        # try to extract from collected text. This handles the case where Claude returns
        # JSON in a text block instead of using the StructuredOutput tool.
        if structured_output is None and output_text and options_dict.get("output_format"):
            structured_output = _try_parse_json(output_text)

        # Compute transcript_path if session_id is available
        transcript_path = None
        if session_id:
            from shepherd_core.types import compute_transcript_path

            effective_cwd = options_dict.get("cwd") or self.cwd
            transcript_path = compute_transcript_path(effective_cwd, session_id)

        # original_cwd is the pre-sandbox workspace path (for session tracking)
        # This is what SessionState captures as host_cwd for CWD validation
        original_cwd = options_dict.get("original_cwd") or options_dict.get("cwd") or self.cwd

        return ExecutionResult(
            success=True,
            output_text=output_text,
            tool_calls=tuple(tool_calls),
            tool_results=tuple(tool_results),
            session_id=session_id,
            structured_output=structured_output or {},  # Ensure dict, not None
            metadata={
                "model": self.model,
                "thinking_length": len("\n".join(collected_thinking)),
                "tool_call_count": len(tool_calls),
                "transcript_path": transcript_path,
                "cwd": original_cwd,  # Use original_cwd for session tracking
            },
        )

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        hooks: dict | None = None,
    ) -> ExecutionResult:
        """Execute via Claude Agent SDK.

        This method orchestrates execution by delegating to focused helper methods:
        1. _build_execution_options(): Create SDK options from binding
        2. _process_message_stream(): Process streaming responses
        3. _handle_execution_error(): Handle errors appropriately
        4. _build_execution_result(): Build final result

        Raises:
            ImportError: If claude-agent-sdk is not installed.
            SDKExecutionError: For non-recoverable execution errors.
        """
        # Build SDK options from binding
        collected_stderr: list[str] = []
        options_dict, options = self._build_execution_options(binding, collected_stderr, hooks=hooks)

        # Build validator for tool calls
        validator = self._build_composite_validator(
            binding,
            runtime,
            binding_name=binding.context_id if binding else None,
        )

        # Emit prompt sent effect
        system_prompt = options_dict.get("system_prompt", "")
        runtime.effects.emit(
            PromptSent(
                task_name=runtime.task_name,
                provider_id=self.provider_id,
                system_prompt=system_prompt or "",
                user_prompt=prompt,
                model_id=self.model,
            )
        )

        # Verbose output: show prompt
        if self._formatter and self.verbose and self.verbose.show_prompts:
            self._formatter.on_prompt_sent(system_prompt or "", prompt)

        # Process message stream - returns partial state plus any error
        (
            collected_text,
            collected_thinking,
            tool_calls,
            tool_results,
            structured_output,
            session_id,
            final_result,
            error,
            llm_metadata,
        ) = await self._process_message_stream(
            prompt, options, options_dict, validator, runtime, collected_stderr, binding
        )

        # Emit LLM response metadata for profiling (always, even on error —
        # metadata may be partial but signals that an invocation occurred).
        runtime.effects.emit(
            LLMResponseReceived(
                task_name=runtime.task_name,
                provider_id=self.provider_id,
                input_tokens=llm_metadata.input_tokens,
                output_tokens=llm_metadata.output_tokens,
                total_tokens=llm_metadata.input_tokens + llm_metadata.output_tokens,
                cost_usd=llm_metadata.cost_usd,
                duration_ms=llm_metadata.duration_ms,
                duration_api_ms=llm_metadata.duration_api_ms,
                num_turns=llm_metadata.num_turns,
                model_id=llm_metadata.model_id,
                is_error=error is not None,
                usage_details=llm_metadata.usage_raw,
                cache_creation_input_tokens=llm_metadata.cache_creation_input_tokens,
                cache_read_input_tokens=llm_metadata.cache_read_input_tokens,
            )
        )

        # Handle error if one occurred during streaming
        if error is not None:
            error_result = self._handle_execution_error(
                error,
                tool_calls,
                tool_results,
                session_id,
                collected_stderr,
                options_dict,
                prompt,
                runtime,
            )
            if error_result is not None:
                return error_result
            # _handle_execution_error raises for non-buffer-overflow errors
            raise error  # pragma: no cover

        # Build and return final result
        return self._build_execution_result(
            collected_text,
            collected_thinking,
            tool_calls,
            tool_results,
            structured_output,
            session_id,
            final_result,
            options_dict,
        )

    async def execute_sdk_with_recovery(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        max_recovery_attempts: int = 1,
    ) -> ExecutionResult:
        """Execute with automatic recovery from buffer overflow.

        If buffer overflow occurs with a session_id, forks the session
        and retries with context about what failed.

        Args:
            prompt: The user prompt to execute
            binding: Provider binding configuration
            runtime: Execution runtime for effects and attribution
            max_recovery_attempts: Maximum recovery retries (default: 1)

        Returns:
            ExecutionResult from successful execution or final failure
        """
        result = await self.execute_sdk(prompt, binding, runtime)

        attempts_remaining = max_recovery_attempts
        while (
            not result.success
            and result.metadata.get("error_type") == "buffer_overflow"
            and result.metadata.get("recoverable")
            and result.session_id
            and attempts_remaining > 0
        ):
            logger.info(f"Attempting recovery for session {result.session_id}")

            runtime.effects.emit(
                RecoveryAttempted(
                    task_name=runtime.task_name,
                    provider_id=self.provider_id,
                    original_session_id=result.session_id,
                    error_type="buffer_overflow",
                    last_tool_name=result.metadata.get("last_tool_name"),
                )
            )

            recovery_prompt = self._build_recovery_prompt(result)
            recovery_binding = self._create_recovery_binding(binding, result.session_id)

            result = await self.execute_sdk(recovery_prompt, recovery_binding, runtime)
            attempts_remaining -= 1

        return result


__all__ = ["ClaudeProvider"]


# =============================================================================
# Provider Factory Registration
# =============================================================================


def _register_provider_factory() -> None:
    """Register ClaudeProvider factory with the provider registry.

    Called at module import time. Wrapped in function to allow
    graceful handling if device module not yet available.
    """
    try:
        register_provider_factory("claude", ClaudeProvider.from_config)
    except ImportError:
        # Device module may not be installed or may not be usable in this environment.
        # Provider construction should still work (factory registration is optional).
        logger.debug("Skipping container provider factory registration (device module unavailable)")


_register_provider_factory()
