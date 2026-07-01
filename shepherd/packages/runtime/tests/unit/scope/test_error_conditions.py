"""Tests for scope error conditions and edge cases.

This module tests error paths in Scope operations:
- Checkpoint restore with invalid checkpoint
- Materialization partial failures
- bind() failures and registry consistency
"""

import pytest
from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect
from shepherd_core.errors import BindingNotFoundError, ContainmentError
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Contexts
# =============================================================================


class SimpleTestContext(ExecutionContext):
    """Simple context for testing."""

    def __init__(self, name: str = "test", count: int = 0):
        self._name = name
        self._count = count

    @property
    def context_id(self) -> str:
        return f"simple:{self._name}"

    @property
    def count(self) -> int:
        return self._count

    def apply_effect(self, effect: Effect) -> "SimpleTestContext":
        if effect.effect_type == "increment":
            return SimpleTestContext(name=self._name, count=self._count + 1)
        return self


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
# Tests: Checkpoint Restore Error Conditions
# =============================================================================


class TestCheckpointRestoreErrors:
    """Tests for checkpoint restore error conditions."""

    def test_restore_checkpoint_from_wrong_scope_raises(self):
        """Restoring a checkpoint from a different scope should raise ValueError."""
        with Scope() as scope1:
            cp = scope1.checkpoint("test")

            with Scope() as scope2, pytest.raises(ValueError, match="belongs to different scope"):
                scope2.restore(cp)

    def test_restore_checkpoint_twice_raises(self):
        """Restoring the same checkpoint twice should raise ValueError."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="test"))
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="after"))

            scope.restore(cp)

            with pytest.raises(ValueError, match="already restored"):
                scope.restore(cp)

    def test_restore_stale_checkpoint_raises(self):
        """Restoring a stale checkpoint (invalidated by earlier restore) should raise.

        When an earlier checkpoint is restored, later checkpoints become stale
        because their position exceeds the current stream length.
        """
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp1 = scope.checkpoint("first")

            scope.emit(Effect(effect_type="e1"))
            cp2 = scope.checkpoint("second")  # Position 2

            scope.emit(Effect(effect_type="e2"))

            # Restore to first checkpoint - invalidates cp2
            scope.restore(cp1)
            assert len(scope.effects) == 1  # Only e0 remains

            # cp2 is now stale (position 2 > stream length 1)
            assert cp2.is_stale

            with pytest.raises(ValueError, match="stale"):
                scope.restore(cp2)

    def test_restore_after_partial_materialize_raises(self):
        """Cannot restore checkpoint if effects after it were materialized."""
        with Scope() as scope:
            cp = scope.checkpoint("before_materialize")

            scope.emit(Effect(effect_type="to_materialize"))

            # Simulate partial materialization by advancing watermark
            scope._materialized_index = len(scope.effects)

            with pytest.raises(ContainmentError, match="materialized"):
                scope.restore(cp)

    def test_checkpoint_effects_since_empty_after_restore(self):
        """After restore, checkpoint's effects_since should be empty."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp = scope.checkpoint("test")
            scope.emit(Effect(effect_type="e1"))
            scope.emit(Effect(effect_type="e2"))

            assert len(cp.effects_since) == 2

            scope.restore(cp)

            # After restore, effects_since should be empty
            assert len(cp.effects_since) == 0

    def test_checkpoint_position_negative_truncation_not_allowed(self):
        """Stream truncate_to with negative position should raise."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))

            # Direct stream access - should raise on negative
            with pytest.raises(ValueError, match="must be >= 0"):
                scope._scope._stream.truncate_to(-1)


# =============================================================================
# Tests: Materialization Error Conditions
# =============================================================================


class TestMaterializationErrors:
    """Tests for materialization error conditions."""

    def test_materialize_from_child_scope_raises(self):
        """Cannot call materialize() from a child scope."""
        with Scope() as parent:
            child = parent.child()

            with pytest.raises(RuntimeError, match="root scope"):
                child.materialize()

    def test_materialize_discarded_scope_raises(self):
        """Cannot materialize a discarded scope."""
        with Scope() as scope:
            forked = scope.fork()
            forked.emit(Effect(effect_type="test"))
            forked.discard()

            with pytest.raises(ContainmentError, match="discarded"):
                forked.materialize()

    def test_discard_after_materialize_raises(self):
        """Cannot discard a scope after effects have been materialized."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="test"))

            # Materialize (this sets _is_materialized = True)
            scope.materialize()

            with pytest.raises(ContainmentError, match="escaped via materialize"):
                scope.discard()

    def test_merge_discarded_child_raises(self):
        """Cannot merge a child scope that was discarded."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))
            child.discard()

            from shepherd_core.foundation.errors import ScopeError

            with pytest.raises(ScopeError, match="discarded"):
                scope.merge(child)


# =============================================================================
# Tests: bind() Error Conditions
# =============================================================================


class TestBindErrors:
    """Tests for bind() error conditions and registry consistency."""

    def test_bind_duplicate_name_raises(self):
        """Binding with duplicate name should raise ValueError."""
        with Scope() as scope:
            ctx1 = SimpleTestContext("first")
            ctx2 = SimpleTestContext("second")

            scope.bind("ctx", ctx1)

            with pytest.raises(ValueError, match="already bound"):
                scope.bind("ctx", ctx2)

    def test_bind_duplicate_context_id_raises(self):
        """Binding contexts with same context_id should raise ValueError."""
        with Scope() as scope:
            ctx1 = DuplicateIdContext("same_id")
            ctx2 = DuplicateIdContext("same_id")

            scope.bind("ctx1", ctx1)

            with pytest.raises(ValueError, match=r"context_id.*already bound"):
                scope.bind("ctx2", ctx2)

    def test_bind_string_without_context_raises(self):
        """Calling bind() with just a string should raise TypeError."""
        with Scope() as scope, pytest.raises(TypeError, match="no context"):
            scope.bind("name")

    def test_bind_context_without_binding_name_raises(self):
        """Binding context without __binding_name__ using single-arg form should raise."""
        with Scope() as scope:
            ctx = SimpleTestContext()  # No __binding_name__

            with pytest.raises(ValueError, match="no __binding_name__"):
                scope.bind(ctx)

    def test_bind_non_string_first_arg_raises(self):
        """Calling bind(non_string, context) should raise TypeError."""
        with Scope() as scope:
            ctx = SimpleTestContext()

            with pytest.raises(TypeError, match="must be a string"):
                scope.bind(123, ctx)

    def test_get_context_nonexistent_raises(self):
        """Getting nonexistent context should raise BindingNotFoundError."""
        with Scope() as scope:
            ctx = SimpleTestContext()
            scope.bind("exists", ctx)

            with pytest.raises(BindingNotFoundError):
                scope.get_context("nonexistent")

    def test_update_context_nonexistent_raises(self):
        """Updating nonexistent context should raise BindingNotFoundError."""
        with Scope() as scope:
            ctx = SimpleTestContext()
            scope.bind("exists", ctx)

            with pytest.raises(BindingNotFoundError):
                scope.update_context("nonexistent", ctx)

    def test_mark_binding_lifecycle_nonexistent_raises(self):
        """Marking lifecycle on nonexistent binding should raise BindingNotFoundError."""
        with Scope() as scope:
            ctx = SimpleTestContext()
            scope.bind("exists", ctx)

            with pytest.raises(BindingNotFoundError):
                scope.mark_binding_lifecycle("nonexistent", is_prepared=True)

    def test_get_binding_nonexistent_raises(self):
        """Getting nonexistent binding should raise BindingNotFoundError."""
        with Scope() as scope:
            ctx = SimpleTestContext()
            scope.bind("exists", ctx)

            with pytest.raises(BindingNotFoundError):
                scope.get_binding("nonexistent")


# =============================================================================
# Tests: Provider Registry Errors
# =============================================================================


class TestProviderRegistryErrors:
    """Tests for provider registry error conditions."""

    def test_get_nonexistent_provider_raises(self):
        """Getting nonexistent provider should raise ProviderNotFoundError."""
        from shepherd_core.errors import ProviderNotFoundError

        with Scope() as scope, pytest.raises(ProviderNotFoundError):
            scope.get_provider("nonexistent")

    def test_get_default_provider_when_none_registered_raises(self):
        """Getting default provider when none registered should raise."""
        from shepherd_core.errors import ProviderNotFoundError

        with Scope() as scope, pytest.raises(ProviderNotFoundError, match="default"):
            scope.get_provider()


# =============================================================================
# Tests: Stream Error Conditions
# =============================================================================


class TestStreamErrors:
    """Tests for stream error conditions."""

    def test_direct_without_scope_context_raises(self):
        """Calling direct() on stream without scope context should raise."""
        from shepherd_core.scope.stream import Stream

        stream = Stream()
        stream = stream.append(Effect(effect_type="test"))

        with pytest.raises(ValueError, match="scope-bound stream"):
            stream.direct()

    def test_by_depth_without_scope_context_raises(self):
        """Calling by_depth() on stream without scope context should raise."""
        from shepherd_core.scope.stream import Stream

        stream = Stream()
        stream = stream.append(Effect(effect_type="test"))

        with pytest.raises(ValueError, match="scope-bound stream"):
            stream.by_depth(1)

    def test_by_depth_negative_raises(self):
        """Calling by_depth() with negative depth should raise."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="test"))

            with pytest.raises(ValueError, match="must be >= 0"):
                scope.effects.by_depth(-1)

    def test_truncate_to_negative_raises(self):
        """Calling truncate_to() with negative position should raise."""
        from shepherd_core.scope.stream import Stream

        stream = Stream()
        stream = stream.append(Effect(effect_type="test"))

        with pytest.raises(ValueError, match="must be >= 0"):
            stream.truncate_to(-1)


# =============================================================================
# Tests: Scope Lifecycle Errors
# =============================================================================


class TestScopeLifecycleErrors:
    """Tests for scope lifecycle error conditions."""

    def test_require_scope_when_none_active_raises(self):
        """require_scope() should raise when no scope is active."""
        from shepherd_runtime.scope import require_scope

        # Outside of any scope context
        with pytest.raises(RuntimeError, match="No active scope"):
            require_scope()

    def test_context_ref_after_scope_closed_raises(self):
        """Accessing ContextRef after scope is closed should raise."""
        # Create scope and get ref
        scope = Scope()
        scope.__enter__()

        ctx = SimpleTestContext("test")
        ref = scope.bind("ctx", ctx)

        # Can access while scope is open
        assert ref.context_id == "simple:test"

        # Close scope
        scope.__exit__(None, None, None)

        # Should raise after scope is closed
        with pytest.raises(RuntimeError, match="closed"):
            _ = ref.context_id


# =============================================================================
# Tests: Effect Application Errors
# =============================================================================


class TestEffectApplicationErrors:
    """Tests for effect application edge cases."""

    def test_apply_effect_to_nonexistent_binding_is_noop(self):
        """Applying effect with unmatched binding_name should be a no-op.

        This is not an error - effects may target bindings that don't exist
        in the current scope (e.g., child scope effects propagating to parent).
        """
        with Scope() as scope:
            ctx = SimpleTestContext("test", count=0)
            scope.bind("ctx", ctx)

            # Effect targets a different binding
            effect = Effect(effect_type="increment", binding_name="nonexistent")
            scope.emit(effect)

            # Original context should be unchanged
            assert scope.get_context("ctx").count == 0

    def test_apply_effect_to_nonexistent_context_id_is_noop(self):
        """Applying effect with unmatched context_id should be a no-op."""
        with Scope() as scope:
            ctx = SimpleTestContext("test", count=0)
            scope.bind("ctx", ctx)

            # Create effect with different context_id (no binding_name)
            effect = Effect(effect_type="increment", context_id="other:context")
            scope.emit(effect)

            # Original context should be unchanged
            assert scope.get_context("ctx").count == 0


# =============================================================================
# Tests: Fork and Merge Error Conditions
# =============================================================================


class TestForkMergeErrors:
    """Tests for fork/merge error conditions."""

    def test_merge_effects_deprecation_warning(self):
        """merge_effects() should emit deprecation warning."""
        import warnings

        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                scope.merge_effects(child.effects)

                assert len(w) == 1
                assert issubclass(w[0].category, DeprecationWarning)
                assert "merge()" in str(w[0].message)

    def test_discard_idempotent(self):
        """Calling discard() multiple times should be idempotent."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))

            child.discard()
            child.discard()  # Should not raise
            child.discard()  # Should not raise

            assert child.is_discarded


# =============================================================================
# Tests: Commit Containment Bug Fixes
# =============================================================================


class TestCommitContainment:
    """Tests for containment after commit() — bug fixes.

    Previously, commit() performed real-world I/O but never updated
    escape bookkeeping (_is_materialized / _materialized_index), so
    discard(), checkpoint restore, and merge all incorrectly succeeded
    after a commit.
    """

    def setup_method(self) -> None:
        """Register a mock materializer."""
        from dataclasses import dataclass, field, replace
        from pathlib import Path
        from typing import Self

        from shepherd_core.types import ReversibilityLevel
        from shepherd_runtime.materialization import (
            MaterializationIntent,
            MaterializationResult,
            clear_materializer_registry,
            register_materializer,
        )

        clear_materializer_registry()

        @dataclass(frozen=True)
        class _MockCtx:
            context_id: str = "mock:test"
            reversibility: ReversibilityLevel = ReversibilityLevel.AUTO
            _pending: bool = True
            _target_path: Path = field(default_factory=Path)

            @property
            def has_pending_changes(self) -> bool:
                return self._pending

            def materialization_intent(self) -> MaterializationIntent:
                return MaterializationIntent(
                    context_type="_MockCtx",
                    context_id=self.context_id,
                    target_path=self._target_path,
                )

            def with_materialized(self, result: MaterializationResult) -> Self:
                return replace(self, _pending=False)

            def apply_effect(self, effect: Effect) -> Self:
                return self

        self._MockCtx = _MockCtx

        class _Materializer:
            def materialize(self, intent: MaterializationIntent) -> MaterializationResult:
                return MaterializationResult.ok(paths_affected=("f.txt",), committed="true")

            def can_rollback(self) -> bool:
                return False

        register_materializer("_MockCtx", _Materializer())

    def test_discard_after_commit_raises(self):
        """discard() must fail after commit() — effects have escaped."""
        with Scope() as scope:
            scope.bind("ws", self._MockCtx())
            scope.commit()

            with pytest.raises(ContainmentError, match="escaped"):
                scope.discard()

    def test_checkpoint_restore_after_commit_raises(self):
        """Checkpoint restore must fail when commit() has escaped effects past the checkpoint."""
        with Scope() as scope:
            scope.bind("ws", self._MockCtx())
            cp = scope.checkpoint("before_commit")
            scope.commit()

            with pytest.raises(ContainmentError, match="materialized"):
                scope.restore(cp)

    def test_merge_committed_fork_raises(self):
        """merge() must fail when the child fork has committed (escaped containment)."""
        with Scope() as scope:
            child = scope.fork()
            child.bind("ws", self._MockCtx())
            child.commit()

            with pytest.raises(ContainmentError, match="escaped"):
                scope.merge(child)

    def test_merge_materialized_fork_raises(self):
        """merge() must fail when the child fork has materialized effects."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))
            child.materialize()

            with pytest.raises(ContainmentError, match="escaped"):
                scope.merge(child)

    def test_is_materialized_true_after_commit(self):
        """is_materialized should be True after a successful commit()."""
        with Scope() as scope:
            scope.bind("ws", self._MockCtx())
            assert scope.is_materialized is False

            scope.commit()

            assert scope.is_materialized is True

    def test_commit_no_pending_changes_does_not_escape(self):
        """commit() with no pending changes should not mark scope as escaped."""
        with Scope() as scope:
            # No bindings with pending changes
            scope.commit()

            assert scope.is_materialized is False
            # discard should still work
            scope.discard()


# =============================================================================
# Tests: ImmutableScope Error Conditions
# =============================================================================


class TestImmutableScopeErrors:
    """Tests for ImmutableScope error conditions."""

    def test_with_updated_context_nonexistent_raises(self):
        """Updating nonexistent context in ImmutableScope should raise."""
        from shepherd_core.scope.model import ImmutableScope

        scope = ImmutableScope()
        ctx = SimpleTestContext("test")

        with pytest.raises(BindingNotFoundError):
            scope.with_updated_context("nonexistent", ctx)
