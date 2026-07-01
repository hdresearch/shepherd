"""Tests for PhaseContext.

Covers:
- CleanupError construction and string representation
- PhaseContext creation with required fields
- Phase output update methods (with_*)
- Cleanup state tracking methods
- Computed properties
- Immutability guarantees
"""

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest
from shepherd_core.types import ExecutionResult, ProviderBinding
from shepherd_runtime._lifecycle import CleanupError, PhaseContext

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_scope() -> MagicMock:
    """Create a mock scope."""
    scope = MagicMock()
    scope.emit = MagicMock()
    return scope


@pytest.fixture
def mock_provider() -> MagicMock:
    """Create a mock provider."""
    provider = MagicMock()
    provider.provider_id = "test-provider"
    provider.capabilities = MagicMock()
    return provider


@pytest.fixture
def mock_binding() -> MagicMock:
    """Create a mock binding with state."""
    binding = MagicMock()
    binding.name = "workspace"
    binding.context = MagicMock()
    binding.context.context_id = "ctx-123"
    return binding


@pytest.fixture
def basic_context(
    mock_scope: MagicMock,
    mock_provider: MagicMock,
    mock_binding: MagicMock,
) -> PhaseContext:
    """Create a basic PhaseContext for testing."""
    return PhaseContext(
        scope=mock_scope,
        provider=mock_provider,
        task_name="test-task",
        prompt="Test prompt",
        bindings=(mock_binding,),
    )


# =============================================================================
# Tests: CleanupError
# =============================================================================


class TestCleanupError:
    """Tests for CleanupError dataclass."""

    def test_cleanup_error_creation(self) -> None:
        """CleanupError should store resource name and exception."""
        exc = RuntimeError("Connection lost")
        error = CleanupError(resource_name="context:workspace", exception=exc)

        assert error.resource_name == "context:workspace"
        assert error.exception is exc

    def test_cleanup_error_str(self) -> None:
        """CleanupError.__str__ should format nicely."""
        exc = RuntimeError("Connection lost")
        error = CleanupError(resource_name="context:workspace", exception=exc)

        assert str(error) == "context:workspace: Connection lost"

    def test_cleanup_error_repr(self) -> None:
        """CleanupError.__repr__ should be detailed."""
        exc = RuntimeError("Connection lost")
        error = CleanupError(resource_name="sandbox:db", exception=exc)

        repr_str = repr(error)
        assert "CleanupError" in repr_str
        assert "sandbox:db" in repr_str
        assert "RuntimeError" in repr_str

    def test_cleanup_error_is_frozen(self) -> None:
        """CleanupError should be immutable."""
        error = CleanupError(
            resource_name="context:workspace",
            exception=RuntimeError("test"),
        )

        with pytest.raises(FrozenInstanceError):
            error.resource_name = "other"  # type: ignore[misc]


# =============================================================================
# Tests: PhaseContext Creation
# =============================================================================


class TestPhaseContextCreation:
    """Tests for PhaseContext creation and defaults."""

    def test_create_with_required_fields(self, mock_scope: MagicMock, mock_provider: MagicMock) -> None:
        """PhaseContext should be creatable with required fields."""
        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="my-task",
        )

        assert ctx.scope is mock_scope
        assert ctx.provider is mock_provider
        assert ctx.task_name == "my-task"

    def test_default_values(self, mock_scope: MagicMock, mock_provider: MagicMock) -> None:
        """PhaseContext should have sensible defaults."""
        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="my-task",
        )

        # Initial defaults
        assert ctx.prompt == ""
        assert ctx.bindings == ()
        assert ctx.artifact_markers == {}
        assert ctx.output_format is None

        # Phase output defaults
        assert ctx.composed_binding is None
        assert ctx.prepared_contexts == {}
        assert ctx.sandboxes == {}
        assert ctx.result is None
        assert ctx.artifact_outputs == {}
        assert ctx.artifact_effects == ()
        assert ctx.extracted_effects == ()
        assert ctx.context_effects == {}
        assert ctx.context_outputs == {}

        # Error/cleanup defaults
        assert ctx.error is None
        assert ctx.cleaned_up_contexts == frozenset()
        assert ctx.discarded_sandboxes == frozenset()
        assert ctx.cleanup_errors == ()

        # Timing default
        assert ctx.phase_timings == {}

    def test_create_with_bindings(
        self,
        mock_scope: MagicMock,
        mock_provider: MagicMock,
        mock_binding: MagicMock,
    ) -> None:
        """PhaseContext should accept bindings tuple."""
        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="my-task",
            bindings=(mock_binding,),
        )

        assert len(ctx.bindings) == 1
        assert ctx.bindings[0] is mock_binding


# =============================================================================
# Tests: Phase Output Update Methods
# =============================================================================


class TestPhaseOutputMethods:
    """Tests for with_* methods that update phase outputs."""

    def test_with_composed_binding(self, basic_context: PhaseContext) -> None:
        """with_composed_binding should return new context with binding set."""
        binding = ProviderBinding(context_ids=["ctx-1"])

        new_ctx = basic_context.with_composed_binding(binding)

        # New context has binding
        assert new_ctx.composed_binding is binding
        # Original unchanged
        assert basic_context.composed_binding is None
        # Other fields preserved
        assert new_ctx.task_name == basic_context.task_name

    def test_with_prepared(self, basic_context: PhaseContext) -> None:
        """with_prepared should return new context with prepared state."""
        mock_context = MagicMock()
        mock_sandbox = MagicMock()

        new_ctx = basic_context.with_prepared(
            prepared_contexts={"workspace": mock_context},
            sandboxes={"workspace": mock_sandbox},
        )

        assert new_ctx.prepared_contexts == {"workspace": mock_context}
        assert new_ctx.sandboxes == {"workspace": mock_sandbox}
        # Original unchanged
        assert basic_context.prepared_contexts == {}
        assert basic_context.sandboxes == {}

    def test_with_prepared_copies_dicts(self, basic_context: PhaseContext) -> None:
        """with_prepared should copy dicts to prevent external mutation."""
        contexts = {"workspace": MagicMock()}
        sandboxes = {"workspace": MagicMock()}

        new_ctx = basic_context.with_prepared(contexts, sandboxes)

        # Mutating originals should not affect context
        contexts["other"] = MagicMock()
        sandboxes["other"] = MagicMock()

        assert "other" not in new_ctx.prepared_contexts
        assert "other" not in new_ctx.sandboxes

    def test_with_result(self, basic_context: PhaseContext) -> None:
        """with_result should return new context with result set."""
        result = ExecutionResult(output_text="Hello, world!")

        new_ctx = basic_context.with_result(result)

        assert new_ctx.result is result
        assert basic_context.result is None

    def test_with_artifacts(self, basic_context: PhaseContext) -> None:
        """with_artifacts should return new context with artifact outputs."""
        mock_effect = MagicMock()
        outputs = {"report": "content"}
        effects = (mock_effect,)

        new_ctx = basic_context.with_artifacts(outputs, effects)

        assert new_ctx.artifact_outputs == {"report": "content"}
        assert new_ctx.artifact_effects == (mock_effect,)
        assert basic_context.artifact_outputs == {}

    def test_with_extracted_effects(self, basic_context: PhaseContext) -> None:
        """with_extracted_effects should return new context with effects."""
        effect1 = MagicMock()
        effect2 = MagicMock()

        new_ctx = basic_context.with_extracted_effects(
            all_effects=(effect1, effect2),
            per_context={"workspace": (effect1,), "session": (effect2,)},
        )

        assert new_ctx.extracted_effects == (effect1, effect2)
        assert new_ctx.context_effects == {
            "workspace": (effect1,),
            "session": (effect2,),
        }
        assert basic_context.extracted_effects == ()

    def test_with_context_outputs(self, basic_context: PhaseContext) -> None:
        """with_context_outputs should return new context with outputs."""
        mock_context = MagicMock()

        new_ctx = basic_context.with_context_outputs({"workspace": mock_context})

        assert new_ctx.context_outputs == {"workspace": mock_context}
        assert basic_context.context_outputs == {}

    def test_with_error(self, basic_context: PhaseContext) -> None:
        """with_error should return new context with error set."""
        error = RuntimeError("Something went wrong")

        new_ctx = basic_context.with_error(error)

        assert new_ctx.error is error
        assert basic_context.error is None

    def test_with_phase_timing(self, basic_context: PhaseContext) -> None:
        """with_phase_timing should accumulate timings."""
        ctx1 = basic_context.with_phase_timing("configure", 10.5)
        ctx2 = ctx1.with_phase_timing("prepare", 25.0)

        assert ctx2.phase_timings == {"configure": 10.5, "prepare": 25.0}
        assert basic_context.phase_timings == {}

    def test_with_prompt(self, basic_context: PhaseContext) -> None:
        """with_prompt should return new context with prompt set."""
        new_ctx = basic_context.with_prompt("New prompt")

        assert new_ctx.prompt == "New prompt"
        assert basic_context.prompt == "Test prompt"


# =============================================================================
# Tests: Cleanup State Tracking
# =============================================================================


class TestCleanupStateTracking:
    """Tests for cleanup state tracking methods."""

    def test_mark_cleaned_up(self, basic_context: PhaseContext) -> None:
        """mark_cleaned_up should add binding to cleaned_up_contexts."""
        ctx1 = basic_context.mark_cleaned_up("workspace")
        ctx2 = ctx1.mark_cleaned_up("session")

        assert ctx2.cleaned_up_contexts == frozenset({"workspace", "session"})
        assert basic_context.cleaned_up_contexts == frozenset()

    def test_mark_sandbox_discarded(self, basic_context: PhaseContext) -> None:
        """mark_sandbox_discarded should add binding to discarded_sandboxes."""
        ctx1 = basic_context.mark_sandbox_discarded("workspace")
        ctx2 = ctx1.mark_sandbox_discarded("db")

        assert ctx2.discarded_sandboxes == frozenset({"workspace", "db"})
        assert basic_context.discarded_sandboxes == frozenset()

    def test_is_cleaned_up(self, basic_context: PhaseContext) -> None:
        """is_cleaned_up should check cleaned_up_contexts."""
        ctx = basic_context.mark_cleaned_up("workspace")

        assert ctx.is_cleaned_up("workspace") is True
        assert ctx.is_cleaned_up("other") is False

    def test_is_sandbox_discarded(self, basic_context: PhaseContext) -> None:
        """is_sandbox_discarded should check discarded_sandboxes."""
        ctx = basic_context.mark_sandbox_discarded("workspace")

        assert ctx.is_sandbox_discarded("workspace") is True
        assert ctx.is_sandbox_discarded("other") is False

    def test_with_cleanup_errors(self, basic_context: PhaseContext) -> None:
        """with_cleanup_errors should set cleanup_errors tuple."""
        errors = [
            CleanupError("context:workspace", RuntimeError("error1")),
            CleanupError("sandbox:db", OSError("error2")),
        ]

        new_ctx = basic_context.with_cleanup_errors(errors)

        assert len(new_ctx.cleanup_errors) == 2
        assert new_ctx.cleanup_errors[0].resource_name == "context:workspace"
        assert new_ctx.cleanup_errors[1].resource_name == "sandbox:db"
        assert basic_context.cleanup_errors == ()

    def test_idempotent_cleanup_marking(self, basic_context: PhaseContext) -> None:
        """Marking same binding twice should be idempotent."""
        ctx1 = basic_context.mark_cleaned_up("workspace")
        ctx2 = ctx1.mark_cleaned_up("workspace")

        # Should still have just one entry
        assert ctx2.cleaned_up_contexts == frozenset({"workspace"})


# =============================================================================
# Tests: Computed Properties
# =============================================================================


class TestComputedProperties:
    """Tests for computed properties."""

    def test_binding_names(
        self,
        mock_scope: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """binding_names should return tuple of binding names."""
        binding1 = MagicMock()
        binding1.name = "workspace"
        binding2 = MagicMock()
        binding2.name = "session"

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(binding1, binding2),
        )

        assert ctx.binding_names == ("workspace", "session")

    def test_binding_names_empty(self, basic_context: PhaseContext) -> None:
        """binding_names should work with single binding."""
        assert basic_context.binding_names == ("workspace",)

    def test_has_error_false(self, basic_context: PhaseContext) -> None:
        """has_error should be False when no error."""
        assert basic_context.has_error is False

    def test_has_error_true(self, basic_context: PhaseContext) -> None:
        """has_error should be True when error is set."""
        ctx = basic_context.with_error(RuntimeError("oops"))
        assert ctx.has_error is True

    def test_has_cleanup_errors_false(self, basic_context: PhaseContext) -> None:
        """has_cleanup_errors should be False when empty."""
        assert basic_context.has_cleanup_errors is False

    def test_has_cleanup_errors_true(self, basic_context: PhaseContext) -> None:
        """has_cleanup_errors should be True when errors exist."""
        ctx = basic_context.with_cleanup_errors([CleanupError("context:x", RuntimeError("test"))])
        assert ctx.has_cleanup_errors is True

    def test_total_effects_zero(self, basic_context: PhaseContext) -> None:
        """total_effects should be 0 when no effects extracted."""
        assert basic_context.total_effects == 0

    def test_total_effects_count(self, basic_context: PhaseContext) -> None:
        """total_effects should count extracted effects."""
        ctx = basic_context.with_extracted_effects(
            all_effects=(MagicMock(), MagicMock(), MagicMock()),
            per_context={},
        )
        assert ctx.total_effects == 3


# =============================================================================
# Tests: Immutability
# =============================================================================


class TestImmutability:
    """Tests for PhaseContext immutability guarantees."""

    def test_context_is_frozen(self, basic_context: PhaseContext) -> None:
        """PhaseContext should be immutable (frozen dataclass)."""
        with pytest.raises(FrozenInstanceError):
            basic_context.task_name = "other"  # type: ignore[misc]

    def test_with_methods_return_new_instance(self, basic_context: PhaseContext) -> None:
        """with_* methods should return new instances."""
        new_ctx = basic_context.with_prompt("new prompt")

        assert new_ctx is not basic_context
        assert type(new_ctx) is type(basic_context)

    def test_chaining_with_methods(self, basic_context: PhaseContext) -> None:
        """with_* methods should be chainable."""
        binding = ProviderBinding(context_ids=["ctx-1"])
        result = ExecutionResult(output_text="output")

        final_ctx = (
            basic_context.with_prompt("new prompt")
            .with_composed_binding(binding)
            .with_result(result)
            .with_phase_timing("configure", 10.0)
        )

        assert final_ctx.prompt == "new prompt"
        assert final_ctx.composed_binding is binding
        assert final_ctx.result is result
        assert final_ctx.phase_timings == {"configure": 10.0}

        # Original unchanged
        assert basic_context.prompt == "Test prompt"
        assert basic_context.composed_binding is None


# =============================================================================
# Tests: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_bindings(self, mock_scope: MagicMock, mock_provider: MagicMock) -> None:
        """PhaseContext should work with no bindings."""
        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="no-bindings",
            bindings=(),
        )

        assert ctx.bindings == ()
        assert ctx.binding_names == ()

    def test_multiple_cleanup_errors(self, basic_context: PhaseContext) -> None:
        """Should handle multiple cleanup errors."""
        errors = [CleanupError(f"context:ctx{i}", RuntimeError(f"error{i}")) for i in range(5)]

        ctx = basic_context.with_cleanup_errors(errors)

        assert len(ctx.cleanup_errors) == 5
        assert all(isinstance(e, CleanupError) for e in ctx.cleanup_errors)

    def test_phase_timing_overwrite(self, basic_context: PhaseContext) -> None:
        """Recording same phase twice should overwrite timing."""
        ctx1 = basic_context.with_phase_timing("configure", 10.0)
        ctx2 = ctx1.with_phase_timing("configure", 15.0)

        assert ctx2.phase_timings["configure"] == 15.0

    def test_large_effects_tuple(self, basic_context: PhaseContext) -> None:
        """Should handle large numbers of effects."""
        effects = tuple(MagicMock() for _ in range(1000))

        ctx = basic_context.with_extracted_effects(
            all_effects=effects,
            per_context={"workspace": effects},
        )

        assert ctx.total_effects == 1000
        assert len(ctx.context_effects["workspace"]) == 1000


# =============================================================================
# Tests: Sandbox Wiring
# =============================================================================


class TestSandboxWiring:
    """Tests for with_sandbox_wired_binding method."""

    def test_wires_sandbox_path_to_composed_binding(
        self,
        mock_scope: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """Should update composed_binding.cwd to sandbox path."""
        from pathlib import Path

        # Create a context with path matching composed_binding.cwd
        mock_context = MagicMock()
        mock_context.path = Path("/original/workspace")

        # Create a sandbox with different path
        mock_sandbox = MagicMock()
        mock_sandbox.path = Path("/sandbox/workspace")

        # Create composed binding with original cwd (as string)
        composed = ProviderBinding(cwd="/original/workspace")

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            composed_binding=composed,
            prepared_contexts={"workspace": mock_context},
            sandboxes={"workspace": mock_sandbox},
        )

        result = ctx.with_sandbox_wired_binding()

        assert result.composed_binding is not None
        assert result.composed_binding.cwd == "/sandbox/workspace"

    def test_returns_self_when_no_sandboxes(
        self,
        mock_scope: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """Should return self unchanged when no sandboxes."""
        composed = ProviderBinding(cwd="/some/path")

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            composed_binding=composed,
            sandboxes={},  # No sandboxes
        )

        result = ctx.with_sandbox_wired_binding()

        assert result is ctx

    def test_returns_self_when_no_composed_binding(
        self,
        mock_scope: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """Should return self unchanged when no composed_binding."""
        mock_sandbox = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            composed_binding=None,  # No binding
            sandboxes={"workspace": mock_sandbox},
        )

        result = ctx.with_sandbox_wired_binding()

        assert result is ctx

    def test_returns_self_when_no_cwd_match(
        self,
        mock_scope: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """Should return self unchanged when cwd doesn't match any context."""
        from pathlib import Path

        mock_context = MagicMock()
        mock_context.path = Path("/different/path")

        mock_sandbox = MagicMock()
        mock_sandbox.path = Path("/sandbox/workspace")

        composed = ProviderBinding(cwd="/original/workspace")

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            composed_binding=composed,
            prepared_contexts={"workspace": mock_context},
            sandboxes={"workspace": mock_sandbox},
        )

        result = ctx.with_sandbox_wired_binding()

        # No change because paths don't match
        assert result is ctx

    def test_handles_context_without_path_attribute(
        self,
        mock_scope: MagicMock,
        mock_provider: MagicMock,
    ) -> None:
        """Should handle contexts that don't have a path attribute."""
        from pathlib import Path

        mock_context = MagicMock(spec=[])  # No attributes
        del mock_context.path  # Ensure no path attribute

        mock_sandbox = MagicMock()
        mock_sandbox.path = Path("/sandbox/workspace")

        composed = ProviderBinding(cwd="/original/workspace")

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            composed_binding=composed,
            prepared_contexts={"workspace": mock_context},
            sandboxes={"workspace": mock_sandbox},
        )

        # Should not raise, just return self
        result = ctx.with_sandbox_wired_binding()

        assert result is ctx
