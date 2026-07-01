"""Tests for EffectEmitter.

Covers:
- Effect routing to scope
- Effect routing to formatter (when present)
- Exception handling around formatter calls
- Finalize behavior
- Convenience methods for common effects
"""

import logging
from unittest.mock import MagicMock

import pytest
from shepherd_core.effects import (
    ContextCaptured,
    ContextCleanedUp,
    ContextPrepared,
    LifecyclePhaseCompleted,
    LifecyclePhaseStarted,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
)
from shepherd_runtime._lifecycle import EffectEmitter

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_scope() -> MagicMock:
    """Create a mock scope with emit method."""
    scope = MagicMock()
    scope.emit = MagicMock()
    return scope


@pytest.fixture
def mock_formatter() -> MagicMock:
    """Create a mock formatter with on_effect and finalize methods."""
    formatter = MagicMock()
    formatter.on_effect = MagicMock()
    formatter.finalize = MagicMock()
    return formatter


# =============================================================================
# Tests: Basic Emission
# =============================================================================


class TestEmitBasic:
    """Tests for basic effect emission."""

    def test_emit_routes_to_scope(self, mock_scope: MagicMock) -> None:
        """emit() should always route effects to scope."""
        emitter = EffectEmitter(mock_scope)
        effect = TaskStarted(task_name="test", provider_id="test-provider")

        emitter.emit(effect)

        mock_scope.emit.assert_called_once_with(effect)

    def test_emit_routes_to_formatter_when_present(self, mock_scope: MagicMock, mock_formatter: MagicMock) -> None:
        """emit() should route effects to formatter when present."""
        emitter = EffectEmitter(mock_scope, mock_formatter)
        effect = TaskStarted(task_name="test", provider_id="test-provider")

        emitter.emit(effect)

        mock_scope.emit.assert_called_once_with(effect)
        mock_formatter.on_effect.assert_called_once_with(effect)

    def test_emit_does_not_call_formatter_when_none(self, mock_scope: MagicMock) -> None:
        """emit() should not fail when formatter is None."""
        emitter = EffectEmitter(mock_scope, formatter=None)
        effect = TaskStarted(task_name="test", provider_id="test-provider")

        # Should not raise
        emitter.emit(effect)

        mock_scope.emit.assert_called_once_with(effect)


# =============================================================================
# Tests: Exception Handling
# =============================================================================


class TestEmitExceptionHandling:
    """Tests for exception handling around formatter calls."""

    def test_emit_handles_formatter_exception(
        self, mock_scope: MagicMock, mock_formatter: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """emit() should re-raise formatter exceptions in strict mode."""
        mock_formatter.on_effect.side_effect = RuntimeError("Formatter broke")
        emitter = EffectEmitter(mock_scope, mock_formatter)
        effect = TaskStarted(task_name="test", provider_id="test-provider")

        with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="Formatter broke"):
            emitter.emit(effect)

        # Scope should still receive the effect
        mock_scope.emit.assert_called_once_with(effect)

        # Error should be logged
        assert "Formatter error on TaskStarted" in caplog.text
        assert "Formatter broke" in caplog.text

    def test_emit_continues_after_formatter_exception(self, mock_scope: MagicMock, mock_formatter: MagicMock) -> None:
        """emit() should continue working after formatter exceptions."""
        mock_formatter.on_effect.side_effect = [
            RuntimeError("First error"),
            None,  # Second call succeeds
        ]
        emitter = EffectEmitter(mock_scope, mock_formatter, strict_formatter=False)

        effect1 = TaskStarted(task_name="test1", provider_id="provider")
        effect2 = TaskCompleted(task_name="test2", provider_id="provider", duration_ms=100)

        emitter.emit(effect1)  # Formatter fails
        emitter.emit(effect2)  # Formatter succeeds

        # Both effects should reach scope
        assert mock_scope.emit.call_count == 2
        mock_scope.emit.assert_any_call(effect1)
        mock_scope.emit.assert_any_call(effect2)


# =============================================================================
# Tests: Finalize
# =============================================================================


class TestFinalize:
    """Tests for finalize behavior."""

    def test_finalize_calls_formatter(self, mock_scope: MagicMock, mock_formatter: MagicMock) -> None:
        """finalize() should call formatter.finalize() when formatter present."""
        emitter = EffectEmitter(mock_scope, mock_formatter)

        emitter.finalize()

        mock_formatter.finalize.assert_called_once()

    def test_finalize_handles_formatter_exception(
        self, mock_scope: MagicMock, mock_formatter: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """finalize() should re-raise formatter exceptions in strict mode."""
        mock_formatter.finalize.side_effect = RuntimeError("Finalize broke")
        emitter = EffectEmitter(mock_scope, mock_formatter)

        with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="Finalize broke"):
            emitter.finalize()

        # Error should be logged
        assert "Formatter error on finalize" in caplog.text
        assert "Finalize broke" in caplog.text

    def test_finalize_noop_without_formatter(self, mock_scope: MagicMock) -> None:
        """finalize() should be a no-op when no formatter present."""
        emitter = EffectEmitter(mock_scope, formatter=None)

        # Should not raise
        emitter.finalize()


# =============================================================================
# Tests: Convenience Methods
# =============================================================================


class TestConvenienceMethods:
    """Tests for convenience effect emission methods."""

    def test_emit_phase_started_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_phase_started() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_phase_started(
            task_name="test-task",
            provider_id="test-provider",
            phase="configure",
            context_count=3,
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, LifecyclePhaseStarted)
        assert effect.task_name == "test-task"
        assert effect.provider_id == "test-provider"
        assert effect.phase == "configure"
        assert effect.context_count == 3

    def test_emit_phase_completed_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_phase_completed() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_phase_completed(
            task_name="test-task",
            provider_id="test-provider",
            phase="execute",
            duration_ms=150.5,
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, LifecyclePhaseCompleted)
        assert effect.task_name == "test-task"
        assert effect.phase == "execute"
        assert effect.duration_ms == 150.5

    def test_emit_phase_failed_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_phase_failed() should create and emit correct effect."""
        from shepherd_core.effects import LifecyclePhaseFailed

        emitter = EffectEmitter(mock_scope)

        emitter.emit_phase_failed(
            task_name="test-task",
            provider_id="test-provider",
            phase="execute",
            duration_ms=50.0,
            error_type="RuntimeError",
            error_message="Something went wrong",
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, LifecyclePhaseFailed)
        assert effect.task_name == "test-task"
        assert effect.phase == "execute"
        assert effect.duration_ms == 50.0
        assert effect.error_type == "RuntimeError"
        assert effect.error_message == "Something went wrong"

    def test_emit_task_started_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_task_started() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_task_started(
            task_name="test-task",
            provider_id="test-provider",
            inputs={"prompt": "hello"},
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, TaskStarted)
        assert effect.inputs == {"prompt": "hello"}

    def test_emit_task_completed_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_task_completed() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_task_completed(
            task_name="test-task",
            provider_id="test-provider",
            outputs={"result": "done"},
            duration_ms=500.0,
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, TaskCompleted)
        assert effect.outputs == {"result": "done"}
        assert effect.duration_ms == 500.0

    def test_emit_task_failed_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_task_failed() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_task_failed(
            task_name="test-task",
            provider_id="test-provider",
            error="Something went wrong",
            error_type="RuntimeError",
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, TaskFailed)
        assert effect.error == "Something went wrong"
        assert effect.error_type == "RuntimeError"

    def test_emit_context_prepared_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_context_prepared() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_context_prepared(
            context_id="ctx-123",
            binding_name="workspace",
            task_name="test-task",
            provider_id="test-provider",
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, ContextPrepared)
        assert effect.context_id == "ctx-123"
        assert effect.binding_name == "workspace"

    def test_emit_context_captured_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_context_captured() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_context_captured(
            context_id="ctx-123",
            binding_name="workspace",
            old_context_id="ctx-old",
            new_context_id="ctx-new",
            effect_count=5,
            task_name="test-task",
            provider_id="test-provider",
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, ContextCaptured)
        assert effect.old_context_id == "ctx-old"
        assert effect.new_context_id == "ctx-new"
        assert effect.effect_count == 5

    def test_emit_context_cleaned_up_creates_correct_effect(self, mock_scope: MagicMock) -> None:
        """emit_context_cleaned_up() should create and emit correct effect."""
        emitter = EffectEmitter(mock_scope)

        emitter.emit_context_cleaned_up(
            context_id="ctx-123",
            binding_name="workspace",
            had_error=True,
            task_name="test-task",
            provider_id="test-provider",
        )

        mock_scope.emit.assert_called_once()
        effect = mock_scope.emit.call_args[0][0]
        assert isinstance(effect, ContextCleanedUp)
        assert effect.had_error is True


# =============================================================================
# Tests: Properties
# =============================================================================


class TestProperties:
    """Tests for emitter properties."""

    def test_scope_property(self, mock_scope: MagicMock) -> None:
        """Scope property should return the configured scope."""
        emitter = EffectEmitter(mock_scope)
        assert emitter.scope is mock_scope

    def test_formatter_property_with_formatter(self, mock_scope: MagicMock, mock_formatter: MagicMock) -> None:
        """Formatter property should return the configured formatter."""
        emitter = EffectEmitter(mock_scope, mock_formatter)
        assert emitter.formatter is mock_formatter

    def test_formatter_property_without_formatter(self, mock_scope: MagicMock) -> None:
        """Formatter property should return None when not configured."""
        emitter = EffectEmitter(mock_scope)
        assert emitter.formatter is None
