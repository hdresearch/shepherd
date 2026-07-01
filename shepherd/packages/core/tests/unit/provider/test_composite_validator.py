"""Tests for Provider._build_composite_validator with binding_name attribution.

These tests verify that ToolCallRejected effects include binding_name for
audit trail purposes when tool calls are rejected.
"""

import pytest
from shepherd_core.effects import ToolCallRejected
from shepherd_core.foundation.protocols import EffectProtocol
from shepherd_core.provider import DefaultProviderRuntime, Provider, ProviderRuntime
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ToolCall,
    ValidationResult,
)

# =============================================================================
# Test Provider Implementation
# =============================================================================


class MockProvider(Provider):
    """Minimal provider implementation for testing."""

    @property
    def provider_id(self) -> str:
        return "provider:mock:test"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="mock",
            supports_streaming=False,
            supports_tools=True,
            supports_structured_output=False,
            supports_session=False,
            available_tools=frozenset({"Read", "Write", "Bash"}),
        )

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
    ) -> ExecutionResult:
        # Not needed for validator tests
        return ExecutionResult(
            success=True,
            output_text="mock",
            tool_calls=(),
            tool_results=(),
        )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def provider() -> MockProvider:
    """Create a mock provider for testing."""
    return MockProvider()


class RecordingEmitter:
    """Minimal effect sink for provider validator tests."""

    def __init__(self) -> None:
        self._effects: list[EffectProtocol] = []

    def emit(self, effect: EffectProtocol) -> None:
        self._effects.append(effect)

    @property
    def effects(self) -> tuple[EffectProtocol, ...]:
        return tuple(self._effects)

    def rejected_effects(self) -> list[ToolCallRejected]:
        return [effect for effect in self._effects if isinstance(effect, ToolCallRejected)]

    def query_by_binding_name(self, binding_name: str) -> list[EffectProtocol]:
        return [effect for effect in self._effects if effect.binding_name == binding_name]


@pytest.fixture
def emitter() -> RecordingEmitter:
    """Create a lightweight effect sink for testing."""
    return RecordingEmitter()


# =============================================================================
# Test: binding_name on capability check rejection
# =============================================================================


class TestCapabilityCheckRejection:
    """Tests for ToolCallRejected with binding_name on capability check failures."""

    def test_capability_rejection_includes_binding_name(self, provider: MockProvider, emitter: RecordingEmitter):
        """ToolCallRejected from capability check includes binding_name."""
        binding = ProviderBinding(
            context_id="workspace,session",
            capabilities=frozenset({"read"}),  # Only read, not bash
        )
        binding_name = "workspace,session"

        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="TestTask"),
            binding_name=binding_name,
        )

        # Try to use Bash which requires 'bash' capability
        tool_call = ToolCall(id="tc_001", name="Bash", params={"command": "ls"})
        result = validator(tool_call)

        assert not result.allowed

        # Verify the emitted effect has binding_name
        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 1

        rejected = rejected_effects[0]
        assert rejected.binding_name == "workspace,session"
        assert rejected.rejected_by == "capability_check"
        assert rejected.tool_name == "Bash"

    def test_capability_rejection_without_binding_name(self, provider: MockProvider, emitter: RecordingEmitter):
        """ToolCallRejected without binding_name when not provided."""
        binding = ProviderBinding(
            context_id="workspace",
            capabilities=frozenset({"read"}),
        )

        # No binding_name passed
        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="TestTask"),
        )

        tool_call = ToolCall(id="tc_002", name="Bash", params={"command": "ls"})
        validator(tool_call)

        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 1
        assert rejected_effects[0].binding_name is None


# =============================================================================
# Test: binding_name on blocked tools rejection
# =============================================================================


class TestBlockedToolsRejection:
    """Tests for ToolCallRejected with binding_name on blocked tools."""

    def test_blocked_tools_rejection_includes_binding_name(self, provider: MockProvider, emitter: RecordingEmitter):
        """ToolCallRejected from blocked_tools includes binding_name."""
        binding = ProviderBinding(
            context_id="secure_context",
            # Include 'write' capability so we get past capability check
            # but block the Write tool explicitly
            capabilities=frozenset({"read", "write", "bash"}),
            blocked_tools=frozenset({"Write", "Bash"}),
        )
        binding_name = "secure_context"

        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="SecureTask"),
            binding_name=binding_name,
        )

        tool_call = ToolCall(id="tc_003", name="Write", params={"path": "/etc/passwd"})
        result = validator(tool_call)

        assert not result.allowed

        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 1

        rejected = rejected_effects[0]
        assert rejected.binding_name == "secure_context"
        assert rejected.rejected_by == "blocked_tools"
        assert rejected.tool_name == "Write"
        assert rejected.reason == "Tool is blocked"


# =============================================================================
# Test: binding_name on custom validator rejection
# =============================================================================


class TestCustomValidatorRejection:
    """Tests for ToolCallRejected with binding_name on custom validator failures."""

    def test_custom_validator_rejection_includes_binding_name(self, provider: MockProvider, emitter: RecordingEmitter):
        """ToolCallRejected from custom validator includes binding_name."""

        def reject_all(tool: ToolCall) -> ValidationResult:
            return ValidationResult.reject(tool, "Custom rejection reason")

        binding = ProviderBinding(
            context_id="custom_validated",
            validate_tool=reject_all,
        )
        binding_name = "custom_validated"

        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="CustomTask"),
            binding_name=binding_name,
        )

        tool_call = ToolCall(id="tc_004", name="Read", params={"path": "/file.txt"})
        result = validator(tool_call)

        assert not result.allowed

        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 1

        rejected = rejected_effects[0]
        assert rejected.binding_name == "custom_validated"
        assert rejected.rejected_by == "custom_validator"
        assert rejected.reason == "Custom rejection reason"

    def test_custom_validator_exception_includes_binding_name(self, provider: MockProvider, emitter: RecordingEmitter):
        """ToolCallRejected from validator exception includes binding_name."""

        def raise_error(tool: ToolCall) -> ValidationResult:
            raise ValueError("Validator exploded")

        binding = ProviderBinding(
            context_id="error_validator",
            validate_tool=raise_error,
        )
        binding_name = "error_validator"

        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="ErrorTask"),
            binding_name=binding_name,
        )

        tool_call = ToolCall(id="tc_005", name="Read", params={"path": "/file.txt"})
        result = validator(tool_call)

        assert not result.allowed

        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 1

        rejected = rejected_effects[0]
        assert rejected.binding_name == "error_validator"
        assert rejected.rejected_by == "custom_validator"
        assert "ValueError" in rejected.reason
        assert "Validator exploded" in rejected.reason


# =============================================================================
# Test: composite binding_name (multiple contexts)
# =============================================================================


class TestCompositeBindingName:
    """Tests for composite binding_name from multiple composed bindings."""

    def test_composite_binding_name_preserved(self, provider: MockProvider, emitter: RecordingEmitter):
        """Composite binding_name with multiple context_ids is preserved."""
        # Simulate a composed binding from multiple contexts
        binding = ProviderBinding(
            context_id="workspace,session,security",
            blocked_tools=frozenset({"Bash"}),
        )
        # The composite binding_name matches context_id for composed bindings
        composite_name = "workspace,session,security"

        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="CompositeTask"),
            binding_name=composite_name,
        )

        tool_call = ToolCall(id="tc_006", name="Bash", params={"command": "rm -rf /"})
        validator(tool_call)

        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 1

        rejected = rejected_effects[0]
        assert rejected.binding_name == "workspace,session,security"

    def test_query_by_binding_name(self, provider: MockProvider, emitter: RecordingEmitter):
        """Effects can be queried by binding_name."""
        binding = ProviderBinding(
            context_id="queryable",
            blocked_tools=frozenset({"Write"}),
        )

        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="QueryTask"),
            binding_name="queryable",
        )

        tool_call = ToolCall(id="tc_007", name="Write", params={"path": "/file"})
        validator(tool_call)

        # Query effects by binding_name
        effects_by_binding = emitter.query_by_binding_name("queryable")
        assert len(effects_by_binding) == 1

        # Query by different binding_name returns nothing
        effects_other = emitter.query_by_binding_name("other")
        assert len(effects_other) == 0


# =============================================================================
# Test: allowed tool calls don't emit rejections
# =============================================================================


class TestAllowedToolCalls:
    """Tests that allowed tool calls don't emit ToolCallRejected."""

    def test_allowed_tool_no_rejection_effect(self, provider: MockProvider, emitter: RecordingEmitter):
        """Allowed tool calls don't emit ToolCallRejected effects."""
        binding = ProviderBinding(
            context_id="permissive",
            capabilities=frozenset({"read", "write", "bash"}),
        )

        validator = provider._build_composite_validator(
            binding,
            DefaultProviderRuntime.from_emitter(emitter, task_name="AllowTask"),
            binding_name="permissive",
        )

        tool_call = ToolCall(id="tc_008", name="Read", params={"path": "/file.txt"})
        result = validator(tool_call)

        assert result.allowed

        # No rejection effects should be emitted
        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 0

    def test_none_binding_allows_all(self, provider: MockProvider, emitter: RecordingEmitter):
        """None binding creates permissive validator."""
        validator = provider._build_composite_validator(
            None,
            DefaultProviderRuntime.from_emitter(emitter, task_name="NoBindingTask"),
        )

        tool_call = ToolCall(id="tc_009", name="Bash", params={"command": "anything"})
        result = validator(tool_call)

        assert result.allowed
        rejected_effects = emitter.rejected_effects()
        assert len(rejected_effects) == 0
