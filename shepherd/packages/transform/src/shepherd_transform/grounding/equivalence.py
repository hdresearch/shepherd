"""Equivalence levels and comparers for behavioral grounding.

This module provides different levels of equivalence checking for comparing
task executions:

- STRICT: Identical effect streams (same effects, same order)
- SEMANTIC: Same operations, order-independent
- OUTCOME: Same final outputs (recommended default)
- RELAXED: Same "important" outputs only

Example:
    >>> from shepherd_transform.grounding import EquivalenceLevel, compare_at_level
    >>> result = compare_at_level(
    ...     original_outputs={"result": 8},
    ...     transformed_outputs={"result": 8, "log": "computed"},
    ...     level=EquivalenceLevel.OUTCOME,
    ... )
    >>> assert result.equivalent
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class EquivalenceLevel(Enum):
    """Level of behavioral equivalence for comparing task executions.

    Levels from strictest to most relaxed:

    STRICT: Identical outputs and effect patterns. Useful for verifying
            no-op transformations or testing the framework itself.

    SEMANTIC: Same outputs with same types, internal structure may differ.
              Order-independent for collections. Useful for refactoring.

    OUTCOME: Same final output values (recommended default). Internal
             effects may differ. Best for optimization transformations.

    RELAXED: Same "important" outputs only, auxiliary outputs may differ.
             Useful for feature-addition transformations.
    """

    STRICT = auto()
    SEMANTIC = auto()
    OUTCOME = auto()
    RELAXED = auto()


@dataclass
class EquivalenceResult:
    """Result of comparing outputs at a specific equivalence level.

    Attributes:
        level: The equivalence level used for comparison
        equivalent: Whether outputs are equivalent at this level
        confidence: Confidence score (0.0 to 1.0)
        differences: List of human-readable difference descriptions
        details: Additional comparison details (field-specific info)
    """

    level: EquivalenceLevel
    equivalent: bool
    confidence: float = 1.0
    differences: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        status = "EQUIVALENT" if self.equivalent else "DIFFERENT"
        lines = [f"{self.level.name}: {status} (confidence: {self.confidence:.0%})"]
        if self.differences:
            lines.append("  Differences:")
            for diff in self.differences[:5]:
                lines.append(f"    - {diff}")
            if len(self.differences) > 5:
                lines.append(f"    ... and {len(self.differences) - 5} more")
        return "\n".join(lines)


def _compare_values_strict(a: Any, b: Any) -> bool:
    """Strict comparison: values must be exactly equal."""
    return a == b  # type: ignore[no-any-return]


def _compare_values_semantic(a: Any, b: Any) -> bool:
    """Semantic comparison: same values, order-independent for collections."""
    if type(a) is not type(b):
        return False

    if isinstance(a, dict) and isinstance(b, dict):
        return a == b  # Dict comparison is order-independent in Python 3.7+

    if isinstance(a, (list, tuple)):
        # Sort if comparable, otherwise compare as-is
        try:
            return sorted(a) == sorted(b)
        except TypeError:
            return a == b  # type: ignore[no-any-return]

    if isinstance(a, set):
        return a == b  # type: ignore[no-any-return]

    return a == b  # type: ignore[no-any-return]


def _compare_values_outcome(a: Any, b: Any) -> bool:
    """Outcome comparison: same final value, type can differ slightly.

    Allows numeric type coercion (int/float) and string normalization.
    """
    if a == b:
        return True

    # Allow int/float coercion with tolerance for floating-point imprecision
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=0.0)

    # String normalization (strip whitespace)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()

    return False


def compare_strict(
    original_outputs: dict[str, Any],
    transformed_outputs: dict[str, Any],
) -> EquivalenceResult:
    """STRICT equivalence: outputs must be exactly identical.

    Both keys and values must match exactly. Any difference is a failure.

    Args:
        original_outputs: Output dict from original task
        transformed_outputs: Output dict from transformed task

    Returns:
        EquivalenceResult with comparison details
    """
    differences = []

    # Check for key differences
    orig_keys = set(original_outputs.keys())
    trans_keys = set(transformed_outputs.keys())

    missing = orig_keys - trans_keys
    extra = trans_keys - orig_keys

    if missing:
        differences.append(f"Missing outputs: {sorted(missing)}")
    if extra:
        differences.append(f"Extra outputs: {sorted(extra)}")

    # Check value differences
    for key in orig_keys & trans_keys:
        if not _compare_values_strict(original_outputs[key], transformed_outputs[key]):
            differences.append(f"'{key}': {original_outputs[key]!r} != {transformed_outputs[key]!r}")

    equivalent = len(differences) == 0
    total = len(orig_keys | trans_keys)
    matching = total - len(differences) if total > 0 else 0
    confidence = matching / total if total > 0 else 1.0

    return EquivalenceResult(
        level=EquivalenceLevel.STRICT,
        equivalent=equivalent,
        confidence=confidence,
        differences=differences,
        details={
            "original_keys": sorted(orig_keys),
            "transformed_keys": sorted(trans_keys),
        },
    )


def compare_semantic(
    original_outputs: dict[str, Any],
    transformed_outputs: dict[str, Any],
) -> EquivalenceResult:
    """SEMANTIC equivalence: same outputs, order-independent.

    Values must match semantically (collections compared order-independently).
    Extra outputs in transformed are allowed.

    Args:
        original_outputs: Output dict from original task
        transformed_outputs: Output dict from transformed task

    Returns:
        EquivalenceResult with comparison details
    """
    differences = []

    orig_keys = set(original_outputs.keys())
    trans_keys = set(transformed_outputs.keys())

    # Missing keys are failures
    missing = orig_keys - trans_keys
    if missing:
        differences.append(f"Missing outputs: {sorted(missing)}")

    # Extra keys are noted but not failures (for semantic equivalence)
    extra = trans_keys - orig_keys

    # Check value differences semantically
    for key in orig_keys & trans_keys:
        if not _compare_values_semantic(original_outputs[key], transformed_outputs[key]):
            differences.append(f"'{key}': {original_outputs[key]!r} != {transformed_outputs[key]!r}")

    equivalent = len(differences) == 0
    checked = len(orig_keys)
    matching = checked - len([d for d in differences if "!=" in d])
    confidence = matching / checked if checked > 0 else 1.0

    return EquivalenceResult(
        level=EquivalenceLevel.SEMANTIC,
        equivalent=equivalent,
        confidence=confidence,
        differences=differences,
        details={
            "original_keys": sorted(orig_keys),
            "transformed_keys": sorted(trans_keys),
            "extra_outputs": sorted(extra),
        },
    )


def compare_outcome(
    original_outputs: dict[str, Any],
    transformed_outputs: dict[str, Any],
) -> EquivalenceResult:
    """OUTCOME equivalence: same final output values.

    Original outputs must be present with matching values (with type coercion).
    Extra outputs are allowed and don't affect equivalence.

    Args:
        original_outputs: Output dict from original task
        transformed_outputs: Output dict from transformed task

    Returns:
        EquivalenceResult with comparison details
    """
    differences = []

    orig_keys = set(original_outputs.keys())
    trans_keys = set(transformed_outputs.keys())

    # Missing keys are failures
    missing = orig_keys - trans_keys
    if missing:
        differences.append(f"Missing outputs: {sorted(missing)}")

    # Extra keys are noted but allowed
    extra = trans_keys - orig_keys

    # Check value differences with outcome-level comparison
    for key in orig_keys & trans_keys:
        if not _compare_values_outcome(original_outputs[key], transformed_outputs[key]):
            differences.append(f"'{key}': {original_outputs[key]!r} -> {transformed_outputs[key]!r}")

    equivalent = len(differences) == 0
    checked = len(orig_keys)
    matching = checked - len([d for d in differences if ":" in d and "->" in d])
    confidence = matching / checked if checked > 0 else 1.0

    return EquivalenceResult(
        level=EquivalenceLevel.OUTCOME,
        equivalent=equivalent,
        confidence=confidence,
        differences=differences,
        details={
            "original_outputs": original_outputs,
            "transformed_outputs": transformed_outputs,
            "new_outputs": sorted(extra),
        },
    )


def compare_relaxed(
    original_outputs: dict[str, Any],
    transformed_outputs: dict[str, Any],
    important_fields: set[str] | None = None,
) -> EquivalenceResult:
    """RELAXED equivalence: same "important" outputs only.

    Only checks specified important fields. Other outputs can change freely.
    If important_fields is None, uses all original output keys.

    Args:
        original_outputs: Output dict from original task
        transformed_outputs: Output dict from transformed task
        important_fields: Set of field names that must match (default: all)

    Returns:
        EquivalenceResult with comparison details
    """
    differences = []

    orig_keys = set(original_outputs.keys())

    # Default to all original keys if not specified
    if important_fields is None:
        important_fields = orig_keys.copy()

    # Only check important fields that exist in original
    fields_to_check = important_fields & orig_keys

    for key in sorted(fields_to_check):
        if key not in transformed_outputs:
            differences.append(f"Missing important output: '{key}'")
        elif not _compare_values_outcome(original_outputs[key], transformed_outputs[key]):
            differences.append(
                f"Important output '{key}' changed: {original_outputs[key]!r} -> {transformed_outputs[key]!r}"
            )

    equivalent = len(differences) == 0
    checked = len(fields_to_check)
    matching = checked - len(differences)
    confidence = matching / checked if checked > 0 else 1.0

    return EquivalenceResult(
        level=EquivalenceLevel.RELAXED,
        equivalent=equivalent,
        confidence=confidence,
        differences=differences,
        details={
            "important_fields": sorted(important_fields),
            "checked_count": checked,
            "matching_count": matching,
        },
    )


def compare_at_level(
    original_outputs: dict[str, Any],
    transformed_outputs: dict[str, Any],
    level: EquivalenceLevel = EquivalenceLevel.OUTCOME,
    important_fields: set[str] | None = None,
) -> EquivalenceResult:
    """Compare outputs at a specific equivalence level.

    This is the main entry point for output comparison. Routes to the
    appropriate comparison function based on level.

    Args:
        original_outputs: Output dict from original task
        transformed_outputs: Output dict from transformed task
        level: The equivalence level to use (default: OUTCOME)
        important_fields: For RELAXED level, which fields are important

    Returns:
        EquivalenceResult with comparison details

    Example:
        >>> result = compare_at_level(
        ...     {"result": 8},
        ...     {"result": 8, "log": "computed"},
        ...     level=EquivalenceLevel.OUTCOME,
        ... )
        >>> assert result.equivalent
    """
    if level == EquivalenceLevel.STRICT:
        return compare_strict(original_outputs, transformed_outputs)
    if level == EquivalenceLevel.SEMANTIC:
        return compare_semantic(original_outputs, transformed_outputs)
    if level == EquivalenceLevel.OUTCOME:
        return compare_outcome(original_outputs, transformed_outputs)
    if level == EquivalenceLevel.RELAXED:
        return compare_relaxed(original_outputs, transformed_outputs, important_fields)
    # Should never happen with proper enum
    return compare_outcome(original_outputs, transformed_outputs)


__all__ = [
    # Enum
    "EquivalenceLevel",
    # Result
    "EquivalenceResult",
    "compare_at_level",
    "compare_outcome",
    "compare_relaxed",
    "compare_semantic",
    # Comparison functions
    "compare_strict",
]
