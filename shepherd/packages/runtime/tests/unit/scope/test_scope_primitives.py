"""Tests for scope primitives: fork, merge, discard, materialize.

These are the four fundamental operations that form the foundation
of Shepherd's speculative execution model.
"""

import pytest
from shepherd_core.effects import Effect
from shepherd_core.errors import ContainmentError
from shepherd_core.foundation.errors import ScopeError
from shepherd_runtime.scope import Scope


class TestFork:
    """Tests for scope.fork() primitive."""

    def test_fork_creates_independent_scope(self):
        """fork() creates a scope with independent effect stream."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="parent_effect"))

            child = scope.fork()
            child.emit(Effect(effect_type="child_effect"))

            # Child has both parent's snapshot and its own effect
            # Actually, fork() copies bindings but not effects
            assert len(child.effects) == 1
            assert len(scope.effects) == 1  # Parent unchanged

    def test_fork_copies_bindings(self):
        """fork() copies current bindings snapshot."""
        from shepherd_tests import MockProvider

        with Scope() as scope:
            scope.register_provider("default", MockProvider(), default=True)

            child = scope.fork()

            # Child has access to parent's provider
            assert child.has_provider("default")

    def test_fork_effects_do_not_propagate(self):
        """Effects in fork do NOT propagate to parent until merge."""
        with Scope() as scope:
            initial_count = len(scope.effects)

            child = scope.fork()
            child.emit(Effect(effect_type="test"))

            # Parent stream unchanged
            assert len(scope.effects) == initial_count

    def test_fork_returns_scope_instance(self):
        """fork() returns a ScopeProxy instance."""
        with Scope() as scope:
            child = scope.fork()
            assert isinstance(child, Scope)


class TestMerge:
    """Tests for scope.merge() primitive."""

    def test_merge_propagates_effects(self):
        """merge() copies child effects to parent."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="child_effect_1"))
            child.emit(Effect(effect_type="child_effect_2"))

            initial_count = len(scope.effects)
            scope.merge(child)

            assert len(scope.effects) == initial_count + 2

    def test_merge_preserves_effect_order(self):
        """merge() preserves effect ordering."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="first"))
            child.emit(Effect(effect_type="second"))

            scope.merge(child)

            effects = [layer.effect.effect_type for layer in scope.effects]
            assert "first" in effects
            assert "second" in effects
            assert effects.index("first") < effects.index("second")

    def test_cannot_merge_discarded_scope(self):
        """merge() raises ScopeError if child was discarded."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))
            child.discard()

            with pytest.raises(ScopeError, match="discarded"):
                scope.merge(child)


class TestDiscard:
    """Tests for scope.discard() primitive."""

    def test_discard_abandons_effects(self):
        """discard() clears the scope's effects."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))
            assert len(child.effects) == 1

            child.discard()

            assert len(child.effects) == 0

    def test_discard_sets_is_discarded(self):
        """discard() sets is_discarded to True."""
        with Scope() as scope:
            child = scope.fork()
            assert child.is_discarded is False

            child.discard()

            assert child.is_discarded is True

    def test_discard_is_idempotent(self):
        """discard() can be called multiple times safely."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))

            child.discard()
            child.discard()  # Second call should not raise
            child.discard()  # Third call should not raise

            assert child.is_discarded is True

    def test_discard_parent_unchanged(self):
        """discard() leaves parent scope unchanged."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="parent_effect"))
            initial_count = len(scope.effects)

            child = scope.fork()
            child.emit(Effect(effect_type="child_effect"))
            child.discard()

            # Parent unchanged
            assert len(scope.effects) == initial_count


class TestMaterialize:
    """Tests for scope.materialize() primitive."""

    def test_materialize_sets_is_materialized(self):
        """materialize() sets is_materialized to True."""
        with Scope() as scope:
            assert scope.is_materialized is False

            # New API: sync, returns MaterializationSummary
            summary = scope.materialize()

            assert scope.is_materialized is True
            # Summary indicates no effects were materialized (empty scope)
            assert summary.effects_processed == 0

    def test_cannot_discard_after_materialize(self):
        """discard() raises ContainmentError after materialize()."""
        with Scope() as scope:
            scope.materialize()

            with pytest.raises(ContainmentError, match="escaped"):
                scope.discard()


class TestSpeculativeExecution:
    """Integration tests for speculative execution patterns."""

    def test_fork_approve_merge_pattern(self):
        """Test the fork -> execute -> approve -> merge pattern."""
        with Scope() as scope:
            # Fork for speculative execution
            child = scope.fork()

            # Execute in fork
            child.emit(Effect(effect_type="speculative_work"))
            child.emit(Effect(effect_type="more_work"))

            # Approve: merge effects
            scope.merge(child)

            # Effects are now in parent
            effect_types = [layer.effect.effect_type for layer in scope.effects]
            assert "speculative_work" in effect_types
            assert "more_work" in effect_types

    def test_fork_reject_discard_pattern(self):
        """Test the fork -> execute -> reject -> discard pattern."""
        with Scope() as scope:
            initial_count = len(scope.effects)

            # Fork for speculative execution
            child = scope.fork()

            # Execute in fork
            child.emit(Effect(effect_type="bad_work"))
            child.emit(Effect(effect_type="more_bad_work"))

            # Reject: discard effects
            child.discard()

            # Parent unchanged
            assert len(scope.effects) == initial_count

    def test_multiple_speculative_branches(self):
        """Test multiple fork branches with selective merge."""
        with Scope() as scope:
            # Create two speculative branches
            branch_a = scope.fork()
            branch_b = scope.fork()

            # Execute different work in each
            branch_a.emit(Effect(effect_type="approach_a"))
            branch_b.emit(Effect(effect_type="approach_b"))

            # Choose branch A, discard branch B
            scope.merge(branch_a)
            branch_b.discard()

            # Only branch A's effects are in parent
            effect_types = [layer.effect.effect_type for layer in scope.effects]
            assert "approach_a" in effect_types
            assert "approach_b" not in effect_types


class TestContainmentModel:
    """Tests for the containment model: SANDBOX -> SCOPE -> MATERIALIZED -> ESCAPED."""

    def test_contained_effects_can_be_discarded(self):
        """Effects in SCOPE containment level can be discarded."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="contained"))

            # Can discard because effects haven't escaped
            child.discard()
            assert child.is_discarded is True

    def test_escaped_effects_cannot_be_discarded(self):
        """Effects that have escaped via materialize cannot be discarded."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="will_escape"))

            # Materialize causes effects to escape (sync API)
            scope.materialize()

            # Now discard should fail - effects have escaped
            with pytest.raises(ContainmentError):
                scope.discard()

    def test_merge_before_discard_succeeds(self):
        """merge() before discard() works correctly."""
        with Scope() as scope:
            child = scope.fork()
            child.emit(Effect(effect_type="test"))

            # Can merge before discard
            scope.merge(child)

            # Child can still be discarded (already merged)
            # This clears remaining state but doesn't affect parent
            # Actually after merge the child's effects were already propagated
            # so discard just clears the local copy
            child.discard()
            assert child.is_discarded is True
