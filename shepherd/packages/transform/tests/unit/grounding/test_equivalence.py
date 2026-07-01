"""Tests for equivalence comparison functions.

These tests verify:
1. EquivalenceLevel enum values
2. compare_strict() - exact match required
3. compare_semantic() - order-independent, extra outputs allowed
4. compare_outcome() - value-focused with type coercion
5. compare_relaxed() - only important fields matter
6. compare_at_level() - routing to appropriate comparer
"""

from shepherd_transform.grounding import (
    EquivalenceLevel,
    EquivalenceResult,
    compare_at_level,
    compare_outcome,
    compare_relaxed,
    compare_semantic,
    compare_strict,
)


class TestEquivalenceLevel:
    """Test EquivalenceLevel enum."""

    def test_has_four_levels(self):
        """All four equivalence levels exist."""
        assert hasattr(EquivalenceLevel, "STRICT")
        assert hasattr(EquivalenceLevel, "SEMANTIC")
        assert hasattr(EquivalenceLevel, "OUTCOME")
        assert hasattr(EquivalenceLevel, "RELAXED")

    def test_levels_are_distinct(self):
        """All levels have distinct values."""
        levels = [
            EquivalenceLevel.STRICT,
            EquivalenceLevel.SEMANTIC,
            EquivalenceLevel.OUTCOME,
            EquivalenceLevel.RELAXED,
        ]
        assert len(set(levels)) == 4


class TestCompareStrict:
    """Test STRICT equivalence comparison."""

    def test_identical_outputs_are_equivalent(self):
        """Identical outputs pass strict comparison."""
        result = compare_strict(
            {"result": 8, "status": "ok"},
            {"result": 8, "status": "ok"},
        )
        assert result.equivalent is True
        assert result.level == EquivalenceLevel.STRICT
        assert result.confidence == 1.0
        assert len(result.differences) == 0

    def test_different_values_fail(self):
        """Different values fail strict comparison."""
        result = compare_strict(
            {"result": 8},
            {"result": 10},
        )
        assert result.equivalent is False
        assert "result" in str(result.differences)

    def test_extra_outputs_fail(self):
        """Extra outputs fail strict comparison."""
        result = compare_strict(
            {"result": 8},
            {"result": 8, "log": "computed"},
        )
        assert result.equivalent is False
        assert "Extra" in str(result.differences)

    def test_missing_outputs_fail(self):
        """Missing outputs fail strict comparison."""
        result = compare_strict(
            {"result": 8, "status": "ok"},
            {"result": 8},
        )
        assert result.equivalent is False
        assert "Missing" in str(result.differences)

    def test_empty_outputs_are_equivalent(self):
        """Empty outputs on both sides are equivalent."""
        result = compare_strict({}, {})
        assert result.equivalent is True
        assert result.confidence == 1.0


class TestCompareSemantic:
    """Test SEMANTIC equivalence comparison."""

    def test_identical_outputs_are_equivalent(self):
        """Identical outputs pass semantic comparison."""
        result = compare_semantic(
            {"result": 8},
            {"result": 8},
        )
        assert result.equivalent is True
        assert result.level == EquivalenceLevel.SEMANTIC

    def test_extra_outputs_allowed(self):
        """Extra outputs are allowed in semantic comparison."""
        result = compare_semantic(
            {"result": 8},
            {"result": 8, "log": "computed"},
        )
        assert result.equivalent is True
        assert "log" in result.details.get("extra_outputs", [])

    def test_missing_outputs_fail(self):
        """Missing required outputs fail semantic comparison."""
        result = compare_semantic(
            {"result": 8, "status": "ok"},
            {"result": 8},
        )
        assert result.equivalent is False
        assert "Missing" in str(result.differences)

    def test_list_order_independent(self):
        """Lists are compared order-independently."""
        result = compare_semantic(
            {"items": [1, 2, 3]},
            {"items": [3, 2, 1]},
        )
        assert result.equivalent is True

    def test_different_values_fail(self):
        """Different values still fail semantic comparison."""
        result = compare_semantic(
            {"result": 8},
            {"result": 10},
        )
        assert result.equivalent is False


class TestCompareOutcome:
    """Test OUTCOME equivalence comparison (default level)."""

    def test_identical_outputs_are_equivalent(self):
        """Identical outputs pass outcome comparison."""
        result = compare_outcome(
            {"result": 8},
            {"result": 8},
        )
        assert result.equivalent is True
        assert result.level == EquivalenceLevel.OUTCOME

    def test_extra_outputs_allowed(self):
        """Extra outputs are allowed in outcome comparison."""
        result = compare_outcome(
            {"result": 8},
            {"result": 8, "log": "computed"},
        )
        assert result.equivalent is True
        assert "log" in result.details.get("new_outputs", [])

    def test_int_float_coercion(self):
        """Int/float values are compared with type coercion."""
        result = compare_outcome(
            {"result": 8},
            {"result": 8.0},
        )
        assert result.equivalent is True

    def test_float_tolerance_near_values(self):
        """Floating-point values within tolerance are equivalent."""
        # Values that differ only by floating-point rounding
        result = compare_outcome(
            {"result": 0.1 + 0.2},
            {"result": 0.3},
        )
        assert result.equivalent is True

    def test_float_tolerance_large_values(self):
        """Tolerance is relative, so large values with tiny relative diff match."""
        result = compare_outcome(
            {"result": 1e15},
            {"result": 1e15 + 1.0},
        )
        assert result.equivalent is True

    def test_float_genuinely_different_values_fail(self):
        """Genuinely different numeric values still fail."""
        result = compare_outcome(
            {"result": 1.0},
            {"result": 1.01},
        )
        assert result.equivalent is False

    def test_string_whitespace_normalization(self):
        """String values are compared with whitespace normalization."""
        result = compare_outcome(
            {"message": "hello"},
            {"message": "hello  "},
        )
        assert result.equivalent is True

    def test_missing_outputs_fail(self):
        """Missing required outputs fail outcome comparison."""
        result = compare_outcome(
            {"result": 8, "status": "ok"},
            {"result": 8},
        )
        assert result.equivalent is False

    def test_different_values_fail(self):
        """Different values fail outcome comparison."""
        result = compare_outcome(
            {"result": 8},
            {"result": 10},
        )
        assert result.equivalent is False


class TestCompareRelaxed:
    """Test RELAXED equivalence comparison."""

    def test_only_checks_important_fields(self):
        """Only important fields are checked."""
        result = compare_relaxed(
            {"result": 8, "debug_info": "xyz"},
            {"result": 8, "debug_info": "abc"},  # Different debug_info
            important_fields={"result"},
        )
        assert result.equivalent is True
        assert "result" in result.details.get("important_fields", [])

    def test_missing_important_field_fails(self):
        """Missing important field fails relaxed comparison."""
        result = compare_relaxed(
            {"result": 8, "status": "ok"},
            {"status": "ok"},
            important_fields={"result"},
        )
        assert result.equivalent is False
        assert "Missing important" in str(result.differences)

    def test_changed_important_field_fails(self):
        """Changed important field fails relaxed comparison."""
        result = compare_relaxed(
            {"result": 8},
            {"result": 10},
            important_fields={"result"},
        )
        assert result.equivalent is False

    def test_defaults_to_all_original_keys(self):
        """Without important_fields, defaults to all original keys."""
        result = compare_relaxed(
            {"result": 8},
            {"result": 10},  # Changed
            important_fields=None,
        )
        assert result.equivalent is False

    def test_extra_outputs_allowed(self):
        """Extra outputs are always allowed in relaxed comparison."""
        result = compare_relaxed(
            {"result": 8},
            {"result": 8, "log": "new", "debug": "info"},
            important_fields={"result"},
        )
        assert result.equivalent is True


class TestCompareAtLevel:
    """Test the compare_at_level routing function."""

    def test_routes_to_strict(self):
        """Routes STRICT level to compare_strict."""
        result = compare_at_level(
            {"result": 8},
            {"result": 8, "extra": "field"},
            level=EquivalenceLevel.STRICT,
        )
        assert result.level == EquivalenceLevel.STRICT
        assert result.equivalent is False  # Extra field fails strict

    def test_routes_to_semantic(self):
        """Routes SEMANTIC level to compare_semantic."""
        result = compare_at_level(
            {"items": [1, 2, 3]},
            {"items": [3, 2, 1]},
            level=EquivalenceLevel.SEMANTIC,
        )
        assert result.level == EquivalenceLevel.SEMANTIC
        assert result.equivalent is True  # Order-independent

    def test_routes_to_outcome(self):
        """Routes OUTCOME level to compare_outcome."""
        result = compare_at_level(
            {"result": 8},
            {"result": 8.0, "log": "added"},
            level=EquivalenceLevel.OUTCOME,
        )
        assert result.level == EquivalenceLevel.OUTCOME
        assert result.equivalent is True  # Type coercion + extra allowed

    def test_routes_to_relaxed(self):
        """Routes RELAXED level to compare_relaxed."""
        result = compare_at_level(
            {"result": 8, "debug": "old"},
            {"result": 8, "debug": "new"},
            level=EquivalenceLevel.RELAXED,
            important_fields={"result"},
        )
        assert result.level == EquivalenceLevel.RELAXED
        assert result.equivalent is True  # Only result matters

    def test_defaults_to_outcome(self):
        """Default level is OUTCOME."""
        result = compare_at_level(
            {"result": 8},
            {"result": 8.0},
        )
        assert result.level == EquivalenceLevel.OUTCOME
        assert result.equivalent is True


class TestEquivalenceResult:
    """Test EquivalenceResult dataclass."""

    def test_str_representation(self):
        """String representation is human-readable."""
        result = EquivalenceResult(
            level=EquivalenceLevel.OUTCOME,
            equivalent=True,
            confidence=0.95,
            differences=[],
        )
        text = str(result)
        assert "OUTCOME" in text
        assert "EQUIVALENT" in text
        assert "95%" in text

    def test_str_shows_differences(self):
        """String representation shows differences."""
        result = EquivalenceResult(
            level=EquivalenceLevel.STRICT,
            equivalent=False,
            confidence=0.5,
            differences=["Value mismatch", "Missing field"],
        )
        text = str(result)
        assert "DIFFERENT" in text
        assert "Value mismatch" in text
        assert "Missing field" in text

    def test_str_truncates_many_differences(self):
        """String representation truncates long difference lists."""
        result = EquivalenceResult(
            level=EquivalenceLevel.STRICT,
            equivalent=False,
            confidence=0.1,
            differences=[f"Diff {i}" for i in range(10)],
        )
        text = str(result)
        assert "... and 5 more" in text


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_vs_nonempty(self):
        """Empty original vs non-empty transformed."""
        result = compare_outcome({}, {"result": 8})
        assert result.equivalent is True  # No required outputs

    def test_nonempty_vs_empty(self):
        """Non-empty original vs empty transformed."""
        result = compare_outcome({"result": 8}, {})
        assert result.equivalent is False  # Required output missing

    def test_nested_dict_comparison(self):
        """Nested dictionaries are compared."""
        result = compare_strict(
            {"config": {"a": 1, "b": 2}},
            {"config": {"a": 1, "b": 2}},
        )
        assert result.equivalent is True

    def test_nested_dict_difference(self):
        """Nested dictionary differences are detected."""
        result = compare_strict(
            {"config": {"a": 1}},
            {"config": {"a": 2}},
        )
        assert result.equivalent is False

    def test_none_values(self):
        """None values are handled correctly."""
        result = compare_outcome(
            {"result": None},
            {"result": None},
        )
        assert result.equivalent is True

    def test_boolean_values(self):
        """Boolean values are compared correctly."""
        result = compare_strict(
            {"success": True},
            {"success": False},
        )
        assert result.equivalent is False

    def test_mixed_type_collections(self):
        """Collections with mixed types are handled."""
        result = compare_semantic(
            {"items": [1, "two", 3.0]},
            {"items": [1, "two", 3.0]},
        )
        assert result.equivalent is True
