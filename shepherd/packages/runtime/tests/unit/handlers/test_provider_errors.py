"""Tests for provider error paths.

This module tests error conditions in Provider operations:
- SDK unavailability scenarios
- Malformed responses
- Validator errors
"""

from collections.abc import Callable

import pytest
from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect
from shepherd_core.errors import ExecutionError
from shepherd_core.provider import DefaultProviderRuntime, Provider, ProviderRuntime
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ToolCall,
    ValidationResult,
)
from shepherd_runtime._lifecycle import ExecutePhase, PhaseContext
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Contexts
# =============================================================================


class MinimalContext(ExecutionContext):
    """Minimal context for provider testing."""

    def __init__(self, name: str = "minimal"):
        self._name = name

    @property
    def context_id(self) -> str:
        return f"minimal:{self._name}"

    def configure(self, capabilities: ProviderCapabilities) -> ProviderBinding:
        return ProviderBinding()

    def prepare(self) -> "MinimalContext":
        return self

    def cleanup(self, error=None) -> None:
        pass

    def apply_effect(self, effect: Effect) -> "MinimalContext":
        return self


# =============================================================================
# Test Providers
# =============================================================================


class MockTestProvider(Provider):
    """Mock provider implementation for error testing."""

    def __init__(
        self,
        *,
        should_return_none: bool = False,
        should_return_wrong_type: bool = False,
        should_raise: Exception | None = None,
        custom_validator: Callable | None = None,
    ):
        self._should_return_none = should_return_none
        self._should_return_wrong_type = should_return_wrong_type
        self._should_raise = should_raise
        self._custom_validator = custom_validator

    @property
    def provider_id(self) -> str:
        return "test:provider"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
    ) -> ExecutionResult:
        if self._should_raise:
            raise self._should_raise

        if self._should_return_none:
            return None  # type: ignore

        if self._should_return_wrong_type:
            return "wrong_type"  # type: ignore

        return ExecutionResult(
            output_text="Test output",
            stop_reason="end_turn",
        )


# =============================================================================
# Tests: SDK Unavailability
# =============================================================================


class TestSDKUnavailability:
    """Tests for SDK unavailability scenarios."""

    @pytest.mark.asyncio
    async def test_provider_returns_none_raises_execution_error(self):
        """Provider returning None should raise ExecutionError.

        This tests the phase validation in ExecutePhase that checks
        the provider's return value.
        """
        provider = MockTestProvider(should_return_none=True)

        with Scope() as scope:
            ctx = MinimalContext()
            scope.bind("ctx", ctx)
            scope.register_provider("default", provider, default=True)

            # Create phase context
            binding = scope.get_binding("ctx")
            phase_ctx = PhaseContext(
                scope=scope,
                provider=provider,
                task_name="test_task",
                bindings=(binding,),
                prompt="Test prompt",
                composed_binding=ProviderBinding(),
            )

            execute_phase = ExecutePhase()

            with pytest.raises(ExecutionError, match="returned None"):
                await execute_phase.execute(phase_ctx)

    @pytest.mark.asyncio
    async def test_provider_returns_wrong_type_raises_execution_error(self):
        """Provider returning wrong type should raise ExecutionError."""
        provider = MockTestProvider(should_return_wrong_type=True)

        with Scope() as scope:
            ctx = MinimalContext()
            scope.bind("ctx", ctx)
            scope.register_provider("default", provider, default=True)

            binding = scope.get_binding("ctx")
            phase_ctx = PhaseContext(
                scope=scope,
                provider=provider,
                task_name="test_task",
                bindings=(binding,),
                prompt="Test prompt",
                composed_binding=ProviderBinding(),
            )

            execute_phase = ExecutePhase()

            with pytest.raises(ExecutionError, match="instead of ExecutionResult"):
                await execute_phase.execute(phase_ctx)

    @pytest.mark.asyncio
    async def test_provider_raises_exception_propagates(self):
        """Provider raising exception should propagate through execute phase."""
        provider = MockTestProvider(should_raise=RuntimeError("SDK error"))

        with Scope() as scope:
            ctx = MinimalContext()
            scope.bind("ctx", ctx)
            scope.register_provider("default", provider, default=True)

            binding = scope.get_binding("ctx")
            phase_ctx = PhaseContext(
                scope=scope,
                provider=provider,
                task_name="test_task",
                bindings=(binding,),
                prompt="Test prompt",
                composed_binding=ProviderBinding(),
            )

            execute_phase = ExecutePhase()

            with pytest.raises(RuntimeError, match="SDK error"):
                await execute_phase.execute(phase_ctx)


# =============================================================================
# Tests: Malformed Responses
# =============================================================================


class TestMalformedResponses:
    """Tests for handling malformed provider responses."""

    def test_execution_result_missing_fields_uses_defaults(self):
        """ExecutionResult with missing optional fields should use defaults."""
        result = ExecutionResult(
            output_text="test",
            stop_reason="end_turn",
        )

        # Check defaults
        assert result.tool_calls == ()
        assert result.session_id is None
        assert result.metadata == {}

    def test_execution_result_empty_output_is_valid(self):
        """Empty output text is valid (provider may have only made tool calls)."""
        result = ExecutionResult(
            output_text="",
            stop_reason="end_turn",
            tool_calls=(ToolCall(id="tc1", name="test_tool", input={}),),
        )

        assert result.output_text == ""
        assert len(result.tool_calls) == 1


# =============================================================================
# Tests: Validator Errors
# =============================================================================


class TestValidatorErrors:
    """Tests for validator error handling in provider."""

    def test_validator_exception_treated_as_rejection(self):
        """If custom validator raises, tool call should be rejected."""

        def bad_validator(tool: ToolCall) -> ValidationResult:
            raise ValueError("Validator crashed")

        provider = MockTestProvider()
        binding = ProviderBinding(validate_tool=bad_validator)

        with Scope() as scope:
            validator = provider._build_composite_validator(
                binding,
                DefaultProviderRuntime.from_emitter(scope, task_name="test_task"),
            )

            tool = ToolCall(id="tc1", name="test_tool", input={})
            result = validator(tool)

            assert not result.allowed
            assert "Validator raised" in result.rejection_reason

    def test_validator_rejection_emits_effect(self):
        """Validator rejection should emit ToolCallRejected effect."""

        def rejecting_validator(tool: ToolCall) -> ValidationResult:
            return ValidationResult.reject(tool, "Not allowed")

        provider = MockTestProvider()
        binding = ProviderBinding(validate_tool=rejecting_validator)

        with Scope() as scope:
            validator = provider._build_composite_validator(
                binding,
                DefaultProviderRuntime.from_emitter(scope, task_name="test_task"),
            )

            tool = ToolCall(id="tc1", name="test_tool", input={})
            result = validator(tool)

            assert not result.allowed

            # Check effect was emitted
            from shepherd_core.effects import ToolCallRejected

            rejected_effects = list(scope.effects.query(ToolCallRejected))
            assert len(rejected_effects) == 1
            assert rejected_effects[0].effect.tool_name == "test_tool"

    def test_blocked_tool_rejection_emits_effect(self):
        """Blocked tool should emit ToolCallRejected effect."""
        provider = MockTestProvider()
        binding = ProviderBinding(blocked_tools=frozenset({"blocked_tool"}))

        with Scope() as scope:
            validator = provider._build_composite_validator(
                binding,
                DefaultProviderRuntime.from_emitter(scope, task_name="test_task"),
            )

            tool = ToolCall(id="tc1", name="blocked_tool", input={})
            result = validator(tool)

            assert not result.allowed
            assert "blocked" in result.rejection_reason.lower()

            from shepherd_core.effects import ToolCallRejected

            rejected_effects = list(scope.effects.query(ToolCallRejected))
            assert len(rejected_effects) == 1
            assert rejected_effects[0].effect.rejected_by == "blocked_tools"

    def test_missing_capability_rejection_emits_effect(self):
        """Tool requiring missing capability should emit rejection effect.

        Tools like 'bash' require the 'bash' capability which is checked
        via capability_for_tool() mapping.
        """
        provider = MockTestProvider()
        # Binding with limited capabilities - no bash capability
        binding = ProviderBinding(capabilities=frozenset())

        with Scope() as scope:
            validator = provider._build_composite_validator(
                binding,
                DefaultProviderRuntime.from_emitter(scope, task_name="test_task"),
            )

            # Tool that requires bash capability (per TOOL_CAPABILITY_REQUIREMENTS)
            # The 'Bash' tool (capital B) maps to the 'bash' capability
            tool = ToolCall(id="tc1", name="Bash", input={"command": "ls"})
            result = validator(tool)

            # Bash requires 'bash' capability which is not in the binding
            assert not result.allowed

            from shepherd_core.effects import ToolCallRejected

            rejected_effects = list(scope.effects.query(ToolCallRejected))
            assert len(rejected_effects) == 1
            assert rejected_effects[0].effect.rejected_by == "capability_check"

    def test_allowed_tool_passes_validator(self):
        """Tool that passes all checks should be allowed."""

        def allowing_validator(tool: ToolCall) -> ValidationResult:
            return ValidationResult.allow(tool)

        provider = MockTestProvider()
        binding = ProviderBinding(validate_tool=allowing_validator)

        with Scope() as scope:
            validator = provider._build_composite_validator(
                binding,
                DefaultProviderRuntime.from_emitter(scope, task_name="test_task"),
            )

            tool = ToolCall(id="tc1", name="test_tool", input={})
            result = validator(tool)

            assert result.allowed

            # No rejection effects
            from shepherd_core.effects import ToolCallRejected

            rejected_effects = list(scope.effects.query(ToolCallRejected))
            assert len(rejected_effects) == 0

    def test_no_binding_allows_all_tools(self):
        """When binding is None, all tools should be allowed."""
        provider = MockTestProvider()

        with Scope() as scope:
            validator = provider._build_composite_validator(
                None,
                DefaultProviderRuntime.from_emitter(scope, task_name="test_task"),
            )

            tool = ToolCall(id="tc1", name="any_tool", input={})
            result = validator(tool)

            assert result.allowed


# =============================================================================
# Tests: Binding Validation
# =============================================================================


class TestBindingValidation:
    """Tests for provider binding validation."""

    def test_default_validate_binding_accepts_all(self):
        """Default validate_binding() should accept any binding."""
        provider = MockTestProvider()

        # Should not raise for any binding
        provider.validate_binding(ProviderBinding())
        provider.validate_binding(
            ProviderBinding(
                system_prompt="custom",
                blocked_tools=frozenset({"tool"}),
            )
        )

    def test_custom_validate_binding_can_reject(self):
        """Custom validate_binding implementation can reject bindings."""
        from shepherd_core.errors import BindingValidationError

        class StrictProvider(MockTestProvider):
            def validate_binding(self, binding: ProviderBinding) -> None:
                # Check total length of all system prompt additions
                total_len = sum(len(s) for s in binding.system_prompt_additions)
                if total_len > 100:
                    raise BindingValidationError(
                        context_id="test",
                        unsatisfied_requirements=["System prompt too long (max 100 chars)"],
                    )

        provider = StrictProvider()

        # Short prompt - should pass
        provider.validate_binding(ProviderBinding(system_prompt_additions=("Short",)))

        # Long prompt - should fail
        with pytest.raises(BindingValidationError, match="too long"):
            provider.validate_binding(ProviderBinding(system_prompt_additions=("x" * 101,)))


# =============================================================================
# Tests: Provider State Errors
# =============================================================================


class TestProviderStateErrors:
    """Tests for provider state-related errors."""

    @pytest.mark.asyncio
    async def test_provider_with_no_capabilities_still_works(self):
        """Provider with empty capabilities should still execute."""

        class EmptyCapabilitiesProvider(Provider):
            @property
            def provider_id(self) -> str:
                return "test:empty_caps"

            @property
            def capabilities(self) -> ProviderCapabilities:
                return ProviderCapabilities()

            async def execute_sdk(
                self,
                prompt: str,
                binding: ProviderBinding | None,
                runtime: ProviderRuntime,
            ) -> ExecutionResult:
                return ExecutionResult(
                    output_text="Output",
                    stop_reason="end_turn",
                )

        provider = EmptyCapabilitiesProvider()

        with Scope() as scope:
            ctx = MinimalContext()
            scope.bind("ctx", ctx)
            scope.register_provider("default", provider, default=True)
            result = await provider.execute_sdk(
                "Test",
                ProviderBinding(),
                DefaultProviderRuntime.from_emitter(scope, task_name="test_task"),
            )

            assert result.output_text == "Output"
