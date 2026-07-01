"""Tests for scope hardening: exception handling and checkpoint validation.

This module tests the hardened scope implementation:
- MaterializationError for expected materialization failures
- Rollback logging and error chaining
- Checkpoint validation with strict mode
- CheckpointValidationError for validation failures

These tests verify fixes for:
- Issue #6: Exception handling logic issue in materialize()
- Issue #19: Checkpoint restoration with stale contexts
"""

import logging

import pytest
from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect
from shepherd_core.errors import MaterializationError
from shepherd_runtime.checkpoint import CheckpointValidationError
from shepherd_runtime.materialization import MaterializationResult
from shepherd_runtime.scope import Scope
from shepherd_tests.contexts import CounterContext


class AnotherContext(ExecutionContext):
    """Another context for testing binding management."""

    def __init__(self, name: str = "default"):
        self._name = name

    @property
    def context_id(self) -> str:
        return f"another:{self._name}"

    def apply_effect(self, effect: Effect) -> "AnotherContext":
        return self


# =============================================================================
# Mock Materializer Registry for Testing
# =============================================================================


class MockMaterializerRegistry:
    """Mock registry for testing materialization error handling."""

    def __init__(self):
        self.materialize_results: list[MaterializationResult] = []
        self.reverse_errors: list[Exception | None] = []
        self._call_index = 0
        self._reverse_index = 0

    def materialize(self, effect: Effect) -> MaterializationResult:
        """Return pre-configured result or default success."""
        if self._call_index < len(self.materialize_results):
            result = self.materialize_results[self._call_index]
            self._call_index += 1
            return result
        self._call_index += 1
        return MaterializationResult(success=True, paths_affected=[])

    def can_reverse(self, effect: Effect) -> bool:
        """All effects are reversible for testing."""
        return True

    def reverse(self, effect: Effect) -> None:
        """Raise pre-configured error or succeed."""
        if self._reverse_index < len(self.reverse_errors):
            error = self.reverse_errors[self._reverse_index]
            self._reverse_index += 1
            if error is not None:
                raise error
        else:
            self._reverse_index += 1


# =============================================================================
# Tests: MaterializationError Exception Class
# =============================================================================


class TestMaterializationErrorClass:
    """Tests for the MaterializationError exception class."""

    def test_materialization_error_basic(self):
        """MaterializationError can be created with just a message."""
        error = MaterializationError("Test failure")
        assert str(error) == "Test failure"
        assert error.original_error is None
        assert error.rollback_errors == ()

    def test_materialization_error_with_original_error(self):
        """MaterializationError captures the original exception."""
        original = ValueError("Original problem")
        error = MaterializationError(
            "Materialization failed",
            original_error=original,
        )
        assert error.original_error is original
        assert "Original error: ValueError: Original problem" in str(error)

    def test_materialization_error_with_rollback_errors(self):
        """MaterializationError captures rollback failures."""
        rollback_errors = (
            ("PatchEffect", "File not found"),
            ("WriteEffect", "Permission denied"),
        )
        error = MaterializationError(
            "Materialization failed",
            rollback_errors=rollback_errors,
        )
        assert len(error.rollback_errors) == 2
        assert "Rollback failures (2)" in str(error)
        assert "PatchEffect: File not found" in str(error)

    def test_materialization_error_with_both(self):
        """MaterializationError captures both original and rollback errors."""
        original = RuntimeError("Something broke")
        rollback_errors = (("Effect1", "Rollback failed"),)
        error = MaterializationError(
            "Complete failure",
            original_error=original,
            rollback_errors=rollback_errors,
        )
        error_str = str(error)
        assert "Complete failure" in error_str
        assert "RuntimeError: Something broke" in error_str
        assert "Effect1: Rollback failed" in error_str


# =============================================================================
# Tests: Materialization Rollback Error Handling
# =============================================================================


class TestMaterializationRollbackHandling:
    """Tests for rollback error handling in materialize()."""

    def test_rollback_attempted_on_failure(self, caplog):
        """Rollback is attempted when materialization fails."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="effect1"))
            scope.emit(Effect(effect_type="effect2"))  # This one will fail

            registry = MockMaterializerRegistry()
            registry.materialize_results = [
                MaterializationResult(success=True, paths_affected=["file1.txt"]),
                MaterializationResult(success=False, error="Test failure"),
            ]

            with caplog.at_level(logging.DEBUG), pytest.raises(MaterializationError) as exc_info:
                scope.materialize(registry=registry)

            # Check rollback was attempted
            assert "attempting rollback" in caplog.text.lower()
            # Check MaterializationError was raised
            assert "Test failure" in str(exc_info.value)

    def test_rollback_failure_captured_in_exception(self, caplog):
        """When rollback fails, errors are captured in MaterializationError."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="effect1"))
            scope.emit(Effect(effect_type="effect2"))  # Will fail

            registry = MockMaterializerRegistry()
            registry.materialize_results = [
                MaterializationResult(success=True, paths_affected=["file1.txt"]),
                MaterializationResult(success=False, error="Materialization failed"),
            ]
            registry.reverse_errors = [RuntimeError("Rollback failed")]

            with caplog.at_level(logging.WARNING), pytest.raises(MaterializationError) as exc_info:
                scope.materialize(registry=registry)

            error = exc_info.value
            assert len(error.rollback_errors) == 1
            assert "Rollback failed" in error.rollback_errors[0][1]

    def test_rollback_logged_at_debug_level(self, caplog):
        """Rollback attempts are logged at debug level."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="effect1"))
            scope.emit(Effect(effect_type="effect2"))

            registry = MockMaterializerRegistry()
            registry.materialize_results = [
                MaterializationResult(success=True, paths_affected=[]),
                MaterializationResult(success=False, error="Fail"),
            ]

            with caplog.at_level(logging.DEBUG), pytest.raises(MaterializationError):
                scope.materialize(registry=registry)

            # Check debug logging
            assert "attempting rollback" in caplog.text.lower()

    def test_original_error_preserved_when_rollback_fails(self):
        """Original error is preserved in exception chain when rollback fails."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="effect1"))
            scope.emit(Effect(effect_type="effect2"))

            registry = MockMaterializerRegistry()
            registry.materialize_results = [
                MaterializationResult(success=True, paths_affected=[]),
                MaterializationResult(success=False, error="Original failure"),
            ]
            registry.reverse_errors = [RuntimeError("Rollback also failed")]

            with pytest.raises(MaterializationError) as exc_info:
                scope.materialize(registry=registry)

            # Original failure message preserved
            assert "Original failure" in str(exc_info.value)

    def test_unexpected_error_triggers_rollback(self, caplog):
        """Unexpected exceptions (not from result.success=False) trigger rollback."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="effect1"))
            scope.emit(Effect(effect_type="effect2"))

            registry = MockMaterializerRegistry()
            # First effect succeeds, second throws unexpectedly
            registry.materialize_results = [
                MaterializationResult(success=True, paths_affected=[]),
            ]

            # Make materialize throw on second call
            original_materialize = registry.materialize
            call_count = [0]

            def throwing_materialize(effect):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise ValueError("Unexpected boom!")
                return original_materialize(effect)

            registry.materialize = throwing_materialize

            with caplog.at_level(logging.DEBUG), pytest.raises(ValueError):
                scope.materialize(registry=registry)

            # Rollback should have been attempted
            assert "attempting rollback" in caplog.text.lower()

    def test_rollback_exception_chains_properly(self):
        """When rollback itself throws, both errors are chained."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="effect1"))
            scope.emit(Effect(effect_type="effect2"))

            registry = MockMaterializerRegistry()
            registry.materialize_results = [
                MaterializationResult(success=True, paths_affected=[]),
            ]

            # Make materialize throw on second call
            call_count = [0]
            original_materialize = registry.materialize

            def throwing_materialize(effect):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise ValueError("Materialize boom!")
                return original_materialize(effect)

            registry.materialize = throwing_materialize

            # Make reverse also throw
            def throwing_reverse(effect):
                raise RuntimeError("Rollback boom!")

            registry.reverse = throwing_reverse

            with pytest.raises(MaterializationError) as exc_info:
                scope.materialize(registry=registry)

            # Should have both the original error reference and rollback info
            assert exc_info.value.original_error is not None
            assert len(exc_info.value.rollback_errors) >= 1


# =============================================================================
# Tests: Checkpoint Validation
# =============================================================================


class TestCheckpointValidation:
    """Tests for checkpoint validation functionality."""

    def test_checkpoint_has_fingerprint(self):
        """Checkpoint captures fingerprint at creation."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            scope.emit(Effect(effect_type="e1"))

            cp = scope.checkpoint("test")

            assert cp._fingerprint is not None
            assert len(cp._fingerprint) > 0

    def test_checkpoint_validate_returns_valid(self):
        """validate() returns True for valid checkpoint."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="e1"))

            is_valid, warnings = cp.validate()

            assert is_valid is True
            assert len(warnings) == 0

    def test_checkpoint_validate_fails_for_restored(self):
        """validate() returns False for already restored checkpoint."""
        with Scope() as scope:
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="e0"))
            scope.restore(cp)

            is_valid, warnings = cp.validate()

            assert is_valid is False
            assert "already restored" in warnings[0].lower()

    def test_checkpoint_validate_fails_for_stale(self):
        """validate() returns False for stale checkpoint."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp1 = scope.checkpoint("first")
            scope.emit(Effect(effect_type="e1"))
            cp2 = scope.checkpoint("second")

            scope.restore(cp1)  # Makes cp2 stale

            is_valid, warnings = cp2.validate()

            assert is_valid is False
            assert "stale" in warnings[0].lower()

    def test_checkpoint_validate_warns_on_binding_decrease(self):
        """validate() warns when binding count decreased (non-strict)."""
        with Scope() as scope:
            ctx = CounterContext(0)
            scope.bind("counter", ctx)
            scope.bind("another", AnotherContext())

            cp = scope.checkpoint("test")  # 2 bindings

            # Simulate binding removal by manipulating internal state
            # In practice this shouldn't happen, but we test the detection
            original_bindings = scope._scope._bindings
            from dataclasses import replace

            scope._scope = replace(scope._scope, _bindings=original_bindings[:1])

            is_valid, warnings = cp.validate(strict=False)

            # Should be valid but with warning
            assert is_valid is True
            assert len(warnings) == 1
            assert "binding count decreased" in warnings[0].lower()

    def test_checkpoint_validate_fails_on_binding_decrease_strict(self):
        """validate(strict=True) fails when binding count decreased."""
        with Scope() as scope:
            ctx = CounterContext(0)
            scope.bind("counter", ctx)
            scope.bind("another", AnotherContext())

            cp = scope.checkpoint("test")

            # Simulate binding removal
            original_bindings = scope._scope._bindings
            from dataclasses import replace

            scope._scope = replace(scope._scope, _bindings=original_bindings[:1])

            is_valid, warnings = cp.validate(strict=True)

            assert is_valid is False
            assert "binding count decreased" in warnings[0].lower()


# =============================================================================
# Tests: Restore with Strict Validation
# =============================================================================


class TestRestoreStrictValidation:
    """Tests for restore() with strict validation mode."""

    def test_restore_default_mode_succeeds(self):
        """restore() succeeds with default (non-strict) mode."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="e1"))

            scope.restore(cp)

            assert len(scope.effects) == 1
            assert cp.is_restored

    def test_restore_strict_mode_succeeds_for_valid(self):
        """restore(strict=True) succeeds for valid checkpoint."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="e1"))

            scope.restore(cp, strict=True)

            assert len(scope.effects) == 1

    def test_restore_strict_mode_raises_on_inconsistency(self):
        """restore(strict=True) raises CheckpointValidationError on issues."""
        with Scope() as scope:
            scope.bind("counter", CounterContext(0))
            scope.bind("another", AnotherContext())

            cp = scope.checkpoint("test")

            # Simulate binding removal
            from dataclasses import replace

            scope._scope = replace(scope._scope, _bindings=scope._scope._bindings[:1])

            with pytest.raises(CheckpointValidationError) as exc_info:
                scope.restore(cp, strict=True)

            assert "validation failed" in str(exc_info.value).lower()

    def test_restore_logs_warnings_in_non_strict(self, caplog):
        """restore() logs warnings in non-strict mode."""
        with Scope() as scope:
            scope.bind("counter", CounterContext(0))
            scope.bind("another", AnotherContext())

            cp = scope.checkpoint("test")

            # Simulate binding removal
            from dataclasses import replace

            scope._scope = replace(scope._scope, _bindings=scope._scope._bindings[:1])

            with caplog.at_level(logging.WARNING):
                scope.restore(cp, strict=False)

            # Should log warning but succeed
            assert "validation warning" in caplog.text.lower()
            assert cp.is_restored

    def test_restore_still_raises_on_critical_issues(self):
        """restore() raises for critical issues even in non-strict mode."""
        with Scope() as scope:
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="e0"))
            scope.restore(cp)

            # Already restored - critical issue
            with pytest.raises(ValueError, match="already restored"):
                scope.restore(cp, strict=False)

    def test_restore_stale_raises_in_both_modes(self):
        """Stale checkpoint raises in both strict and non-strict modes."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp1 = scope.checkpoint("first")
            scope.emit(Effect(effect_type="e1"))
            cp2 = scope.checkpoint("second")

            scope.restore(cp1)

            # cp2 is stale - should raise in non-strict
            with pytest.raises(ValueError, match="stale"):
                scope.restore(cp2, strict=False)

        # Also test strict mode
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp1 = scope.checkpoint("first")
            scope.emit(Effect(effect_type="e1"))
            cp2 = scope.checkpoint("second")

            scope.restore(cp1)

            # cp2 is stale - should raise in strict
            with pytest.raises((ValueError, CheckpointValidationError)):
                scope.restore(cp2, strict=True)


# =============================================================================
# Tests: Checkpoint Fingerprint
# =============================================================================


class TestCheckpointFingerprint:
    """Tests for checkpoint fingerprint functionality."""

    def test_fingerprint_changes_with_stream_content(self):
        """Different stream contents produce different fingerprints."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="typeA"))
            cp1 = scope.checkpoint("first")

        with Scope() as scope:
            scope.emit(Effect(effect_type="typeB"))
            cp2 = scope.checkpoint("second")

        assert cp1._fingerprint != cp2._fingerprint

    def test_fingerprint_same_for_same_content(self):
        """Same stream content produces same fingerprint."""
        fingerprints = []
        for _ in range(2):
            with Scope() as scope:
                scope.emit(Effect(effect_type="same_type"))
                cp = scope.checkpoint("test")
                fingerprints.append(cp._fingerprint)

        assert fingerprints[0] == fingerprints[1]

    def test_empty_stream_fingerprint(self):
        """Empty stream has predictable fingerprint."""
        with Scope() as scope:
            cp = scope.checkpoint("empty")

        assert cp._fingerprint == "empty"


# =============================================================================
# Tests: CheckpointValidationError
# =============================================================================


class TestCheckpointValidationError:
    """Tests for CheckpointValidationError exception class."""

    def test_validation_error_basic(self):
        """CheckpointValidationError has basic attributes."""
        error = CheckpointValidationError(
            "my_checkpoint",
            "stale checkpoint",
        )
        assert error.checkpoint_name == "my_checkpoint"
        assert error.reason == "stale checkpoint"
        assert "my_checkpoint" in str(error)
        assert "stale checkpoint" in str(error)

    def test_validation_error_with_details(self):
        """CheckpointValidationError includes details in message."""
        error = CheckpointValidationError(
            "test_cp",
            "binding mismatch",
            details="expected 3, got 2",
        )
        assert error.details == "expected 3, got 2"
        assert "expected 3, got 2" in str(error)

    def test_validation_error_inherits_from_valueerror(self):
        """CheckpointValidationError inherits from ValueError."""
        error = CheckpointValidationError("cp", "reason")
        assert isinstance(error, ValueError)


# =============================================================================
# Tests: Integration - Materialization and Checkpoint
# =============================================================================


class TestIntegration:
    """Integration tests combining materialization and checkpoints."""

    def test_checkpoint_before_failed_materialize(self):
        """Checkpoint can be restored after failed materialization."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="safe_work"))
            cp = scope.checkpoint("before_materialize")
            scope.emit(Effect(effect_type="risky_work"))

            registry = MockMaterializerRegistry()
            registry.materialize_results = [
                MaterializationResult(success=True, paths_affected=[]),
                MaterializationResult(success=False, error="Failed"),
            ]

            try:
                scope.materialize(registry=registry)
            except MaterializationError:
                # Materialize failed, but we can still restore checkpoint
                # because nothing was actually materialized (watermark not updated)
                scope.restore(cp)

            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "safe_work"

    def test_context_state_preserved_through_checkpoint_restore(self):
        """Context state is correctly restored after checkpoint restore."""
        with Scope() as scope:
            counter = CounterContext(count=0)
            ref = scope.bind("counter", counter)

            assert ref.count == 0

            cp = scope.checkpoint("initial")

            scope.emit(Effect(effect_type="increment", binding_name="counter"))
            scope.emit(Effect(effect_type="increment", binding_name="counter"))

            assert ref.count == 2

            scope.restore(cp)

            assert ref.count == 0
