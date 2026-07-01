"""Provider: Layer 3 of the three-layer model.

This module defines:
- Provider: Abstract base for SDK adapters

Three-Layer Model
-----------------
- Layer 1 (Scope): Resource container - owns bindings, providers, stream
- Layer 2 (ExecutionLifecycle): Orchestrates configure/prepare/capture/cleanup
- Layer 3 (Provider): Translates binding to SDK config, executes

Provider Responsibilities:
1. Declare capabilities (what SDK supports)
2. Translate ProviderBinding to SDK-specific config
3. Execute via SDK and return ExecutionResult
4. Build composite validators from binding
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..effects import (
    ToolCallRejected,
)
from ..types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ValidationResult,
    capability_for_tool,
)

if TYPE_CHECKING:
    from .runtime import ProviderRuntime


# =============================================================================
# Layer 3: Provider Protocol
# =============================================================================


class Provider(ABC):
    """Layer 3: Abstract base for execution providers.

    Providers are responsible for:
    1. Declaring capabilities (what the SDK supports)
    2. Translating ProviderBinding to SDK-specific config
    3. Executing via SDK and returning ExecutionResult
    4. Building composite validators from binding

    Providers are NOT responsible for:
    - Lifecycle orchestration (that's ExecutionLifecycle)
    - Calling configure/prepare/capture/cleanup on contexts
    - Effect emission for lifecycle events

    Subclasses implement:
    - provider_id: Unique identifier
    - capabilities: What this provider supports
    - _translate_binding(): Convert ProviderBinding to SDK config
    - execute_sdk(): Actually run the LLM
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider instance."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """What this provider supports."""
        ...

    @property
    def formatter(self) -> Any:
        """Return the verbose formatter if verbose output is enabled.

        Default implementation returns None (no verbose output).
        Subclasses that support verbose output should override this.

        Returns:
            VerboseFormatter instance or None
        """
        return None

    @abstractmethod
    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        hooks: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute via SDK and return result.

        This method:
        1. Translates binding to SDK-specific config
        2. Builds validator from binding
        3. Executes via SDK
        4. Emits tool call effects
        5. Returns ExecutionResult

        Args:
            prompt: The prompt to send to the LLM
            binding: Composed ProviderBinding from contexts
            runtime: Minimal execution runtime for effect emission and task attribution
            hooks: Optional provider-specific hook configuration. For
                ClaudeProvider, this is a dict matching the Claude Agent SDK
                hooks format (PreToolUse/PostToolUse callbacks). Other
                providers accept but ignore this parameter.

        Returns:
            ExecutionResult with outputs and tool calls
        """
        ...

    def validate_binding(self, binding: ProviderBinding) -> None:
        """Validate that this provider can satisfy the binding requirements.

        Called by ExecutionLifecycle during configure phase, BEFORE any
        prepare() calls. This allows early failure with clear error messages
        about what the provider cannot support.

        Since this is called during configure (a pure phase), no cleanup
        is needed if validation fails.

        Args:
            binding: Composed binding from all contexts

        Raises:
            BindingValidationError: If provider cannot satisfy requirements

        Default implementation: Accept any binding (no validation).
        Override in subclasses for provider-specific validation.
        """
        return

    def _build_composite_validator(
        self,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        binding_name: str | None = None,
    ) -> Any:
        """Build a validator that checks capabilities and custom validation."""
        if binding is None:
            return ValidationResult.allow

        task_name = runtime.task_name

        def validate(tool: ToolCall) -> ValidationResult:
            # Check capability requirements
            required_cap = capability_for_tool(tool.name)
            if required_cap and required_cap not in binding.capabilities:
                runtime.effects.emit(
                    ToolCallRejected(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        tool_call_id=tool.id,
                        tool_name=tool.name,
                        reason=f"Requires capability '{required_cap}'",
                        rejected_by="capability_check",
                        binding_name=binding_name,
                    )
                )
                return ValidationResult.reject(tool, f"Tool '{tool.name}' requires capability '{required_cap}'")

            # Check blocked tools
            if tool.name in binding.blocked_tools:
                runtime.effects.emit(
                    ToolCallRejected(
                        task_name=task_name,
                        provider_id=self.provider_id,
                        tool_call_id=tool.id,
                        tool_name=tool.name,
                        reason="Tool is blocked",
                        rejected_by="blocked_tools",
                        binding_name=binding_name,
                    )
                )
                return ValidationResult.reject(tool, f"Tool '{tool.name}' is blocked")

            # Run custom validator if present
            if binding.validate_tool:
                try:
                    result = binding.validate_tool(tool)
                except Exception as e:  # noqa: BLE001
                    # Validator raised an exception - treat as rejection
                    runtime.effects.emit(
                        ToolCallRejected(
                            task_name=task_name,
                            provider_id=self.provider_id,
                            tool_call_id=tool.id,
                            tool_name=tool.name,
                            reason=f"Validator error: {type(e).__name__}: {e}",
                            rejected_by="custom_validator",
                            binding_name=binding_name,
                        )
                    )
                    return ValidationResult.reject(tool, f"Validator raised {type(e).__name__}: {e}")
                if not result.allowed:
                    runtime.effects.emit(
                        ToolCallRejected(
                            task_name=task_name,
                            provider_id=self.provider_id,
                            tool_call_id=tool.id,
                            tool_name=tool.name,
                            reason=result.rejection_reason or "Rejected by validator",
                            rejected_by="custom_validator",
                            binding_name=binding_name,
                        )
                    )
                return result

            return ValidationResult.allow(tool)

        return validate

    def _translate_binding(self, binding: ProviderBinding | None) -> dict[str, Any]:
        """Translate ProviderBinding to SDK-specific config.

        Subclasses should override this to produce their SDK's config format.
        """
        return {}

    async def _invoke_tool_handler(
        self,
        tool_def: ToolDefinition,
        tool_call: ToolCall,
        context_id: str,
    ) -> Any:
        """Invoke a tool handler with proper context injection and error handling.

        This shared method handles custom tool invocation for all providers.
        It is called when the SDK routes a custom tool call back to us.

        Handles:
        - Context injection (if tool_def.inject_context is True)
        - Async vs sync handler dispatch
        - Error handling via tool_def.error_handler

        Args:
            tool_def: The ToolDefinition for the tool being invoked.
            tool_call: The ToolCall with id, name, and params.
            context_id: The context ID for building ToolContext.

        Returns:
            The result from the tool handler.

        Raises:
            ValueError: If the tool has no handler defined.
            Exception: Re-raises handler exceptions if no error_handler is set.
        """
        args = tool_call.params

        # Build context if needed
        tool_context = ToolContext(
            context_id=context_id,
            tool_name=tool_def.name,
            tool_call_id=tool_call.id,
        )

        try:
            # Choose handler (prefer async)
            if tool_def.async_handler:
                if tool_def.inject_context:
                    result = await tool_def.async_handler(tool_context, args)  # type: ignore[arg-type, call-arg]
                else:
                    result = await tool_def.async_handler(args)  # type: ignore[arg-type, call-arg]
            elif tool_def.handler:
                result = tool_def.handler(tool_context, args) if tool_def.inject_context else tool_def.handler(args)  # type: ignore[arg-type, call-arg]
            else:
                raise ValueError(f"Tool {tool_def.name} has no handler")

            return result

        except Exception as e:
            if tool_def.error_handler:
                # Convert to error message instead of raising
                return {"error": tool_def.error_handler(e)}
            raise


__all__ = [
    "Provider",
]
