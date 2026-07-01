"""MCPServerContext: External MCP server integration with zero-code configuration.

This module provides MCPServerContext, an execution context that wraps external
MCP (Model Context Protocol) servers. It enables easy integration of third-party
MCP servers from https://mcpservers.org/ and elsewhere.

Key Features:
- Zero code for simple servers (just config)
- Multiple transport types: stdio, SSE, HTTP
- Tool access control via allowed_tools, blocked_tools
- Config file loading (YAML, JSON)

Transport Types:
    stdio: Server runs as subprocess, communicates via stdin/stdout
        - command: Executable to run (e.g., "npx", "python")
        - args: Command arguments
        - env: Environment variables

    sse: Server-Sent Events over HTTP
        - url: SSE endpoint URL
        - headers: HTTP headers (e.g., Authorization)

    http: HTTP-based MCP transport
        - url: HTTP endpoint URL
        - headers: HTTP headers

Examples:
    # Simple stdio server
    fs = MCPServerContext(
        name="filesystem",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "/projects"),
    )

    # SSE server with auth
    github = MCPServerContext(
        name="github",
        url="https://mcp.github.com/v1",
        transport_type="sse",
        headers={"Authorization": f"Bearer {token}"},
    )

    # From config file
    servers = MCPServerContext.from_yaml("mcp_servers.yaml")
    for name, server in servers.items():
        scope.bind(name, server)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self

from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
)
from shepherd_runtime.context import BindableContext

from shepherd_contexts.mcp.effects import MCPToolCalled

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shepherd_core.effects import Effect
    from shepherd_runtime.context import Sandbox


@dataclass(frozen=True)
class MCPServerContext(BindableContext):
    """Execution context wrapping an external MCP server.

    For simple MCP servers, just provide config - no code needed.
    For more control, subclass and override configure().

    The MCP server lifecycle is managed by the provider:
    - Provider starts the server (subprocess) or connects (SSE/HTTP)
    - Provider routes tool calls to the server
    - Provider shuts down the server on cleanup

    This context's role is to:
    - Declare the server configuration via ProviderBinding.mcp_servers
    - Apply tool access controls (allowed/blocked)
    - Capture MCP tool calls as effects for audit trail

    Attributes:
        name: Server name, used as key in mcp_servers dict
        description: Human-readable description for LLM prompt
        command: Executable for stdio transport (e.g., "npx", "python")
        args: Command-line arguments for stdio transport
        env: Environment variables for stdio transport
        cwd: Working directory for stdio subprocess
        url: Server URL for SSE/HTTP transport
        headers: HTTP headers for SSE/HTTP transport
        transport_type: One of "stdio", "sse", "http"
        allowed_tools: Whitelist of allowed tools (None = all)
        blocked_tools: Blacklist of blocked tools
        require_confirmation: Tools that need user approval
        trust_level: Abstract trust level for provider translation
        reversibility_level: How reversible are effects from this server
        visible: Whether to include in LLM prompt
    """

    __binding_name__: ClassVar[str] = "mcp"

    # === Server Identity ===
    name: str
    description: str | None = None

    # === Transport: stdio (subprocess) ===
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    # === Transport: SSE/HTTP (remote) ===
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    transport_type: Literal["stdio", "sse", "http"] = "stdio"

    # === Tool Access Control ===
    allowed_tools: frozenset[str] | None = None  # None = all allowed
    blocked_tools: frozenset[str] = field(default_factory=frozenset)
    require_confirmation: frozenset[str] = field(default_factory=frozenset)

    # === Trust & Reversibility ===
    trust_level: Literal["sandbox", "restricted", "standard", "elevated"] = "standard"
    reversibility_level: ReversibilityLevel = ReversibilityLevel.AUTO

    # === Visibility ===
    visible: bool = True

    # === Internal ===
    _frozen_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not self.url and not self.command:
            raise ValueError(f"MCPServerContext '{self.name}' requires either 'command' (stdio) or 'url' (sse/http)")

        if self.url and self.transport_type == "stdio":
            # Auto-detect transport type from url presence
            object.__setattr__(self, "transport_type", "sse")

    # =========================================================================
    # ExecutionContext Protocol
    # =========================================================================

    @property
    def context_id(self) -> str:
        """Stable identifier for effect attribution."""
        if self._frozen_id:
            return self._frozen_id

        if self.url:
            return f"mcp:{self.name}:{self.transport_type}"
        return f"mcp:{self.name}:stdio"

    @property
    def reversibility(self) -> ReversibilityLevel:
        """How reversible are effects from this MCP server?

        Default is AUTO, but servers that perform external actions
        (send emails, publish content) should use COMPENSABLE or NONE.
        """
        return self.reversibility_level

    def __str__(self) -> str:
        """String representation for LLM prompt."""
        if not self.visible:
            return ""
        return self.description or f"MCP server: {self.name}"

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        """Return binding with MCP server config.

        Args:
            capabilities: Provider capabilities (unused for MCP servers)

        Returns:
            ProviderBinding with mcp_servers config
        """
        # Prefix blocked_tools and require_confirmation with MCP tool name pattern
        # so they match the SDK's prefixed names (e.g., "mcp__github__delete_repo")
        prefix = f"mcp__{self.name}__"
        prefixed_blocked = frozenset(f"{prefix}{t}" for t in self.blocked_tools)
        prefixed_confirm = frozenset(f"{prefix}{t}" for t in self.require_confirmation)

        return ProviderBinding(
            context_id=self.context_id,
            context_type="MCPServerContext",
            visible=self.visible,
            context_description=str(self) if self.visible else None,
            mcp_servers={self.name: self._build_transport_config()},
            blocked_tools=prefixed_blocked,
            require_confirmation=prefixed_confirm,
            trust_level=self.trust_level,
            cwd=self.cwd,
        )

    def prepare(self) -> Self:
        """No preparation needed - provider starts MCP server.

        The provider (ClaudeProvider, OpenAIProvider) is responsible for
        actually starting the MCP server based on the config in mcp_servers.
        """
        return self

    # === v2 API ===

    def extract_effects(
        self,
        sandbox: Sandbox | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        """Extract MCP tool call effects from result. PURE.

        MCP servers are external, so effects are for audit trail only.
        No local state changes to derive.

        Args:
            sandbox: Ignored (MCP doesn't use filesystem sandbox)
            result: ExecutionResult containing tool calls

        Returns:
            Sequence of MCPToolCalled effects
        """
        effects: list[Effect] = []

        # Find tool calls that went to this MCP server
        # Tool names follow pattern: mcp__{server_name}__{tool_name}
        prefix = f"mcp__{self.name}__"

        for call, res in zip(result.tool_calls, result.tool_results, strict=False):
            if call.name.startswith(prefix):
                tool_name = call.name[len(prefix) :]
                effects.append(
                    MCPToolCalled(
                        server_name=self.name,
                        tool_name=tool_name,
                        params=call.params,
                        success=res.success,
                        context_id=self.context_id,
                    )
                )

        return effects

    def apply_effect(self, effect: Effect) -> Self:
        """Apply effect to derive new state. PURE.

        MCP servers are external, so there's no local state to derive.
        Effects are for audit trail only.

        Args:
            effect: Effect to apply (ignored)

        Returns:
            Self (unchanged)
        """
        return self

    def cleanup(self, error: Exception | None = None) -> None:
        """No cleanup needed - provider manages MCP server lifecycle."""

    # =========================================================================
    # Transport Configuration
    # =========================================================================

    def _build_transport_config(self) -> dict[str, Any]:
        """Build SDK-compatible MCP server transport config.

        Returns a dictionary suitable for passing to the SDK's mcp_servers
        configuration. Environment variables in the format ${VAR} or
        ${VAR:-default} are expanded.
        """
        if self.url:
            # Remote transport (SSE or HTTP)
            config: dict[str, Any] = {
                "type": self.transport_type,
                "url": self._expand_env_vars(self.url),
            }
            if self.headers:
                config["headers"] = {k: self._expand_env_vars(v) for k, v in self.headers.items()}
        else:
            # stdio transport (subprocess)
            config = {
                "command": self.command,
                "args": list(self.args),
            }
            if self.env:
                config["env"] = {k: self._expand_env_vars(v) for k, v in self.env.items()}

        if self.allowed_tools is not None:
            config["allowed_tools"] = sorted(self.allowed_tools)

        return config

    @staticmethod
    def _expand_env_vars(value: str) -> str:
        """Expand ${VAR} and ${VAR:-default} patterns.

        Examples:
            ${API_KEY} -> value of API_KEY env var, or empty string (with warning)
            ${API_KEY:-default} -> value of API_KEY, or "default" if not set
        """
        import warnings

        def replace(match: re.Match) -> str:
            var = match.group(1)
            default = match.group(2)
            env_value = os.environ.get(var)

            if env_value is None:
                if default is None:
                    # No default provided and env var is missing - warn
                    warnings.warn(
                        f"Environment variable '{var}' is not set and no default provided. Using empty string.",
                        UserWarning,
                        stacklevel=4,
                    )
                    return ""
                # Default provided
                return default
            return env_value

        # Match ${VAR} or ${VAR:-default}
        pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"
        return re.sub(pattern, replace, value)

    # =========================================================================
    # Config File Loading
    # =========================================================================

    @classmethod
    def from_dict(cls, name: str, config: dict[str, Any]) -> MCPServerContext:
        """Create MCPServerContext from a config dictionary.

        Args:
            name: Server name
            config: Configuration dictionary with transport and control options

        Returns:
            Configured MCPServerContext instance

        Example config (stdio):
            {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/projects"],
                "allowed_tools": ["list_directory", "read_file"],
            }

        Example config (sse):
            {
                "url": "https://mcp.example.com/v1",
                "type": "sse",
                "headers": {"Authorization": "Bearer ${TOKEN}"},
                "require_confirmation": ["dangerous_action"],
                "reversibility": "compensable",
            }
        """
        # Determine transport type
        if "url" in config:
            transport = config.get("type", config.get("transport_type", "sse"))
            return cls(
                name=name,
                description=config.get("description"),
                url=config["url"],
                headers=config.get("headers", {}),
                transport_type=transport,
                allowed_tools=(frozenset(config["allowed_tools"]) if "allowed_tools" in config else None),
                blocked_tools=frozenset(config.get("blocked_tools", [])),
                require_confirmation=frozenset(config.get("require_confirmation", [])),
                trust_level=config.get("trust_level", "standard"),
                reversibility_level=cls._parse_reversibility(config.get("reversibility", "auto")),
                visible=config.get("visible", True),
            )
        # stdio transport
        return cls(
            name=name,
            description=config.get("description"),
            command=config.get("command"),
            args=tuple(config.get("args", [])),
            env=config.get("env", {}),
            cwd=config.get("cwd"),
            allowed_tools=(frozenset(config["allowed_tools"]) if "allowed_tools" in config else None),
            blocked_tools=frozenset(config.get("blocked_tools", [])),
            require_confirmation=frozenset(config.get("require_confirmation", [])),
            trust_level=config.get("trust_level", "standard"),
            reversibility_level=cls._parse_reversibility(config.get("reversibility", "auto")),
            visible=config.get("visible", True),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> dict[str, MCPServerContext]:
        """Load multiple MCP server contexts from a YAML file.

        Args:
            path: Path to YAML configuration file

        Returns:
            Dictionary mapping server names to MCPServerContext instances

        YAML format:
            servers:
              filesystem:
                command: npx
                args: ["-y", "@modelcontextprotocol/server-filesystem", "/projects"]
                allowed_tools: [list_directory, read_file]

              github:
                url: https://mcp.github.com/v1
                type: sse
                headers:
                  Authorization: "Bearer ${GITHUB_TOKEN}"
                require_confirmation: [create_pull_request]
                reversibility: compensable

        Alternative format (without 'servers' wrapper):
            filesystem:
              command: npx
              args: ["-y", "@modelcontextprotocol/server-filesystem"]
        """
        try:
            import yaml
        except ImportError:
            raise ImportError("PyYAML is required for YAML config loading. Install with: pip install pyyaml") from None

        with open(path) as f:
            data = yaml.safe_load(f)

        # Support both {servers: {...}} and {...} formats
        servers = data.get("servers", data)
        return {name: cls.from_dict(name, config) for name, config in servers.items()}

    @classmethod
    def from_json(cls, path: str | Path) -> dict[str, MCPServerContext]:
        """Load multiple MCP server contexts from a JSON file.

        Args:
            path: Path to JSON configuration file

        Returns:
            Dictionary mapping server names to MCPServerContext instances

        JSON format is the same as YAML (see from_yaml docstring).
        """
        import json

        with open(path) as f:
            data = json.load(f)

        # Support both {servers: {...}} and {...} formats
        servers = data.get("servers", data)
        return {name: cls.from_dict(name, config) for name, config in servers.items()}

    @staticmethod
    def _parse_reversibility(value: str) -> ReversibilityLevel:
        """Parse reversibility level from string.

        Args:
            value: One of "auto", "compensable", "none" (case-insensitive)

        Returns:
            Corresponding ReversibilityLevel enum value
        """
        mapping = {
            "auto": ReversibilityLevel.AUTO,
            "compensable": ReversibilityLevel.COMPENSABLE,
            "none": ReversibilityLevel.NONE,
        }
        return mapping.get(value.lower(), ReversibilityLevel.AUTO)


__all__ = ["MCPServerContext"]
