"""Tests that the runtime Scope satisfies the public protocol surface."""

from shepherd_core.scope.types import MaterializationSummary
from shepherd_runtime.scope import Scope


class TestScopeProtocolCompliance:
    """Tests that Scope satisfies the expected runtime protocol surface."""

    def test_scope_has_id_property(self):
        """Scope has id property."""
        with Scope() as scope:
            assert hasattr(scope, "id")
            assert isinstance(scope.id, str)
            assert scope.id.startswith("scope_")

    def test_scope_has_effects_property(self):
        """Scope has effects property returning a stream-like value."""
        with Scope() as scope:
            assert hasattr(scope, "effects")
            assert hasattr(scope.effects, "layers")
            assert hasattr(scope.effects, "append")

    def test_scope_has_is_discarded_property(self):
        """Scope has is_discarded property."""
        with Scope() as scope:
            assert hasattr(scope, "is_discarded")
            assert isinstance(scope.is_discarded, bool)
            assert scope.is_discarded is False

    def test_scope_has_is_materialized_property(self):
        """Scope has is_materialized property."""
        with Scope() as scope:
            assert hasattr(scope, "is_materialized")
            assert isinstance(scope.is_materialized, bool)
            assert scope.is_materialized is False

    def test_scope_has_fork_method(self):
        """Scope has fork() method."""
        with Scope() as scope:
            assert hasattr(scope, "fork")
            assert callable(scope.fork)

    def test_scope_has_merge_method(self):
        """Scope has merge() method."""
        with Scope() as scope:
            assert hasattr(scope, "merge")
            assert callable(scope.merge)

    def test_scope_has_discard_method(self):
        """Scope has discard() method."""
        with Scope() as scope:
            assert hasattr(scope, "discard")
            assert callable(scope.discard)

    def test_scope_has_materialize_method(self):
        """Scope has sync materialize() method."""
        with Scope() as scope:
            assert hasattr(scope, "materialize")
            assert callable(scope.materialize)
            result = scope.materialize()
            assert isinstance(result, MaterializationSummary)

    def test_scope_has_emit_method(self):
        """Scope has emit() method."""
        with Scope() as scope:
            assert hasattr(scope, "emit")
            assert callable(scope.emit)
