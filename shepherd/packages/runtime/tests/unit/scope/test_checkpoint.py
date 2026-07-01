"""Tests for checkpoint/restore scope methods using stream truncation model.

The checkpoint/restore feature uses stream truncation to discard effects:
1. checkpoint() records the current stream position
2. restore() truncates stream to that position and replays to recompute state
"""

import pytest
from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect
from shepherd_core.errors import BindingNotFoundError, ContainmentError
from shepherd_runtime.checkpoint import Checkpoint
from shepherd_runtime.scope import Scope
from shepherd_tests.contexts import CounterContext


class AnotherContext(ExecutionContext):
    """Another context for testing binding management."""

    @property
    def context_id(self) -> str:
        return "another:test"

    def apply_effect(self, effect: Effect) -> "AnotherContext":
        return self


# =============================================================================
# Tests for checkpoint()
# =============================================================================


class TestCheckpoint:
    """Tests for scope.checkpoint() method."""

    def test_checkpoint_creates_savepoint(self):
        """checkpoint() creates a Checkpoint object with position info."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="before"))

            cp = scope.checkpoint("test_checkpoint")

            assert isinstance(cp, Checkpoint)
            assert cp.name == "test_checkpoint"
            assert cp.position == 1  # Position after the "before" effect
            assert cp.is_active
            assert not cp.is_restored

    def test_checkpoint_effects_since_shows_subsequent_effects(self):
        """effects_since shows effects emitted after checkpoint."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="before"))

            cp = scope.checkpoint("savepoint")

            # No effects since checkpoint yet
            assert len(cp.effects_since) == 0

            # Emit effects after checkpoint
            scope.emit(Effect(effect_type="after_1"))
            scope.emit(Effect(effect_type="after_2"))

            # Now effects_since shows 2 effects
            assert len(cp.effects_since) == 2
            assert cp.effects_since[0].effect.effect_type == "after_1"
            assert cp.effects_since[1].effect.effect_type == "after_2"

    def test_checkpoint_name_in_repr(self):
        """Checkpoint repr includes name and status."""
        with Scope() as scope:
            cp = scope.checkpoint("my_savepoint")

            repr_str = repr(cp)
            assert "my_savepoint" in repr_str
            assert "active" in repr_str

            scope.restore(cp)

            repr_str = repr(cp)
            assert "restored" in repr_str

    def test_checkpoint_tracks_binding_count(self):
        """Checkpoint records binding count for removal tracking."""
        with Scope() as scope:
            counter = CounterContext(count=0)
            scope.bind("counter", counter)

            cp = scope.checkpoint("after_bind")

            assert cp._binding_count == 1
            assert cp.bindings_added == 0

            # Add another binding after checkpoint
            scope.bind("another", AnotherContext())

            assert cp.bindings_added == 1


# =============================================================================
# Tests for restore()
# =============================================================================


class TestRestore:
    """Tests for scope.restore() method."""

    def test_restore_truncates_stream(self):
        """restore() removes effects emitted after checkpoint."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="before"))

            cp = scope.checkpoint("savepoint")

            scope.emit(Effect(effect_type="after_1"))
            scope.emit(Effect(effect_type="after_2"))

            assert len(scope.effects) == 3

            scope.restore(cp)

            # Stream truncated to checkpoint position
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "before"
            assert cp.is_restored

    def test_restore_recomputes_context_state(self):
        """restore() resets and replays to recompute context state."""
        with Scope() as scope:
            counter = CounterContext(count=0)
            scope.bind("counter", counter)

            assert scope.get_context("counter").count == 0

            cp = scope.checkpoint("before_increments")

            # Emit increment effects (with binding_name for routing)
            scope.emit(Effect(effect_type="increment", binding_name="counter"))
            scope.emit(Effect(effect_type="increment", binding_name="counter"))

            assert scope.get_context("counter").count == 2

            scope.restore(cp)

            # State recomputed from initial
            assert scope.get_context("counter").count == 0

    def test_restore_twice_raises(self):
        """Cannot restore the same checkpoint twice."""
        with Scope() as scope:
            cp = scope.checkpoint("savepoint")

            scope.restore(cp)

            with pytest.raises(ValueError, match="already restored"):
                scope.restore(cp)

    def test_restore_wrong_scope_raises(self):
        """Cannot restore checkpoint from different scope."""
        with Scope() as scope1:
            cp = scope1.checkpoint("savepoint")

            with Scope() as scope2, pytest.raises(ValueError, match="belongs to different scope"):
                scope2.restore(cp)

    def test_restore_after_materialize_raises(self):
        """Cannot restore if effects after checkpoint were materialized."""
        with Scope() as scope:
            cp = scope.checkpoint("savepoint")

            scope.emit(Effect(effect_type="will_escape"))

            # Simulate materialization by advancing watermark
            scope._materialized_index = len(scope.effects)

            with pytest.raises(ContainmentError, match="materialized"):
                scope.restore(cp)

    def test_restore_removes_bindings_added_after_checkpoint(self):
        """Bindings added after checkpoint are removed on restore."""
        with Scope() as scope:
            counter = CounterContext(count=0)
            scope.bind("counter", counter)

            cp = scope.checkpoint("before_new_binding")

            # Add a new binding after checkpoint
            scope.bind("new_context", AnotherContext())
            assert scope.get_context("new_context") is not None
            assert cp.bindings_added == 1

            # Restore removes the new binding
            scope.restore(cp)

            with pytest.raises(BindingNotFoundError):
                scope.get_context("new_context")

            # Original binding still works
            assert scope.get_context("counter").count == 0


# =============================================================================
# Tests for stale checkpoints
# =============================================================================


class TestStaleCheckpoint:
    """Tests for stale checkpoint behavior."""

    def test_stale_checkpoint_effects_since_returns_empty(self):
        """Stale checkpoint's effects_since returns empty stream."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            scope.emit(Effect(effect_type="e1"))

            cp1 = scope.checkpoint("first")  # position=2

            scope.emit(Effect(effect_type="e2"))
            scope.emit(Effect(effect_type="e3"))

            cp2 = scope.checkpoint("second")  # position=4

            # Restore to first checkpoint
            scope.restore(cp1)
            assert len(scope.effects) == 2

            # cp2 is now stale (position=4 > stream length=2)
            assert cp2.is_stale
            assert not cp2.is_active
            assert len(cp2.effects_since) == 0  # Safe inspection returns empty

    def test_stale_checkpoint_restore_raises(self):
        """Restoring stale checkpoint raises ValueError."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            scope.emit(Effect(effect_type="e1"))

            cp1 = scope.checkpoint("first")  # position=2
            scope.emit(Effect(effect_type="e2"))

            cp2 = scope.checkpoint("second")  # position=3

            # Restore to first checkpoint - makes cp2 stale
            scope.restore(cp1)

            # Attempting to restore stale checkpoint raises
            with pytest.raises(ValueError, match="stale"):
                scope.restore(cp2)

    def test_is_stale_property(self):
        """is_stale correctly identifies invalidated checkpoints."""
        with Scope() as scope:
            cp1 = scope.checkpoint("first")
            scope.emit(Effect(effect_type="e0"))
            cp2 = scope.checkpoint("second")

            assert not cp1.is_stale
            assert not cp2.is_stale

            scope.restore(cp1)

            assert not cp1.is_stale  # Restored, not stale
            assert cp2.is_stale  # Invalidated by restore


# =============================================================================
# Tests for nested checkpoints
# =============================================================================


class TestNestedCheckpoints:
    """Tests for multiple checkpoint workflows."""

    def test_nested_checkpoints(self):
        """Multiple checkpoints can be created and restored in order."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))

            cp1 = scope.checkpoint("first")
            scope.emit(Effect(effect_type="e1"))

            cp2 = scope.checkpoint("second")
            scope.emit(Effect(effect_type="e2"))

            assert len(scope.effects) == 3

            # Restore to second checkpoint
            scope.restore(cp2)
            assert len(scope.effects) == 2
            assert cp1.is_active  # First checkpoint still usable

            # Restore to first checkpoint
            scope.restore(cp1)
            assert len(scope.effects) == 1

    def test_checkpoint_at_stream_start(self):
        """Checkpoint at position 0 works correctly."""
        with Scope() as scope:
            cp = scope.checkpoint("empty")

            scope.emit(Effect(effect_type="e0"))
            scope.emit(Effect(effect_type="e1"))

            assert len(scope.effects) == 2

            scope.restore(cp)

            assert len(scope.effects) == 0


# =============================================================================
# Tests for checkpoint/restore workflow
# =============================================================================


class TestCheckpointWorkflow:
    """Integration tests for checkpoint/restore workflows."""

    def test_error_recovery_pattern(self):
        """Checkpoint can be used for error recovery."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="safe_work"))

            cp = scope.checkpoint("before_risky")

            try:
                scope.emit(Effect(effect_type="risky_work"))
                raise RuntimeError("Simulated failure")
            except RuntimeError:
                scope.restore(cp)

            # Only safe_work remains
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "safe_work"
            assert cp.is_restored

    def test_conditional_rollback_pattern(self):
        """Checkpoint can be used for conditional rollback."""
        with Scope() as scope:
            cp = scope.checkpoint("attempt")

            scope.emit(Effect(effect_type="tentative_change"))

            # Simulate checking a condition
            result_valid = False

            if not result_valid:
                scope.restore(cp)

            assert cp.is_restored
            assert len(scope.effects) == 0

    def test_effects_since_for_preview(self):
        """Can inspect effects before deciding to restore."""
        with Scope() as scope:
            cp = scope.checkpoint("preview_point")

            scope.emit(Effect(effect_type="change_1"))
            scope.emit(Effect(effect_type="change_2"))

            # Inspect before deciding
            pending = cp.effects_since
            assert len(pending) == 2

            # Decide to rollback
            scope.restore(cp)
            assert len(scope.effects) == 0


# =============================================================================
# Tests for ContextRef behavior after restore
# =============================================================================


class TestContextRefAfterRestore:
    """Tests that ContextRef sees restored state correctly."""

    def test_context_ref_reflects_restored_state(self):
        """ContextRef sees restored state without needing to re-bind.

        This is a priority test because it validates the core user experience:
        refs obtained from bind() should always reflect current scope state,
        even after restore operations.
        """
        with Scope() as scope:
            counter = CounterContext(count=0)
            ref = scope.bind("counter", counter)

            assert ref.count == 0

            cp = scope.checkpoint("initial")

            scope.emit(Effect(effect_type="increment", binding_name="counter"))
            scope.emit(Effect(effect_type="increment", binding_name="counter"))

            assert ref.count == 2  # ContextRef sees current state

            scope.restore(cp)

            assert ref.count == 0  # ContextRef sees restored state


# =============================================================================
# Tests for checkpoint context manager
# =============================================================================


class TestCheckpointContextManager:
    """Tests for using checkpoint as a context manager."""

    def test_checkpoint_as_context_manager(self):
        """Checkpoint can be used with `with` statement."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="before"))

            with scope.checkpoint("test") as cp:
                assert isinstance(cp, Checkpoint)
                assert cp.is_active

            # After exiting, checkpoint is invalidated
            assert cp._exited

    def test_checkpoint_restore_inside_context_manager(self):
        """Can call cp.restore() inside the with block."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="before"))

            with scope.checkpoint("rollback_test") as cp:
                scope.emit(Effect(effect_type="tentative_1"))
                scope.emit(Effect(effect_type="tentative_2"))

                assert len(scope.effects) == 3

                # Restore inside the with block
                cp.restore()

                assert len(scope.effects) == 1
                assert cp.is_restored

    def test_checkpoint_restore_after_exit_raises(self):
        """Cannot call cp.restore() after exiting context manager."""
        with Scope() as scope:
            with scope.checkpoint("test") as cp:
                scope.emit(Effect(effect_type="change"))

            # After exiting, restore should fail
            with pytest.raises(ValueError, match="after exiting context manager"):
                cp.restore()

    def test_checkpoint_context_manager_with_exception(self):
        """Context manager invalidates checkpoint even on exception."""
        with Scope() as scope:
            try:
                with scope.checkpoint("error_test") as cp:
                    scope.emit(Effect(effect_type="before_error"))
                    raise RuntimeError("Simulated error")
            except RuntimeError:
                pass

            # Checkpoint was invalidated by exiting the context manager
            assert cp._exited

    def test_checkpoint_context_manager_restore_and_continue(self):
        """Can restore and continue working inside context manager."""
        with Scope() as scope:
            counter = CounterContext(count=0)
            scope.bind("counter", counter)

            with scope.checkpoint("retry_point") as cp:
                scope.emit(Effect(effect_type="increment", binding_name="counter"))
                scope.emit(Effect(effect_type="increment", binding_name="counter"))

                assert scope.get_context("counter").count == 2

                # Restore and try different approach
                cp.restore()

                assert scope.get_context("counter").count == 0

                # Continue with new work
                scope.emit(Effect(effect_type="increment", binding_name="counter"))

                assert scope.get_context("counter").count == 1

    def test_checkpoint_context_manager_conditional_restore(self):
        """Common pattern: conditionally restore based on result."""
        with Scope() as scope:
            with scope.checkpoint("conditional") as cp:
                scope.emit(Effect(effect_type="change_1"))
                scope.emit(Effect(effect_type="change_2"))

                # Simulate checking if result is acceptable
                result_ok = False

                if not result_ok:
                    cp.restore()

            # Effects were rolled back
            assert len(scope.effects) == 0
