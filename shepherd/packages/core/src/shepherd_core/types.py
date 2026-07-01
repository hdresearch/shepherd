"""Core types for the three-layer execution context framework.

This module defines the foundational types used throughout the framework:
- ReversibilityLevel: How reversible effects are
- ToolCall, ToolResult: Provider-agnostic tool invocation types
- ValidationResult: Result from tool validation
- ProviderCapabilities: What a provider supports
- ProviderBinding: Configuration contributed by contexts
- ExecutionResult: Result from provider execution
- ToolDefinition: Custom tool definition

Design Principles:
1. All types are immutable (frozen Pydantic models)
2. Provider-agnostic (no SDK-specific types)
3. Composable (ProviderBinding.compose())
4. Type-safe with generics where appropriate
5. Serializable via Pydantic (model_dump/model_validate)

Three-Layer Model:
- Layer 1 (Scope): Uses these types for binding storage
- Layer 2 (ExecutionLifecycle): Composes ProviderBindings, produces ExecutionResults
- Layer 3 (Provider): Translates ProviderBinding to SDK config
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from enum import Enum, auto
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shepherd_core.errors import CapabilityError, PreparationError

# =============================================================================
# Base Model Classes (D43: Strict Validation)
# =============================================================================


class StrictModel(BaseModel):
    """Base class for internal protocol types.

    Uses extra="forbid" to catch typos and invalid fields immediately
    rather than silently dropping them. Also validates default values
    to catch type mismatches at class definition time.

    Use this for types we control: ProviderBinding, ExecutionResult, etc.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class StrictModelWithCallables(BaseModel):
    """StrictModel variant that allows callable fields.

    Some internal types (ToolDefinition, ProviderBinding) have callable
    fields like handlers and validators that require arbitrary_types_allowed.

    Use this for types with Callable fields that can't be serialized.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        arbitrary_types_allowed=True,
    )


class ExtensibleModel(BaseModel):
    """Base class for external data types.

    Uses extra="ignore" to allow forward-compatibility when parsing
    responses from external systems (providers, APIs) that may gain
    new fields over time.

    Use this for types from external sources: LLM responses, API payloads.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")


# =============================================================================
# Reversibility
# =============================================================================


class ReversibilityLevel(Enum):
    """How reversible are effects from this context?

    Composition follows "weakest link" semantics:
    - AUTO + AUTO = AUTO
    - AUTO + COMPENSABLE = COMPENSABLE
    - anything + NONE = NONE
    """

    AUTO = auto()  # Mechanically reversible (git reset, db rollback)
    COMPENSABLE = auto()  # Requires compensation action (send correction email)
    NONE = auto()  # Cannot be reversed (published tweet, sent SMS)

    def compose(self, other: ReversibilityLevel) -> ReversibilityLevel:
        """Compose two levels (weakest wins)."""
        order = [ReversibilityLevel.AUTO, ReversibilityLevel.COMPENSABLE, ReversibilityLevel.NONE]
        return order[max(order.index(self), order.index(other))]

    @classmethod
    def compose_all(cls, levels: Iterator[ReversibilityLevel]) -> ReversibilityLevel:
        """Compose multiple levels (weakest wins)."""
        result = cls.AUTO
        for level in levels:
            result = result.compose(level)
        return result


# =============================================================================
# Tool Types
# =============================================================================


class ToolCall(StrictModel):
    """Provider-agnostic tool invocation."""

    id: str
    name: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_fields(cls, data: Any) -> Any:
        """Back-compat for older field names used in some providers/tests."""
        if isinstance(data, dict) and "input" in data and "params" not in data:
            # Legacy name used by some providers and older internal code.
            data = dict(data)
            data["params"] = data.pop("input")
        return data

    def with_params(self, **updates: Any) -> ToolCall:
        """Return copy with updated params."""
        return self.model_copy(update={"params": {**self.params, **updates}})

    def __repr__(self) -> str:
        return f"ToolCall({self.name!r}, id={self.id!r})"


class ToolResult(StrictModel):
    """Provider-agnostic tool result."""

    tool_call_id: str
    success: bool
    output: Any = None
    error: str | None = None

    def __repr__(self) -> str:
        status = "ok" if self.success else f"err: {self.error}"
        return f"ToolResult({self.tool_call_id!r}, {status})"


class ValidationResult(StrictModel):
    """Result from validating a tool call."""

    allowed: bool
    tool: ToolCall  # May be modified from original
    rejection_reason: str | None = None

    @classmethod
    def allow(cls, tool: ToolCall) -> ValidationResult:
        """Allow the tool call (optionally with modified params)."""
        return cls(allowed=True, tool=tool)

    @classmethod
    def reject(cls, tool: ToolCall, reason: str) -> ValidationResult:
        """Reject the tool call with a reason."""
        return cls(allowed=False, tool=tool, rejection_reason=reason)


# Callback type aliases
ToolValidator = Callable[[ToolCall], ValidationResult]


class ToolContext(StrictModel):
    """Context passed to tool handlers when inject_context=True.

    Provides tool handlers with execution context information without
    coupling them to the full ExecutionContext protocol.
    """

    context_id: str
    tool_name: str
    tool_call_id: str
    execution_context: Any | None = None  # The ExecutionContext that owns this tool


# Type aliases for tool handlers
# Standard: (args) -> result
# With context: (ToolContext, args) -> result
ToolHandler = Callable[[dict[str, Any]], Any]
ToolHandlerWithContext = Callable[[ToolContext, dict[str, Any]], Any]

# Async variants
AsyncToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]
AsyncToolHandlerWithContext = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]

# Error handler: (exception) -> error message string
ToolErrorHandler = Callable[[Exception], str]


class ToolDefinition(StrictModelWithCallables):
    """Provider-agnostic custom tool definition.

    Contexts can provide in-process custom tools that get translated to
    provider-specific formats:
    - Claude: Wrapped in an in-process MCP server via create_sdk_mcp_server()
    - OpenAI: Converted to FunctionTool instances

    For external MCP servers (subprocess or remote), use mcp_servers in
    ProviderBinding instead - those don't need Python handlers.

    Attributes:
        name: Tool name (will be prefixed by provider, e.g., mcp__ctx__name)
        description: Human-readable description for the LLM
        parameters_schema: JSON Schema for tool parameters
        handler: Sync handler function
        async_handler: Async handler (preferred if provided)
        error_handler: Convert exceptions to error messages (None = re-raise)
        inject_context: If True, handler receives (ToolContext, args) instead of just args
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]  # JSON Schema
    handler: ToolHandler | ToolHandlerWithContext = Field(exclude=True)  # Not serializable
    async_handler: AsyncToolHandler | AsyncToolHandlerWithContext | None = Field(
        default=None, exclude=True
    )  # Not serializable
    error_handler: ToolErrorHandler | None = Field(default=None, exclude=True)  # Not serializable
    inject_context: bool = False  # If True, handler receives (ToolContext, args)


# =============================================================================
# Provider Capabilities
# =============================================================================


class ProviderCapabilities(StrictModel):
    """What a provider supports - passed to configure() for adaptation."""

    provider_type: str  # "claude", "openai", "anthropic_api", etc.
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_structured_output: bool = True
    supports_session: bool = False
    supports_fork_session: bool = False
    supports_images: bool = False
    max_tools: int | None = None
    available_tools: frozenset[str] | None = None  # Built-in tools available


# =============================================================================
# Capability to Tool Mapping
# =============================================================================

# Framework-level mapping from capabilities to tools
CAPABILITY_TOOL_MAP: dict[str, frozenset[str]] = {
    "read": frozenset({"Read", "Glob", "Grep", "read", "grep", "glob", "list"}),
    "write": frozenset({"Write", "Edit", "NotebookEdit", "write", "edit", "patch"}),
    "bash": frozenset({"Bash", "bash"}),
    "web": frozenset({"WebSearch", "WebFetch", "webfetch", "websearch"}),
    "task": frozenset({"Task", "todowrite"}),
}

# Reverse mapping: tool -> required capability
TOOL_CAPABILITY_REQUIREMENTS: dict[str, str] = {
    # PascalCase entries (Claude SDK tool names)
    "Write": "write",
    "Edit": "write",
    "NotebookEdit": "write",
    "Bash": "bash",
    "WebSearch": "web",
    "WebFetch": "web",
    "Task": "task",
    # Lowercase entries (OpenAI/LiteLLM provider tool names)
    # Added by Spike 1 — see design/SPIKES-openai-provider.md
    "bash": "bash",
    "write_file": "write",
    "edit_file": "write",
    # Note: read_file, search_files, search_content are intentionally omitted.
    # Read-only tools are ungated by design, matching Read/Glob/Grep above.
    # Lowercase entries (OpenCode provider tool names)
    "write": "write",
    "edit": "write",
    "patch": "write",
    "webfetch": "web",
    "websearch": "web",
    "todowrite": "task",
    # Note: read, grep, glob, list are intentionally omitted (ungated read tools).
}


def tools_for_capabilities(caps: frozenset[str]) -> frozenset[str]:
    """Map capabilities to allowed tools."""
    result: set[str] = set()
    for cap in caps:
        result.update(CAPABILITY_TOOL_MAP.get(cap, frozenset()))
    return frozenset(result)


def capability_for_tool(tool_name: str) -> str | None:
    """Get required capability for a tool, if any."""
    return TOOL_CAPABILITY_REQUIREMENTS.get(tool_name)


# =============================================================================
# Provider Binding
# =============================================================================


class ProviderBinding(StrictModelWithCallables):
    """Provider-agnostic binding configuration returned by contexts.

    Contexts return this from configure(). Providers translate to SDK-specific
    config. Multiple bindings are composed before translation.

    Design: Declarative, immutable, composable. NO provider-specific fields.

    Composition Semantics:
    - context_ids: joined with ","
    - visible: all must be visible for composite to be visible
    - context_description: joined with newlines
    - system_prompt_additions: concatenated
    - capabilities: intersection (most restrictive)
    - blocked_tools: union (all blocks apply)
    - custom_tools: concatenated (in-process tools with handlers)
    - mcp_servers: merged (external servers, later wins on name conflicts)
    - validate_tool: chained (first rejection wins)
    - session_id: first non-None wins
    - session_isolation: most isolated wins (isolated > forked > shared)
    - trust_level: most restrictive wins (sandbox < restricted < standard < elevated)
    - require_confirmation: union (all confirmations apply)
    - cwd: last wins
    - environment: merged (later wins for conflicts)
    """

    # === Identity ===
    context_id: str = ""
    context_type: str = ""

    # === Visibility ===
    visible: bool = True
    context_description: str | None = None

    # === Prompt Additions ===
    system_prompt_additions: tuple[str, ...] = ()

    # === Capabilities (framework maps to tools) ===
    capabilities: frozenset[str] = frozenset()

    # === Tool Restrictions ===
    blocked_tools: frozenset[str] = frozenset()

    # === Custom Tools ===
    # In-process tools with Python handlers (provider creates MCP server or FunctionTool)
    custom_tools: tuple[ToolDefinition, ...] = ()
    # External MCP servers (subprocess or remote) - config only, no handlers
    # Keys are server names, values are transport configs:
    #   stdio: {"command": "...", "args": [...], "env": {...}}
    #   sse:   {"type": "sse", "url": "...", "headers": {...}}
    #   http:  {"type": "http", "url": "...", "headers": {...}}
    mcp_servers: Mapping[str, dict[str, Any]] = Field(default_factory=dict)

    # === Validation (for custom logic beyond capabilities) ===
    validate_tool: ToolValidator | None = Field(default=None, exclude=True)  # Not serializable

    # === Session (abstract - providers translate to their session model) ===
    session_id: str | None = None
    session_isolation: Literal["shared", "forked", "isolated"] = "shared"

    # === Trust (abstract - providers translate to their permission model) ===
    trust_level: Literal["sandbox", "restricted", "standard", "elevated"] = "standard"
    require_confirmation: frozenset[str] = frozenset()  # Tool names requiring confirmation

    # === Environment ===
    cwd: str | None = None
    original_cwd: str | None = None  # Pre-sandbox workspace path (for session tracking)
    environment: Mapping[str, str] = Field(default_factory=dict)

    # === Structured Output ===
    # JSON Schema for structured output extraction (provider-specific handling)
    output_format: dict[str, Any] | None = None

    # === Helpers ===

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_fields(cls, data: Any) -> Any:
        """Back-compat for older ProviderBinding field names.

        ProviderBinding used to include fields like `context_ids` and `system_prompt`.
        Convert those into the current representation to keep strict validation.
        """
        if not isinstance(data, dict):
            return data

        updated = dict(data)

        # Older code passed `context_ids=[...]` instead of `context_id="..."`.
        if "context_ids" in updated and "context_id" not in updated:
            context_ids = updated.pop("context_ids")
            if context_ids is None:
                updated["context_id"] = ""
            elif isinstance(context_ids, str):
                updated["context_id"] = context_ids
            else:
                updated["context_id"] = ",".join(str(x) for x in context_ids)

        # Older code passed a single `system_prompt` string.
        if "system_prompt" in updated and "system_prompt_additions" not in updated:
            system_prompt = updated.pop("system_prompt")
            updated["system_prompt_additions"] = (system_prompt,) if system_prompt else ()

        return updated

    def with_capabilities(self, *caps: str) -> ProviderBinding:
        """Add capabilities."""
        return self.model_copy(update={"capabilities": self.capabilities | frozenset(caps)})

    def with_blocked_tools(self, *tools: str) -> ProviderBinding:
        """Add blocked tools."""
        return self.model_copy(update={"blocked_tools": self.blocked_tools | frozenset(tools)})

    def allowed_tools(self, available: frozenset[str] | None = None) -> frozenset[str]:
        """Compute allowed tools from capabilities minus blocked."""
        allowed = tools_for_capabilities(self.capabilities)
        if available is not None:
            allowed = allowed & available
        return allowed - self.blocked_tools

    @staticmethod
    def compose(*bindings: ProviderBinding) -> ProviderBinding:
        """Compose multiple bindings with well-defined merge semantics.

        Can be called with multiple arguments or a single list:
            ProviderBinding.compose(b1, b2)
            ProviderBinding.compose(b1, b2, b3)
        """
        # Handle both ProviderBinding.compose(b1, b2) and ProviderBinding.compose([b1, b2])
        if len(bindings) == 1 and isinstance(bindings[0], list):
            bindings = tuple(bindings[0])
        if not bindings:
            return ProviderBinding()

        # Ordering constants for composition
        trust_order: list[Literal["sandbox", "restricted", "standard", "elevated"]] = [
            "sandbox",
            "restricted",
            "standard",
            "elevated",
        ]
        isolation_order: list[Literal["shared", "forked", "isolated"]] = ["shared", "forked", "isolated"]

        # Collectors
        context_ids: list[str] = []
        descriptions: list[str] = []
        system_additions: list[str] = []
        capability_sets: list[frozenset[str]] = []
        blocked_sets: list[frozenset[str]] = []
        custom_tools: list[ToolDefinition] = []
        mcp_servers: dict[str, dict[str, Any]] = {}  # Merged external MCP servers
        validators: list[ToolValidator] = []
        confirmation_sets: list[frozenset[str]] = []

        cwd: str | None = None
        session_id: str | None = None
        output_format: dict[str, Any] | None = None
        environment: dict[str, str] = {}
        all_visible = True

        # Track min trust and max isolation
        min_trust_idx = len(trust_order) - 1  # Start at most permissive
        max_isolation_idx = 0  # Start at least isolated

        for b in bindings:
            if b.context_id:
                context_ids.append(b.context_id)
            if b.context_description:
                descriptions.append(b.context_description)
            system_additions.extend(b.system_prompt_additions)
            if b.capabilities:
                capability_sets.append(b.capabilities)
            blocked_sets.append(b.blocked_tools)
            custom_tools.extend(b.custom_tools)
            mcp_servers.update(b.mcp_servers)  # Later wins on name conflicts
            if b.validate_tool:
                validators.append(b.validate_tool)
            if b.cwd:
                cwd = b.cwd
            if b.session_id and session_id is None:  # First non-None wins
                session_id = b.session_id
            if b.output_format and output_format is None:  # First non-None wins
                output_format = b.output_format
            environment.update(b.environment)
            all_visible = all_visible and b.visible
            confirmation_sets.append(b.require_confirmation)

            # Trust: most restrictive wins (lower index = more restrictive)
            trust_idx = trust_order.index(b.trust_level)
            min_trust_idx = min(min_trust_idx, trust_idx)

            # Isolation: most isolated wins (higher index = more isolated)
            isolation_idx = isolation_order.index(b.session_isolation)
            max_isolation_idx = max(max_isolation_idx, isolation_idx)

        # Compose capabilities: intersection
        composed_caps: frozenset[str] = frozenset()
        if capability_sets:
            composed_caps = capability_sets[0]
            for s in capability_sets[1:]:
                composed_caps = composed_caps & s

        # Compose blocked: union
        composed_blocked = frozenset().union(*blocked_sets) if blocked_sets else frozenset()

        # Compose confirmations: union
        composed_confirmations = frozenset().union(*confirmation_sets) if confirmation_sets else frozenset()

        # Compose validators: chain
        def chained_validator(tool: ToolCall) -> ValidationResult:
            current = tool
            for v in validators:
                result = v(current)
                if not result.allowed:
                    return result
                current = result.tool
            return ValidationResult.allow(current)

        return ProviderBinding(
            context_id=",".join(context_ids),
            visible=all_visible,
            context_description="\n\n".join(descriptions) if descriptions else None,
            system_prompt_additions=tuple(system_additions),
            capabilities=composed_caps,
            blocked_tools=composed_blocked,
            custom_tools=tuple(custom_tools),
            mcp_servers=mcp_servers,
            validate_tool=chained_validator if validators else None,
            session_id=session_id,
            session_isolation=isolation_order[max_isolation_idx],
            trust_level=trust_order[min_trust_idx],
            require_confirmation=composed_confirmations,
            cwd=cwd,
            environment=environment,
            output_format=output_format,
        )


# =============================================================================
# Execution Result
# =============================================================================


class ExecutionResult(StrictModel):
    """Result from provider execution, passed to capture().

    Contains everything a context might need to capture its state:
    - Whether execution succeeded
    - Text and structured output from the LLM
    - All tool calls and their results
    - Session information
    - Provider-specific metadata

    Serializable via model_dump() / model_validate() for caching.
    """

    success: bool = True
    output_text: str = ""
    # Optional provider signal (e.g., "end_turn"). Not used by core logic, but accepted for compat/debugging.
    stop_reason: str | None = None
    structured_output: dict[str, Any] = Field(default_factory=dict)
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def tool_calls_by_name(self, name: str) -> list[tuple[ToolCall, ToolResult]]:
        """Get all tool calls with a specific name, paired with results."""
        return [
            (call, result)
            for call, result in zip(self.tool_calls, self.tool_results, strict=False)
            if call.name == name
        ]

    def successful_tool_calls(self) -> list[tuple[ToolCall, ToolResult]]:
        """Get all successful tool calls paired with results."""
        return [
            (call, result) for call, result in zip(self.tool_calls, self.tool_results, strict=False) if result.success
        ]


# =============================================================================
# Trace Configuration
# =============================================================================


class TraceConfig(StrictModel):
    """Controls which effects are captured during execution.

    Used by ExecutionLifecycle to filter effect emission. Does NOT affect
    provider behavior - providers always capture internally. This controls
    what gets emitted to the effect stream.

    Usage:
        async with ExecutionLifecycle(
            scope, provider, task_name="my_task",
            trace_config=TraceConfig(capture_thinking=False)
        ) as lifecycle:
            await lifecycle.execute(prompt)

    Attributes:
        capture_thinking: Capture AgentThinking effects (default: True)
        capture_messages: Capture AgentMessage effects (default: True)
        capture_prompts: Capture PromptSent effects (default: False, verbose)
        capture_tool_calls: Capture ToolCallStarted/Completed (default: True)
        capture_tool_results: Include result content in tool effects (default: True)
        capture_context_lifecycle: Capture context lifecycle effects (default: True)
        truncate_content_at: Truncate content longer than this (default: 10000)
    """

    # Agent trace effects
    capture_thinking: bool = True
    capture_messages: bool = True
    capture_prompts: bool = False  # Off by default (verbose)

    # Tool effects
    capture_tool_calls: bool = True
    capture_tool_results: bool = True

    # Context lifecycle effects
    capture_context_lifecycle: bool = True

    # Content truncation (None = no truncation)
    truncate_content_at: int | None = 10000

    @classmethod
    def minimal(cls) -> TraceConfig:
        """Minimal tracing - only task lifecycle effects.

        Use this for production when you only need task start/complete/fail
        effects and don't need the detailed trace of thinking, messages,
        and tool calls.
        """
        return cls(
            capture_thinking=False,
            capture_messages=False,
            capture_prompts=False,
            capture_tool_calls=False,
            capture_tool_results=False,
            capture_context_lifecycle=False,
        )

    @classmethod
    def full(cls) -> TraceConfig:
        """Full tracing - everything including prompts.

        Use this for debugging when you need complete visibility into
        agent execution including the prompts sent to the model.
        """
        return cls(
            capture_thinking=True,
            capture_messages=True,
            capture_prompts=True,
            capture_tool_calls=True,
            capture_tool_results=True,
            capture_context_lifecycle=True,
            truncate_content_at=None,
        )


# =============================================================================
# Transcript Path Computation
# =============================================================================


def compute_transcript_path(cwd: str | Path | None, session_id: str) -> str:
    """Compute Claude Code transcript path for a session.

    Claude Code stores transcripts at:
        ~/.claude/projects/<project-path>/<session_id>.jsonl

    The project path is the absolute cwd with both slashes and underscores
    replaced by dashes (matching Claude CLI behavior).
    Example: /Users/alice/my_project -> -Users-alice-my-project
    """
    if cwd is None:
        cwd = Path.cwd()

    cwd_path = Path(cwd).resolve()

    # Convert: /Users/alice/my_project -> -Users-alice-my-project
    # Claude CLI replaces both / and _ with - (confirmed via spike)
    project_folder = str(cwd_path).replace("/", "-").replace("_", "-")

    transcript_path = Path.home() / ".claude" / "projects" / project_folder / f"{session_id}.jsonl"
    return str(transcript_path)


__all__ = [
    # Capability mapping
    "CAPABILITY_TOOL_MAP",
    "TOOL_CAPABILITY_REQUIREMENTS",
    "CapabilityError",
    # Results
    "ExecutionResult",
    "ExtensibleModel",
    # Errors
    "PreparationError",
    "ProviderBinding",
    # Provider
    "ProviderCapabilities",
    # Reversibility
    "ReversibilityLevel",
    # Base model classes (D43)
    "StrictModel",
    "StrictModelWithCallables",
    # Tool types
    "ToolCall",
    "ToolContext",
    "ToolDefinition",
    "ToolHandler",
    "ToolHandlerWithContext",
    "ToolResult",
    "ToolValidator",
    # Trace config
    "TraceConfig",
    "ValidationResult",
    "capability_for_tool",
    # Transcript path
    "compute_transcript_path",
    "tools_for_capabilities",
]
