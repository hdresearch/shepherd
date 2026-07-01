"""Tests for Stream B quality improvements to scope module.

This module tests the three fixes implemented in Stream B:
1. Issue 2.4: Missing Error Recovery in bind() - registry consistency
2. Issue 3.3.1: Partial Checkpoint Restore - keep_bindings and exclude_effect_types
3. Issue #26: Circular Import Cleanup - verified by test imports working
"""

import pytest
from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect
from shepherd_runtime.scope import Scope
from shepherd_tests.contexts import SimpleContext


class DuplicateIdContext(ExecutionContext):
    """Context with configurable context_id for testing duplicates."""

    def __init__(self, context_id: str):
        self._context_id = context_id

    @property
    def context_id(self) -> str:
        return self._context_id

    def apply_effect(self, effect: Effect) -> "DuplicateIdContext":
        return self


# =============================================================================
# Issue 2.4: Missing Error Recovery in bind()
# =============================================================================


class TestBindRegistryConsistency:
    """Tests for registry consistency when bind() fails.

    Issue 2.4: If with_binding() raises ValueError, the registry should
    NOT be updated. The fix ensures two-phase commit: scope is updated
    first, then registry only on success.
    """

    def test_registry_not_updated_on_duplicate_name_error(self):
        """Registry should not have entry if bind fails due to duplicate name."""
        with Scope() as scope:
            ctx1 = SimpleContext("first")
            ctx2 = SimpleContext("second")

            # Successful bind
            scope.bind("ctx", ctx1)
            assert "ctx" in scope._binding_registry._lifecycle_state

            # Count registry entries before failed bind
            registry_size_before = len(scope._binding_registry._lifecycle_state)

            # This should fail - duplicate name
            with pytest.raises(ValueError, match="already bound"):
                scope.bind("ctx", ctx2)

            # Registry should not have grown
            assert len(scope._binding_registry._lifecycle_state) == registry_size_before
            # Only original "ctx" should be in registry
            assert list(scope._binding_registry._lifecycle_state.keys()) == ["ctx"]

    def test_registry_not_updated_on_duplicate_context_id_error(self):
        """Registry should not have entry if bind fails due to duplicate context_id."""
        with Scope() as scope:
            ctx1 = DuplicateIdContext("same_id")
            ctx2 = DuplicateIdContext("same_id")

            # Successful bind
            scope.bind("ctx1", ctx1)
            assert "ctx1" in scope._binding_registry._lifecycle_state

            # Count registry entries before failed bind
            registry_size_before = len(scope._binding_registry._lifecycle_state)

            # This should fail - duplicate context_id
            with pytest.raises(ValueError, match=r"context_id.*already bound"):
                scope.bind("ctx2", ctx2)

            # Registry should not have grown
            assert len(scope._binding_registry._lifecycle_state) == registry_size_before
            # "ctx2" should NOT be in registry
            assert "ctx2" not in scope._binding_registry._lifecycle_state

    def test_successful_bind_updates_registry(self):
        """Successful bind should update both scope and registry."""
        with Scope() as scope:
            ctx = SimpleContext()

            # Registry empty before bind
            assert len(scope._binding_registry._lifecycle_state) == 0

            scope.bind("myctx", ctx)

            # Registry should have the entry
            assert "myctx" in scope._binding_registry._lifecycle_state
            # Scope should have the binding
            assert scope._scope.get_binding("myctx") is not None


# =============================================================================
# Issue 3.3.1: Partial Checkpoint Restore
# =============================================================================


class TestPartialRestore:
    """Tests for partial checkpoint restore with keep_bindings and exclude_effect_types."""

    def test_restore_basic_without_filters(self):
        """Basic restore without filters should work as before."""
        with Scope() as scope:
            ctx = SimpleContext("counter", value=0)
            scope.bind("counter", ctx)

            scope.emit(Effect(effect_type="increment", binding_name="counter"))
            cp = scope.checkpoint("savepoint")
            scope.emit(Effect(effect_type="increment", binding_name="counter"))
            scope.emit(Effect(effect_type="increment", binding_name="counter"))

            # Value should be 3 after 3 increments
            assert scope.get_context("counter").value == 3
            assert len(scope.effects) == 3

            # Restore to checkpoint
            scope.restore(cp)

            # Should be back to 1 effect, value 1
            assert len(scope.effects) == 1
            assert scope.get_context("counter").value == 1

    def test_restore_with_keep_bindings_preserves_state(self):
        """Restore with keep_bindings should preserve specified binding's current state."""
        with Scope() as scope:
            ctx1 = SimpleContext("primary", value=0)
            ctx2 = SimpleContext("secondary", value=0)
            scope.bind("primary", ctx1)
            scope.bind("secondary", ctx2)

            scope.emit(Effect(effect_type="increment", binding_name="primary"))
            cp = scope.checkpoint("savepoint")
            scope.emit(Effect(effect_type="increment", binding_name="primary"))
            scope.emit(Effect(effect_type="increment", binding_name="secondary"))

            # After checkpoint: primary=2, secondary=1
            assert scope.get_context("primary").value == 2
            assert scope.get_context("secondary").value == 1

            # Restore but keep primary's current state
            scope.restore(cp, keep_bindings=["primary"])

            # Primary should keep its value (2), secondary should reset (0)
            assert scope.get_context("primary").value == 2
            # secondary was at initial state (0) at checkpoint, replay one effect = 1
            # But wait - the effects after checkpoint are discarded, so secondary should reset to 0
            # Actually let me re-read the implementation...
            # The keep_bindings preserves the current state of the binding, not replay
            # So secondary resets to initial and replays effects from truncated stream
            # truncated stream has 1 effect (before checkpoint), but that was for primary
            assert scope.get_context("secondary").value == 0

    def test_restore_keep_bindings_preserves_new_binding(self):
        """Bindings added after checkpoint can be kept with keep_bindings."""
        with Scope() as scope:
            ctx1 = SimpleContext("first", value=10)
            scope.bind("first", ctx1)

            cp = scope.checkpoint("before_second")

            # Add new binding after checkpoint
            ctx2 = SimpleContext("second", value=20)
            scope.bind("second", ctx2)

            scope.emit(Effect(effect_type="increment", binding_name="second"))

            # Normally restore would remove "second"
            # But with keep_bindings, it should be preserved
            scope.restore(cp, keep_bindings=["second"])

            # first should still exist (was before checkpoint)
            assert scope.get_context("first").value == 10
            # second should be preserved with current value (21)
            assert scope.get_context("second").value == 21

    def test_restore_keep_bindings_unknown_binding_raises(self):
        """Specifying unknown binding in keep_bindings should raise ValueError."""
        with Scope() as scope:
            ctx = SimpleContext("known")
            scope.bind("known", ctx)
            cp = scope.checkpoint("test")

            with pytest.raises(ValueError, match=r"unknown binding.*nonexistent"):
                scope.restore(cp, keep_bindings=["nonexistent"])

    def test_restore_with_exclude_effect_types(self):
        """Restore with exclude_effect_types should skip those effects during replay."""
        with Scope() as scope:
            ctx = SimpleContext("counter", value=0)
            scope.bind("counter", ctx)

            # Create some effects of different types
            scope.emit(Effect(effect_type="increment", binding_name="counter"))  # +1 = 1
            scope.emit(Effect(effect_type="other", binding_name="counter"))  # no-op
            scope.emit(Effect(effect_type="increment", binding_name="counter"))  # +1 = 2
            cp = scope.checkpoint("after_mixed")

            scope.emit(Effect(effect_type="increment", binding_name="counter"))  # +1 = 3

            assert scope.get_context("counter").value == 3

            # Restore but skip "increment" effects during replay
            scope.restore(cp, exclude_effect_types=["increment"])

            # Only "other" effect was replayed, which is a no-op
            # So counter should be at initial value 0
            assert scope.get_context("counter").value == 0

    def test_restore_with_both_keep_and_exclude(self):
        """Can use both keep_bindings and exclude_effect_types together."""
        with Scope() as scope:
            ctx1 = SimpleContext("kept", value=0)
            ctx2 = SimpleContext("reset", value=0)
            scope.bind("kept", ctx1)
            scope.bind("reset", ctx2)

            scope.emit(Effect(effect_type="increment", binding_name="kept"))
            scope.emit(Effect(effect_type="increment", binding_name="reset"))
            cp = scope.checkpoint("mixed")

            scope.emit(Effect(effect_type="increment", binding_name="kept"))
            scope.emit(Effect(effect_type="increment", binding_name="reset"))

            # kept=2, reset=2
            assert scope.get_context("kept").value == 2
            assert scope.get_context("reset").value == 2

            # Restore: keep "kept" binding, exclude "increment" effects
            scope.restore(cp, keep_bindings=["kept"], exclude_effect_types=["increment"])

            # "kept" preserves its value (2) because of keep_bindings
            assert scope.get_context("kept").value == 2
            # "reset" is reset to initial, and no effects are replayed (excluded)
            assert scope.get_context("reset").value == 0

    def test_restore_exclude_empty_list_is_noop(self):
        """Empty exclude_effect_types list should behave like no filtering."""
        with Scope() as scope:
            ctx = SimpleContext("counter", value=0)
            scope.bind("counter", ctx)

            scope.emit(Effect(effect_type="increment", binding_name="counter"))
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="increment", binding_name="counter"))

            scope.restore(cp, exclude_effect_types=[])

            # Should be 1 (one increment replayed)
            assert scope.get_context("counter").value == 1


# =============================================================================
# Issue #26: Circular Import Cleanup
# =============================================================================


class TestCircularImportCleanup:
    """Tests verifying circular imports are resolved.

    Issue #26: Multiple local imports were scattered through method bodies.
    The fix consolidates them at module level. If this test file imports
    successfully, the circular import issue is resolved.
    """

    def test_scope_module_imports_cleanly(self):
        """Verify scope module and related imports work without circular import errors."""
        # These imports would fail if there were circular import issues
        from shepherd_core.scope.stream import Stream
        from shepherd_runtime.checkpoint import Checkpoint
        from shepherd_runtime.scope import Scope, ScopeProxy

        # Basic sanity checks
        assert Scope is ScopeProxy
        assert Checkpoint is not None
        assert Stream is not None

    def test_effect_layer_available_at_module_level(self):
        """EffectLayer should be importable from scope module level."""
        from shepherd_core.scope.stream import EffectLayer

        # Should be able to create EffectLayer
        effect = Effect(effect_type="test")
        layer = EffectLayer(effect=effect, sequence=0)
        assert layer.effect == effect
        assert layer.sequence == 0

    def test_checkpoint_available_at_runtime_owner_path(self):
        """Checkpoint should be importable from its runtime owner path."""
        from shepherd_runtime.checkpoint import Checkpoint

        with Scope() as scope:
            cp = scope.checkpoint("test")
            assert isinstance(cp, Checkpoint)

    def test_materialization_summary_available(self):
        """MaterializationSummary should be importable."""
        from shepherd_core.scope.types import MaterializationSummary

        # Should be usable
        summary = MaterializationSummary(
            effects_processed=5,
            effects_materialized=3,
            total_paths_affected=10,
            rollback_errors=(),
        )
        assert summary.effects_processed == 5


# =============================================================================
# Integration Tests
# =============================================================================


# =============================================================================
# Stream.get() for Causality Lookup
# =============================================================================


class TestStreamGet:
    """Tests for Stream.get(sequence) method."""

    def test_get_returns_effect_by_sequence(self):
        """get(sequence) returns the effect at that sequence number."""
        from shepherd_core.scope.stream import Stream

        stream = Stream()
        e0 = Effect(effect_type="first")
        e1 = Effect(effect_type="second")
        e2 = Effect(effect_type="third")

        stream = stream.append(e0).append(e1).append(e2)

        assert stream.get(0) == e0
        assert stream.get(1) == e1
        assert stream.get(2) == e2

    def test_get_returns_none_for_nonexistent_sequence(self):
        """get(sequence) returns None if sequence doesn't exist."""
        from shepherd_core.scope.stream import Stream

        stream = Stream()
        e0 = Effect(effect_type="first")
        stream = stream.append(e0)

        assert stream.get(1) is None
        assert stream.get(99) is None
        assert stream.get(-1) is None

    def test_get_on_empty_stream_returns_none(self):
        """get() on empty stream returns None."""
        from shepherd_core.scope.stream import Stream

        stream = Stream()
        assert stream.get(0) is None

    def test_get_enables_causality_chain_traversal(self):
        """get() can be used to traverse caused_by chain."""
        from shepherd_core.scope.stream import Stream

        # Create effects with distinct effect_types for identification
        effect_0 = Effect(effect_type="tool_call_started")
        effect_1 = Effect(effect_type="workspace_patch_captured")
        effect_2 = Effect(effect_type="tool_call_completed")

        stream = Stream().append(effect_0).append(effect_1).append(effect_2)

        # Verify get() returns the exact effect objects
        assert stream.get(0) is effect_0
        assert stream.get(1) is effect_1
        assert stream.get(2) is effect_2

        # Verify effect_type is correct
        assert stream.get(0).effect_type == "tool_call_started"
        assert stream.get(1).effect_type == "workspace_patch_captured"
        assert stream.get(2).effect_type == "tool_call_completed"

        # This enables causality pattern:
        # patch = stream.get(1)
        # cause = stream.get(patch.caused_by)  # if patch has caused_by field


class TestPartialRestoreIntegration:
    """Integration tests for partial restore scenarios."""

    def test_error_recovery_preserving_workspace(self):
        """Simulate error recovery that preserves workspace state.

        This is a common use case: an operation fails, but we want to
        keep the workspace binding's accumulated state while rolling
        back other effects.
        """
        with Scope() as scope:
            workspace = SimpleContext("workspace", value=100)
            temp = SimpleContext("temp", value=0)
            scope.bind("workspace", workspace)
            scope.bind("temp", temp)

            # Workspace gets modified
            scope.emit(Effect(effect_type="increment", binding_name="workspace"))

            cp = scope.checkpoint("before_risky")

            # Risky operation modifies temp
            scope.emit(Effect(effect_type="increment", binding_name="temp"))
            scope.emit(Effect(effect_type="increment", binding_name="temp"))

            # Simulate failure - need to roll back temp but keep workspace
            scope.restore(cp, keep_bindings=["workspace"])

            # workspace preserved its state (101)
            assert scope.get_context("workspace").value == 101
            # temp was reset (effects discarded, replays from checkpoint which has 0)
            # Actually temp starts at 0, and the effects before checkpoint were only for workspace
            # So temp should be 0
            assert scope.get_context("temp").value == 0

    def test_selective_effect_rollback(self):
        """Simulate selective rollback of certain effect types.

        Use case: Roll back tool calls but keep state changes.
        """
        with Scope() as scope:
            ctx = SimpleContext("counter", value=0)
            scope.bind("counter", ctx)

            # Mix of effect types - using basic Effect with different effect_type
            # "set_value" would need special handling in SimpleContext, so we use increment
            scope.emit(Effect(effect_type="increment", binding_name="counter"))  # +1 = 1
            scope.emit(Effect(effect_type="tool_call", binding_name="counter"))  # no-op
            scope.emit(Effect(effect_type="increment", binding_name="counter"))  # +1 = 2

            cp = scope.checkpoint("mixed_effects")

            scope.emit(Effect(effect_type="tool_call", binding_name="counter"))  # no-op

            assert scope.get_context("counter").value == 2

            # Restore but don't replay tool_call effects
            # This should replay only the two increment effects
            scope.restore(cp, exclude_effect_types=["tool_call"])

            # Both increments should be applied, tool_call was skipped
            assert scope.get_context("counter").value == 2
