"""Tests for MaterializerRegistry - effect-type-based materializer dispatch.

These tests verify that MaterializerRegistry properly dispatches materialization
to registered materializers based on effect type.
"""

from __future__ import annotations

from typing import Literal

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.effect_materialization import (
    MaterializationResult,
    MaterializerRegistry,
    ReversalError,
    get_materializer,
    get_materializer_registry,
    register_materializer,
    reset_materializer_registry,
)

# =============================================================================
# Test Effect Types
# =============================================================================


class MaterializerTestEffect(Effect):
    """Test effect type for materializer tests."""

    effect_type: Literal["materializer_test_effect"] = "materializer_test_effect"
    value: str = ""


class MaterializerOtherEffect(Effect):
    """Another test effect type for materializer tests."""

    effect_type: Literal["materializer_other_effect"] = "materializer_other_effect"


class MaterializerChildEffect(MaterializerTestEffect):
    """Child effect type for MRO testing."""

    effect_type: Literal["materializer_child_effect"] = "materializer_child_effect"


# =============================================================================
# Mock Materializer
# =============================================================================


class MockMaterializer:
    """Mock materializer for testing."""

    def __init__(
        self,
        effect_cls: type = MaterializerTestEffect,
        *,
        should_fail: bool = False,
        can_reverse: bool = True,
    ):
        self._effect_cls = effect_cls
        self._should_fail = should_fail
        self._can_reverse = can_reverse
        self.materialized_effects: list[Effect] = []
        self.reversed_effects: list[Effect] = []

    @property
    def effect_type(self) -> type:
        return self._effect_cls

    def materialize(self, effect: Effect) -> MaterializationResult:
        if self._should_fail:
            return MaterializationResult.fail("Mock failure")
        self.materialized_effects.append(effect)
        return MaterializationResult.ok(paths_affected=("/mock/path",))

    def can_reverse(self, effect: Effect) -> bool:
        return self._can_reverse

    def reverse(self, effect: Effect) -> None:
        if not self._can_reverse:
            raise ReversalError(effect, "Cannot reverse")
        self.reversed_effects.append(effect)


# =============================================================================
# Tests
# =============================================================================


class TestMaterializerRegistryBasics:
    """Tests for basic registry operations."""

    def test_register_and_get(self):
        """Can register and retrieve a materializer."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer()

        registry.register(materializer)

        effect = MaterializerTestEffect()
        assert registry.get(effect) is materializer

    def test_register_replaces_existing(self):
        """Registering for same type replaces existing materializer."""
        registry = MaterializerRegistry()
        materializer1 = MockMaterializer()
        materializer2 = MockMaterializer()

        registry.register(materializer1)
        registry.register(materializer2)

        effect = MaterializerTestEffect()
        assert registry.get(effect) is materializer2

    def test_unregister(self):
        """Can unregister a materializer."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer()

        registry.register(materializer)
        removed = registry.unregister(MaterializerTestEffect)

        assert removed is materializer
        assert registry.get(MaterializerTestEffect()) is None

    def test_unregister_not_found(self):
        """Unregistering non-existent type returns None."""
        registry = MaterializerRegistry()

        removed = registry.unregister(MaterializerTestEffect)

        assert removed is None

    def test_has_materializer(self):
        """has_materializer checks existence correctly."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer()

        assert not registry.has_materializer(MaterializerTestEffect())

        registry.register(materializer)

        assert registry.has_materializer(MaterializerTestEffect())
        assert not registry.has_materializer(MaterializerOtherEffect())

    def test_registered_types(self):
        """registered_types returns list of registered effect types."""
        registry = MaterializerRegistry()
        materializer1 = MockMaterializer(MaterializerTestEffect)
        materializer2 = MockMaterializer(MaterializerOtherEffect)

        registry.register(materializer1)
        registry.register(materializer2)

        types = registry.registered_types()

        assert MaterializerTestEffect in types
        assert MaterializerOtherEffect in types
        assert len(types) == 2

    def test_len_and_contains(self):
        """__len__ and __contains__ work correctly."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer()

        assert len(registry) == 0
        assert MaterializerTestEffect not in registry

        registry.register(materializer)

        assert len(registry) == 1
        assert MaterializerTestEffect in registry


class TestMROLookup:
    """Tests for MRO-based materializer lookup."""

    def test_exact_type_match(self):
        """Exact type match takes precedence."""
        registry = MaterializerRegistry()
        parent_materializer = MockMaterializer(MaterializerTestEffect)
        child_materializer = MockMaterializer(MaterializerChildEffect)

        registry.register(parent_materializer)
        registry.register(child_materializer)

        effect = MaterializerChildEffect()
        assert registry.get(effect) is child_materializer

    def test_mro_fallback(self):
        """Falls back to parent type materializer via MRO."""
        registry = MaterializerRegistry()
        parent_materializer = MockMaterializer(MaterializerTestEffect)

        registry.register(parent_materializer)

        # No materializer for child, should fall back to parent
        effect = MaterializerChildEffect()
        assert registry.get(effect) is parent_materializer

    def test_mro_no_match(self):
        """Returns None when no materializer matches in MRO."""
        registry = MaterializerRegistry()
        other_materializer = MockMaterializer(MaterializerOtherEffect)

        registry.register(other_materializer)

        effect = MaterializerTestEffect()
        assert registry.get(effect) is None


class TestMaterialize:
    """Tests for the materialize() method."""

    def test_materialize_dispatches(self):
        """materialize() dispatches to correct materializer."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer()

        registry.register(materializer)

        effect = MaterializerTestEffect()
        result = registry.materialize(effect)

        assert result.success
        assert effect in materializer.materialized_effects

    def test_materialize_returns_result(self):
        """materialize() returns MaterializationResult."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer()

        registry.register(materializer)

        result = registry.materialize(MaterializerTestEffect())

        assert isinstance(result, MaterializationResult)
        assert result.success
        assert result.paths_affected == ("/mock/path",)

    def test_materialize_unregistered_returns_ok(self):
        """Unregistered effects are informational (no-op, returns success)."""
        registry = MaterializerRegistry()

        result = registry.materialize(MaterializerTestEffect())

        assert result.success
        assert result.paths_affected == ()

    def test_materialize_failure(self):
        """Failed materialization returns failure result."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer(should_fail=True)

        registry.register(materializer)

        result = registry.materialize(MaterializerTestEffect())

        assert not result.success
        assert "Mock failure" in result.error

    def test_materialize_exception_handled(self):
        """Exceptions during materialization are caught and returned as failure."""
        registry = MaterializerRegistry()

        class RaisingMaterializer:
            @property
            def effect_type(self) -> type:
                return MaterializerTestEffect

            def materialize(self, effect: Effect) -> MaterializationResult:
                raise RuntimeError("Unexpected error")

            def can_reverse(self, effect: Effect) -> bool:
                return False

            def reverse(self, effect: Effect) -> None:
                pass

        registry.register(RaisingMaterializer())

        result = registry.materialize(MaterializerTestEffect())

        assert not result.success
        assert "Unexpected error" in result.error


class TestReversibility:
    """Tests for can_reverse() and reverse() methods."""

    def test_can_reverse_delegates(self):
        """can_reverse() delegates to materializer."""
        registry = MaterializerRegistry()
        reversible = MockMaterializer(can_reverse=True)
        irreversible = MockMaterializer(MaterializerOtherEffect, can_reverse=False)

        registry.register(reversible)
        registry.register(irreversible)

        assert registry.can_reverse(MaterializerTestEffect())
        assert not registry.can_reverse(MaterializerOtherEffect())

    def test_can_reverse_unregistered_returns_false(self):
        """can_reverse() returns False for unregistered effects."""
        registry = MaterializerRegistry()

        assert not registry.can_reverse(MaterializerTestEffect())

    def test_reverse_dispatches(self):
        """reverse() dispatches to materializer."""
        registry = MaterializerRegistry()
        materializer = MockMaterializer()

        registry.register(materializer)

        effect = MaterializerTestEffect()
        registry.reverse(effect)

        assert effect in materializer.reversed_effects

    def test_reverse_unregistered_raises(self):
        """reverse() raises ValueError for unregistered effects."""
        registry = MaterializerRegistry()

        with pytest.raises(ValueError) as exc_info:
            registry.reverse(MaterializerTestEffect())

        assert "No materializer registered" in str(exc_info.value)


class TestGlobalRegistry:
    """Tests for global registry functions."""

    def setup_method(self):
        """Reset global registry before each test."""
        reset_materializer_registry()

    def teardown_method(self):
        """Reset global registry after each test."""
        reset_materializer_registry()

    def test_get_materializer_registry_creates_once(self):
        """get_materializer_registry creates singleton."""
        registry1 = get_materializer_registry()
        registry2 = get_materializer_registry()

        assert registry1 is registry2

    def test_reset_materializer_registry(self):
        """reset_materializer_registry clears and creates new."""
        registry1 = get_materializer_registry()
        reset_materializer_registry()
        registry2 = get_materializer_registry()

        assert registry1 is not registry2

    def test_register_materializer_global(self):
        """register_materializer uses global registry."""
        materializer = MockMaterializer()

        register_materializer(materializer)

        assert MaterializerTestEffect in get_materializer_registry()

    def test_get_materializer_global(self):
        """get_materializer uses global registry."""
        materializer = MockMaterializer()
        register_materializer(materializer)

        result = get_materializer(MaterializerTestEffect())

        assert result is materializer
