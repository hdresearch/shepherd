"""Behavioral grounding for task transformations.

This module provides tools for verifying that transformed tasks preserve
the behavior of original tasks through behavioral testing.

Key Components:
- EquivalenceLevel: Enum defining strictness levels (STRICT, SEMANTIC, OUTCOME, RELAXED)
- GroundingResult: Result of behavioral grounding verification
- behavioral_grounding(): Verify transformation preserves behavior
- ground_transformation(): High-level function combining reconstruction + grounding
- TestInputGenerator: Automatic test input generation
- TaskInputSpec: Input field specification for a task

Example:
    >>> from shepherd_transform.grounding import (
    ...     behavioral_grounding,
    ...     EquivalenceLevel,
    ...     ground_transformation,
    ...     TestInputGenerator,
    ...     TaskInputSpec,
    ... )
    >>>
    >>> # Generate test inputs automatically
    >>> spec = TaskInputSpec.from_task_class(Calculator)
    >>> generator = TestInputGenerator(spec, seed=42)
    >>> test_cases = generator.generate_all()
    >>>
    >>> # Verify a transformed task preserves behavior
    >>> result = behavioral_grounding(
    ...     original_class=Calculator,
    ...     transformed_class=CalculatorWithLogging,
    ...     test_cases=test_cases,
    ...     equivalence=EquivalenceLevel.OUTCOME,
    ... )
    >>> if result.passed:
    ...     print("Transformation verified!")
    >>>
    >>> # One-step reconstruction and grounding
    >>> result, new_class = ground_transformation(
    ...     original_class=Calculator,
    ...     transformed_source=llm_generated_code,
    ...     test_cases=test_cases,
    ... )

See Also:
    - shepherd_transform.source: Transform-facing task reconstruction facade
"""

from __future__ import annotations

from .equivalence import (
    EquivalenceLevel,
    EquivalenceResult,
    compare_at_level,
    compare_outcome,
    compare_relaxed,
    compare_semantic,
    compare_strict,
)
from .grounding import (
    GroundingResult,
    Mismatch,
    behavioral_grounding,
    ground_transformation,
)
from .inputs import (
    CoverageReport,
    TaskInputSpec,
    TestInputGenerator,
    analyze_coverage,
    generate_for_type,
    get_boundary_values,
)

__all__ = [
    "CoverageReport",
    # Equivalence levels
    "EquivalenceLevel",
    "EquivalenceResult",
    "GroundingResult",
    # Grounding
    "Mismatch",
    # Test input generation
    "TaskInputSpec",
    "TestInputGenerator",
    "analyze_coverage",
    "behavioral_grounding",
    "compare_at_level",
    "compare_outcome",
    "compare_relaxed",
    "compare_semantic",
    # Comparison functions
    "compare_strict",
    "generate_for_type",
    "get_boundary_values",
    "ground_transformation",
]
