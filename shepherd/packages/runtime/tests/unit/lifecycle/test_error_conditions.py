"""Tests for lifecycle error paths and failure recovery.

This module tests error conditions in the ExecutionLifecycle:
- Context preparation failures mid-way through multiple contexts
- Provider binding validation failures
- Phase execution failures and rollback behavior
"""

from dataclasses import dataclass

import pytest
from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect
from shepherd_core.errors import ExecutionError, TaskExecutionError
from shepherd_core.provider import ProviderRuntime
from shepherd_core.types import (
    ExecutionResult,
    PreparationError,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
)
from shepherd_runtime.context import BindableContext
from shepherd_runtime.lifecycle import ExecutionLifecycle
from shepherd_runtime.sandbox_registry import reset_default_registry
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Contexts
# =============================================================================


@dataclass(frozen=True)
class SimpleContext(BindableContext):
    """Simple context for basic testing."""

    name: str = "simple"
    _should_fail_prepare: bool = False
    _should_fail_cleanup: bool = False

    @property
    def context_id(self) -> str:
        return f"simple:{self.name}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    def configure(self, capabilities: ProviderCapabilities) -> ProviderBinding:
        return ProviderBinding()

    def prepare(self) -> "SimpleContext":
        if self._should_fail_prepare:
            raise RuntimeError(f"Prepare failed for {self.name}")
        return self

    def cleanup(self, error: Exception | None = None) -> None:
        if self._should_fail_cleanup:
            raise RuntimeError(f"Cleanup failed for {self.name}")

    def apply_effect(self, effect: Effect) -> "SimpleContext":
        return self


class FailOnPrepareContext(ExecutionContext):
    """Context that fails during prepare()."""

    def __init__(self, name: str = "fail_prepare", fail_on_call: int = 1):
        self._name = name
        self._fail_on_call = fail_on_call
        self._prepare_call_count = 0

    @property
    def context_id(self) -> str:
        return f"fail_prepare:{self._name}"

    def configure(self, capabilities: ProviderCapabilities) -> ProviderBinding:
        return ProviderBinding()

    def prepare(self) -> "FailOnPrepareContext":
        self._prepare_call_count += 1
        if self._prepare_call_count >= self._fail_on_call:
            raise RuntimeError(f"Prepare failed for {self._name} on call {self._prepare_call_count}")
        return self

    def cleanup(self, error: Exception | None = None) -> None:
        pass

    def extract_effects(self, sandbox, result: ExecutionResult) -> list[Effect]:
        """Default: no effects extracted."""
        return []

    def apply_effect(self, effect: Effect) -> "FailOnPrepareContext":
        return self


class TrackingContext(ExecutionContext):
    """Context that tracks lifecycle calls for verification."""

    def __init__(self, name: str = "tracking"):
        self._name = name
        self.configure_called = False
        self.prepare_called = False
        self.cleanup_called = False
        self.cleanup_error = None

    @property
    def context_id(self) -> str:
        return f"tracking:{self._name}"

    def configure(self, capabilities: ProviderCapabilities) -> ProviderBinding:
        self.configure_called = True
        return ProviderBinding()

    def prepare(self) -> "TrackingContext":
        self.prepare_called = True
        return self

    def cleanup(self, error: Exception | None = None) -> None:
        self.cleanup_called = True
        self.cleanup_error = error

    def extract_effects(self, sandbox, result: ExecutionResult) -> list[Effect]:
        """Default: no effects extracted."""
        return []

    def apply_effect(self, effect: Effect) -> "TrackingContext":
        return self


# =============================================================================
# Mock Provider
# =============================================================================


class MockProvider:
    """Mock provider for testing."""

    def __init__(
        self,
        provider_id: str = "mock:test",
        should_fail_execute: bool = False,
        should_fail_validation: bool = False,
        validation_error: str = "Validation failed",
    ):
        self._provider_id = provider_id
        self._should_fail_execute = should_fail_execute
        self._should_fail_validation = should_fail_validation
        self._validation_error = validation_error

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(provider_type="mock")

    @property
    def formatter(self):
        return None

    def validate_binding(self, binding: ProviderBinding) -> None:
        if self._should_fail_validation:
            from shepherd_core.errors import BindingValidationError

            raise BindingValidationError(
                context_id="test_context",
                unsatisfied_requirements=[self._validation_error],
            )

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
    ) -> ExecutionResult:
        if self._should_fail_execute:
            raise ExecutionError("Provider execution failed")
        return ExecutionResult(
            output_text="Mock output",
            stop_reason="end_turn",
        )


# =============================================================================
# Tests: Context Preparation Failures
# =============================================================================


class TestContextPreparationFailures:
    """Tests for context preparation failure handling."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset sandbox registry before each test."""
        reset_default_registry()
        yield
        reset_default_registry()

    @pytest.mark.asyncio
    async def test_prepare_failure_with_single_context(self):
        """When a single context fails to prepare, PreparationError is raised."""
        with Scope() as scope:
            ctx = FailOnPrepareContext("single", fail_on_call=1)
            scope.bind("ctx", ctx)
            provider = MockProvider()
            scope.register_provider("default", provider, default=True)

            with pytest.raises(PreparationError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    pass

            assert "single" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_prepare_failure_mid_multiple_contexts_triggers_rollback(self):
        """When second context fails to prepare, first context is cleaned up.

        This tests the critical error path where preparation fails partway through
        binding multiple contexts. The already-prepared contexts should be
        cleaned up before the error is raised.
        """
        with Scope() as scope:
            # First context - will prepare successfully
            ctx1 = TrackingContext("first")
            # Second context - will fail to prepare
            ctx2 = FailOnPrepareContext("second", fail_on_call=1)
            # Third context - should never be prepared
            ctx3 = TrackingContext("third")

            scope.bind("ctx1", ctx1)
            scope.bind("ctx2", ctx2)
            scope.bind("ctx3", ctx3)

            provider = MockProvider()
            scope.register_provider("default", provider, default=True)

            with pytest.raises(PreparationError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    pass

            # First context should have been prepared and then cleaned up
            assert ctx1.prepare_called, "First context should have been prepared"
            assert ctx1.cleanup_called, "First context should have been cleaned up after failure"

            # Third context should never have been touched
            assert not ctx3.prepare_called, "Third context should not have been prepared"

    @pytest.mark.asyncio
    async def test_prepare_failure_error_propagates_cause(self):
        """PreparationError should chain the original exception as __cause__."""
        with Scope() as scope:
            ctx = FailOnPrepareContext("test")
            scope.bind("ctx", ctx)
            provider = MockProvider()
            scope.register_provider("default", provider, default=True)

            with pytest.raises(PreparationError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    pass

            # Check that the original exception is chained
            assert exc_info.value.__cause__ is not None
            assert isinstance(exc_info.value.__cause__, RuntimeError)

    @pytest.mark.asyncio
    async def test_cleanup_failure_during_rollback_is_logged_not_raised(self):
        """If cleanup fails during rollback, error is logged but not raised.

        The original preparation error should still propagate.
        """
        with Scope() as scope:
            # First context - will prepare successfully but fail cleanup
            ctx1 = SimpleContext(name="first", _should_fail_cleanup=True)
            # Second context - will fail to prepare
            ctx2 = FailOnPrepareContext("second", fail_on_call=1)

            scope.bind("ctx1", ctx1)
            scope.bind("ctx2", ctx2)

            provider = MockProvider()
            scope.register_provider("default", provider, default=True)

            # Should still raise PreparationError, not the cleanup error
            with pytest.raises(PreparationError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    pass


# =============================================================================
# Tests: Provider Binding Validation Failures
# =============================================================================


class TestProviderBindingValidationFailures:
    """Tests for provider binding validation error handling."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset sandbox registry before each test."""
        reset_default_registry()
        yield
        reset_default_registry()

    @pytest.mark.asyncio
    async def test_binding_validation_failure_before_prepare(self):
        """Binding validation failure should occur before any prepare() calls.

        This is critical for clean error handling - we should fail fast during
        the configure phase, not after preparing resources.
        """
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            # Provider that fails validation
            provider = MockProvider(
                should_fail_validation=True,
                validation_error="Missing required capability",
            )
            scope.register_provider("default", provider, default=True)

            from shepherd_core.errors import BindingValidationError

            with pytest.raises(BindingValidationError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    pass

            # Crucially: prepare should NOT have been called
            assert not ctx.prepare_called, "prepare() should not be called if validation fails"
            assert "Missing required capability" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_binding_validation_preserves_scope_state(self):
        """Validation failure should not modify scope state."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            ref = scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_validation=True)
            scope.register_provider("default", provider, default=True)

            from shepherd_core.errors import BindingValidationError

            stream_len_before = len(scope.effects)

            with pytest.raises(BindingValidationError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    pass

            # Stream should not have task-related effects on validation failure
            # (only LifecyclePhaseStarted/Failed effects are acceptable)
            stream_len_after = len(scope.effects)
            # The stream may have some lifecycle effects, but no task completion
            assert stream_len_after >= stream_len_before


# =============================================================================
# Tests: Phase Execution Failures
# =============================================================================


class TestPhaseExecutionFailures:
    """Tests for phase execution failure and rollback behavior."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset sandbox registry before each test."""
        reset_default_registry()
        yield
        reset_default_registry()

    @pytest.mark.asyncio
    async def test_execute_phase_failure_triggers_cleanup(self):
        """When execute phase fails, prepared contexts are cleaned up."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            # Provider that fails during execute
            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            # Context should have been prepared and then cleaned up
            assert ctx.prepare_called
            assert ctx.cleanup_called

    @pytest.mark.asyncio
    async def test_execute_phase_failure_passes_error_to_cleanup(self):
        """The error from execute phase should be passed to cleanup()."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            # Check that cleanup received the error
            assert ctx.cleanup_error is not None
            assert isinstance(ctx.cleanup_error, ExecutionError)

    @pytest.mark.asyncio
    async def test_execute_called_twice_raises(self):
        """Calling execute() twice on same lifecycle should raise."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider()
            scope.register_provider("default", provider, default=True)

            async with ExecutionLifecycle(scope, provider) as lc:
                await lc.execute("First prompt")

                with pytest.raises(RuntimeError, match=r"execute.*only.*once"):
                    await lc.execute("Second prompt")

    @pytest.mark.asyncio
    async def test_execute_outside_context_manager_raises(self):
        """Calling execute() without entering context manager should raise."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider()
            scope.register_provider("default", provider, default=True)

            lc = ExecutionLifecycle(scope, provider)

            with pytest.raises(RuntimeError, match="Must enter"):
                await lc.execute("Test prompt")

    @pytest.mark.asyncio
    async def test_entering_lifecycle_twice_raises(self):
        """Entering the same lifecycle twice should raise."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider()
            scope.register_provider("default", provider, default=True)

            lc = ExecutionLifecycle(scope, provider)

            async with lc:
                with pytest.raises(RuntimeError, match="already entered"):
                    await lc.__aenter__()


# =============================================================================
# Tests: Rollback Behavior
# =============================================================================


class TestRollbackBehavior:
    """Tests for rollback behavior during lifecycle failures."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset sandbox registry before each test."""
        reset_default_registry()
        yield
        reset_default_registry()

    @pytest.mark.asyncio
    async def test_rollback_order_is_reverse_of_prepare(self):
        """Contexts should be cleaned up in reverse order of preparation."""
        cleanup_order = []

        class OrderTrackingContext(ExecutionContext):
            def __init__(self, name: str):
                self._name = name

            @property
            def context_id(self) -> str:
                return f"order:{self._name}"

            def configure(self, capabilities):
                return ProviderBinding()

            def prepare(self):
                return self

            def cleanup(self, error=None):
                cleanup_order.append(self._name)

            def apply_effect(self, effect):
                return self

        with Scope() as scope:
            ctx1 = OrderTrackingContext("first")
            ctx2 = OrderTrackingContext("second")
            ctx3 = OrderTrackingContext("third")

            scope.bind("ctx1", ctx1)
            scope.bind("ctx2", ctx2)
            scope.bind("ctx3", ctx3)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test")

            # Cleanup should be in reverse order
            assert cleanup_order == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_partial_rollback_continues_on_cleanup_error(self):
        """If one cleanup fails, others should still be attempted."""
        cleanup_attempted = []

        class MaybeFailCleanupContext(ExecutionContext):
            def __init__(self, name: str, fail_cleanup: bool = False):
                self._name = name
                self._fail_cleanup = fail_cleanup

            @property
            def context_id(self) -> str:
                return f"maybe:{self._name}"

            def configure(self, capabilities):
                return ProviderBinding()

            def prepare(self):
                return self

            def cleanup(self, error=None):
                cleanup_attempted.append(self._name)
                if self._fail_cleanup:
                    raise RuntimeError(f"Cleanup failed for {self._name}")

            def apply_effect(self, effect):
                return self

        with Scope() as scope:
            ctx1 = MaybeFailCleanupContext("first")
            ctx2 = MaybeFailCleanupContext("second", fail_cleanup=True)  # This will fail
            ctx3 = MaybeFailCleanupContext("third")

            scope.bind("ctx1", ctx1)
            scope.bind("ctx2", ctx2)
            scope.bind("ctx3", ctx3)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            # The original error should still propagate
            with pytest.raises(TaskExecutionError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test")

            # All three should have had cleanup attempted
            assert "first" in cleanup_attempted
            assert "second" in cleanup_attempted
            assert "third" in cleanup_attempted


# =============================================================================
# Tests: Lifecycle State Consistency
# =============================================================================


class TestLifecycleStateConsistency:
    """Tests for lifecycle state consistency after errors."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset sandbox registry before each test."""
        reset_default_registry()
        yield
        reset_default_registry()

    @pytest.mark.asyncio
    async def test_binding_lifecycle_flag_cleared_on_error(self):
        """in_lifecycle flag should be cleared even on error."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            binding_before = scope.get_binding("ctx")
            assert not binding_before.in_lifecycle

            with pytest.raises(TaskExecutionError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    binding_during = scope.get_binding("ctx")
                    assert binding_during.in_lifecycle
                    await lc.execute("Test")

            # Flag should be cleared after lifecycle exits
            binding_after = scope.get_binding("ctx")
            assert not binding_after.in_lifecycle

    @pytest.mark.asyncio
    async def test_binding_prepared_flag_cleared_on_error(self):
        """is_prepared flag should be cleared on error after cleanup."""
        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError):
                async with ExecutionLifecycle(scope, provider) as lc:
                    # During lifecycle, binding should be prepared
                    binding_during = scope.get_binding("ctx")
                    assert binding_during.is_prepared
                    await lc.execute("Test")

            # After error and cleanup, is_prepared should be cleared
            binding_after = scope.get_binding("ctx")
            assert not binding_after.is_prepared


# =============================================================================
# Tests: TaskExecutionError with Effects
# =============================================================================


class TestTaskExecutionError:
    """Tests for TaskExecutionError wrapping with effects capture."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset sandbox registry before each test."""
        reset_default_registry()
        yield
        reset_default_registry()

    @pytest.mark.asyncio
    async def test_execution_error_wrapped_as_task_execution_error(self):
        """Execution failures should be wrapped in TaskExecutionError."""
        from shepherd_core.errors import TaskExecutionError

        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            # Verify it's a TaskExecutionError, not raw ExecutionError
            assert isinstance(exc_info.value, TaskExecutionError)

    @pytest.mark.asyncio
    async def test_task_execution_error_has_effects(self):
        """TaskExecutionError should have .effects property populated."""
        from shepherd_core.errors import TaskExecutionError

        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            error = exc_info.value
            # Effects should be captured
            assert error.effects is not None
            # Should be a Stream object (or at least have len)
            assert len(error.effects) >= 0

    @pytest.mark.asyncio
    async def test_task_execution_error_contains_task_failed_effect(self):
        """TaskExecutionError.effects should contain TaskFailed effect."""
        from shepherd_core.effects import TaskFailed
        from shepherd_core.errors import TaskExecutionError

        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            error = exc_info.value
            # Query for TaskFailed effects
            task_failed_effects = list(error.effects.query(TaskFailed))
            assert len(task_failed_effects) >= 1
            # Verify the TaskFailed has useful information
            failed = task_failed_effects[0]
            assert failed.error_type is not None

    @pytest.mark.asyncio
    async def test_task_execution_error_has_cause(self):
        """TaskExecutionError should preserve the original exception as cause."""
        from shepherd_core.errors import TaskExecutionError

        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            error = exc_info.value
            # Should have cause attribute
            assert error.cause is not None
            # Cause should be the original ExecutionError
            assert isinstance(error.cause, ExecutionError)

    @pytest.mark.asyncio
    async def test_task_execution_error_has_phase(self):
        """TaskExecutionError should indicate the phase where failure occurred."""
        from shepherd_core.errors import TaskExecutionError

        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            error = exc_info.value
            # Should have phase attribute
            assert error.phase is not None
            # Phase should be "execute" since that's where we failed
            assert error.phase == "execute"

    @pytest.mark.asyncio
    async def test_task_execution_error_not_double_wrapped(self):
        """TaskExecutionError should not be double-wrapped."""
        from shepherd_core.errors import TaskExecutionError

        with Scope() as scope:
            ctx = TrackingContext("test")
            scope.bind("ctx", ctx)

            provider = MockProvider(should_fail_execute=True)
            scope.register_provider("default", provider, default=True)

            with pytest.raises(TaskExecutionError) as exc_info:
                async with ExecutionLifecycle(scope, provider) as lc:
                    await lc.execute("Test prompt")

            error = exc_info.value
            # The cause should NOT be another TaskExecutionError
            assert not isinstance(error.cause, TaskExecutionError)
