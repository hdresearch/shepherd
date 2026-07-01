"""Test input generation for behavioral grounding.

This module provides automatic test input generation for verifying task
transformations. It generates diverse inputs based on type annotations,
boundary cases, and random sampling.

Key Components:
- TaskInputSpec: Specification of a task's input fields
- TestInputGenerator: Generator for test inputs
- CoverageReport: Analysis of test coverage quality
- analyze_coverage(): Estimate coverage confidence

Example:
    >>> from shepherd_transform.grounding import TestInputGenerator, TaskInputSpec
    >>>
    >>> spec = TaskInputSpec.from_task_class(MyTask)
    >>> generator = TestInputGenerator(spec, seed=42)
    >>> test_cases = generator.generate_all()
    >>>
    >>> # Use with behavioral_grounding
    >>> result = behavioral_grounding(
    ...     original_class=MyTask,
    ...     transformed_class=TransformedTask,
    ...     test_cases=test_cases,
    ... )

See Also:
    - shepherd_transform.grounding.grounding: Behavioral grounding functions
    - spikes/spike_test_input_generation.py: Validation spike (5/5 tests)
"""

from __future__ import annotations

import random as random_module
import string
from dataclasses import dataclass, field
from typing import Any, Literal, Union, get_args, get_origin

# Module-level random instance (used by public functions without explicit rng)
_default_rng = random_module.Random()  # noqa: S311 — not used for security

__all__ = [
    # Coverage
    "CoverageReport",
    # Specification
    "TaskInputSpec",
    # Generator
    "TestInputGenerator",
    "analyze_coverage",
    # Helpers (for advanced use)
    "generate_for_type",
    "get_boundary_values",
]


# =============================================================================
# TaskInputSpec
# =============================================================================


@dataclass
class TaskInputSpec:
    """Specification of a task's input fields for test generation.

    Attributes:
        task_name: Name of the task class
        fields: Mapping of field name to type annotation
        defaults: Mapping of field name to default value (if any)
        constraints: Mapping of field name to constraint dict (ge, le, etc.)
        descriptions: Mapping of field name to field description
    """

    task_name: str
    fields: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, dict[str, Any]] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_task_class(cls, task_class: type) -> TaskInputSpec:
        """Extract input specification from a @task class.

        Uses extract_task_metadata() to get field information, then
        extracts the relevant input field data.

        Args:
            task_class: A @task decorated class

        Returns:
            TaskInputSpec with input field information

        Example:
            >>> @task
            ... class Calculator(BaseModel):
            ...     x: Input(int)
            ...     y: Input(int) = 0
            >>> spec = TaskInputSpec.from_task_class(Calculator)
            >>> spec.fields
            {'x': int, 'y': int}
        """
        from shepherd_runtime.task.metadata import extract_task_metadata

        meta = extract_task_metadata(task_class)

        fields: dict[str, Any] = {}
        defaults: dict[str, Any] = {}
        constraints: dict[str, dict[str, Any]] = {}
        descriptions: dict[str, str] = {}

        for name, field_info in meta.inputs.items():
            fields[name] = field_info.inner_type
            if not field_info.required and field_info.default is not None:
                defaults[name] = field_info.default
            if field_info.constraints:
                constraints[name] = field_info.constraints
            if field_info.description:
                descriptions[name] = field_info.description

        return cls(
            task_name=meta.name,
            fields=fields,
            defaults=defaults,
            constraints=constraints,
            descriptions=descriptions,
        )

    @classmethod
    def from_task(cls, task: object) -> TaskInputSpec:
        """Extract input specification from class-form or function-form task objects."""
        from shepherd_runtime.nucleus import CallableTask

        if isinstance(task, type):
            return cls.from_task_class(task)

        if isinstance(task, CallableTask):
            fields: dict[str, Any] = {}
            defaults: dict[str, Any] = {}
            for parameter in task.metadata.parameters:
                fields[parameter.name] = parameter.base_annotation
                if parameter.has_default:
                    defaults[parameter.name] = parameter.default
            return cls(
                task_name=task.metadata.name or task.metadata.qualname,
                fields=fields,
                defaults=defaults,
            )

        raise TypeError("TaskInputSpec.from_task() expects a class-form task or function-form CallableTask")

    @classmethod
    def from_dict(cls, fields: dict[str, Any], task_name: str = "Task") -> TaskInputSpec:
        """Create TaskInputSpec from a simple field dict.

        Useful for testing or when task metadata isn't available.

        Args:
            fields: Mapping of field name to type
            task_name: Name for the spec

        Returns:
            TaskInputSpec with the given fields
        """
        return cls(task_name=task_name, fields=fields)


# =============================================================================
# Type Value Generation
# =============================================================================


def generate_for_type(
    type_hint: Any,
    strategy: Literal["signature", "boundary", "random"] = "signature",
    constraints: dict[str, Any] | None = None,
    rng: random_module.Random | None = None,
) -> Any:
    """Generate a value for a given type annotation.

    Args:
        type_hint: The type annotation (int, str, list[int], etc.)
        strategy: Generation strategy
            - "signature": Typical values for the type
            - "boundary": Edge case values
            - "random": Random values within constraints
        constraints: Optional constraints (ge, le, min_length, max_length)
        rng: Optional Random instance for reproducibility

    Returns:
        A generated value of the appropriate type

    Supported Types:
        - Basic: int, float, str, bool
        - Generic: list[T], dict[K, V], set[T], tuple[T, ...]
        - Optional: T | None, Optional[T]
        - Literal: Literal["a", "b", "c"]
        - Union: Union[int, str]
    """
    if rng is None:
        rng = _default_rng
    constraints = constraints or {}
    origin = get_origin(type_hint)
    args = get_args(type_hint)

    # Handle Literal types
    if origin is Literal:
        if strategy == "boundary":
            return args[0] if args else None
        return rng.choice(args) if args else None

    # Handle Union types (including Optional which is Union[T, None])
    if origin is Union:
        # Filter out None for value generation
        non_none_args = [a for a in args if a is not type(None)]
        if not non_none_args:
            return None
        if strategy == "boundary" and type(None) in args and rng.random() < 0.3:
            # For Optional types, None is a valid boundary case
            return None
        chosen_type = rng.choice(non_none_args)
        return generate_for_type(chosen_type, strategy, constraints, rng)

    # Handle list types
    if origin is list:
        item_type = args[0] if args else str
        if strategy == "boundary":
            return []  # Empty list edge case
        count = rng.randint(1, 5)
        return [generate_for_type(item_type, strategy, rng=rng) for _ in range(count)]

    # Handle dict types
    if origin is dict:
        key_type = args[0] if args else str
        val_type = args[1] if len(args) > 1 else Any
        if strategy == "boundary":
            return {}  # Empty dict edge case
        count = rng.randint(1, 3)
        result = {}
        for _ in range(count):
            key = generate_for_type(key_type, strategy, rng=rng)
            # Ensure key is hashable
            if isinstance(key, (list, dict, set)):
                key = str(key)
            result[key] = generate_for_type(val_type, strategy, rng=rng)
        return result

    # Handle set types
    if origin is set:
        item_type = args[0] if args else str
        if strategy == "boundary":
            return set()
        count = rng.randint(1, 5)
        items = []
        for _ in range(count):
            item = generate_for_type(item_type, strategy, rng=rng)
            # Ensure item is hashable
            if isinstance(item, (list, dict, set)):
                item = str(item)
            items.append(item)
        return set(items)

    # Handle tuple types
    if origin is tuple:
        if args:
            return tuple(generate_for_type(t, strategy, rng=rng) for t in args)
        return ()

    # Handle basic types
    if type_hint is int:
        return _generate_int(strategy, constraints, rng)

    if type_hint is float:
        return _generate_float(strategy, constraints, rng)

    if type_hint is str:
        return _generate_str(strategy, constraints, rng)

    if type_hint is bool:
        return rng.choice([True, False])

    # Handle Any type
    if type_hint is Any:
        # Pick a random basic type
        basic_type = rng.choice([int, str, bool])
        return generate_for_type(basic_type, strategy, rng=rng)

    # Fallback for unknown types
    return None


def _generate_int(
    strategy: Literal["signature", "boundary", "random"],
    constraints: dict[str, Any],
    rng: random_module.Random,
) -> int:
    """Generate an integer value."""
    min_val = constraints.get("ge", constraints.get("gt", -100))
    if "gt" in constraints:
        min_val = constraints["gt"] + 1
    max_val = constraints.get("le", constraints.get("lt", 100))
    if "lt" in constraints:
        max_val = constraints["lt"] - 1

    if strategy == "boundary":
        # Return boundary values respecting constraints
        boundaries = [0, 1, -1, min_val, max_val]
        valid = [b for b in boundaries if min_val <= b <= max_val]
        return rng.choice(valid) if valid else min_val  # type: ignore[no-any-return]

    return rng.randint(min_val, max_val)


def _generate_float(
    strategy: Literal["signature", "boundary", "random"],
    constraints: dict[str, Any],
    rng: random_module.Random,
) -> float:
    """Generate a float value."""
    min_val = constraints.get("ge", constraints.get("gt", -100.0))
    if "gt" in constraints:
        min_val = constraints["gt"] + 0.001
    max_val = constraints.get("le", constraints.get("lt", 100.0))
    if "lt" in constraints:
        max_val = constraints["lt"] - 0.001

    if strategy == "boundary":
        boundaries = [0.0, 1.0, -1.0, min_val, max_val]
        valid = [b for b in boundaries if min_val <= b <= max_val]
        return rng.choice(valid) if valid else min_val  # type: ignore[no-any-return]

    return rng.uniform(min_val, max_val)


def _generate_str(
    strategy: Literal["signature", "boundary", "random"],
    constraints: dict[str, Any],
    rng: random_module.Random,
) -> str:
    """Generate a string value."""
    min_len = constraints.get("min_length", 0)
    max_len = constraints.get("max_length", 100)

    if strategy == "boundary":
        # Boundary cases for strings
        if min_len == 0:
            return rng.choice(["", " ", "a"])
        return "a" * min_len  # type: ignore[no-any-return]

    # Generate random string
    length = rng.randint(max(min_len, 3), min(max_len, 20))
    return "".join(rng.choices(string.ascii_letters + string.digits, k=length))


def get_boundary_values(
    type_hint: Any,
    constraints: dict[str, Any] | None = None,
    rng: random_module.Random | None = None,
) -> list[Any]:
    """Get boundary/edge case values for a type.

    Args:
        type_hint: The type annotation
        constraints: Optional constraints to respect
        rng: Optional Random instance for reproducibility

    Returns:
        List of boundary values for the type

    Example:
        >>> get_boundary_values(int)
        [0, 1, -1, 100, -100, 2147483647, -2147483648]
        >>> get_boundary_values(str)
        ['', ' ', 'a', 'test', 'A...100 chars...A']
    """
    if rng is None:
        rng = _default_rng
    constraints = constraints or {}
    origin = get_origin(type_hint)
    args = get_args(type_hint)

    # Handle Literal types - return all options
    if origin is Literal:
        return list(args)

    # Handle Union types
    if origin is Union:
        result = []
        for arg in args:
            if arg is type(None):
                result.append(None)
            else:
                result.extend(get_boundary_values(arg, constraints, rng))
        return result

    # Handle list types
    if origin is list:
        item_type = args[0] if args else str
        return [
            [],  # Empty
            [generate_for_type(item_type, "signature", rng=rng)],  # Single
            [generate_for_type(item_type, "signature", rng=rng) for _ in range(10)],  # Many
        ]

    # Handle dict types
    if origin is dict:
        key_type = args[0] if args else str
        val_type = args[1] if len(args) > 1 else str
        single_key = generate_for_type(key_type, "signature", rng=rng)
        if isinstance(single_key, (list, dict, set)):
            single_key = "key"
        return [
            {},  # Empty
            {single_key: generate_for_type(val_type, "signature", rng=rng)},  # Single
        ]

    # Basic types
    if type_hint is int:
        min_val = constraints.get("ge", -2147483648)
        max_val = constraints.get("le", 2147483647)
        boundaries = [0, 1, -1, 100, -100, max_val, min_val]
        return [b for b in boundaries if min_val <= b <= max_val]

    if type_hint is float:
        min_val = constraints.get("ge", float("-inf"))
        max_val = constraints.get("le", float("inf"))
        boundaries = [0.0, 1.0, -1.0, 0.001, 1000.0]
        return [b for b in boundaries if min_val <= b <= max_val]

    if type_hint is str:
        min_len = constraints.get("min_length", 0)
        max_len = constraints.get("max_length", 1000)
        result = []
        if min_len == 0:
            result.extend(["", " "])
        result.extend(["a", "test", "Hello World!"])
        if max_len >= 100:
            result.append("A" * 100)
        return result

    if type_hint is bool:
        return [True, False]

    return [None]


# =============================================================================
# TestInputGenerator
# =============================================================================


class TestInputGenerator:
    """Generate test inputs for behavioral grounding.

    This class generates diverse test inputs based on type annotations,
    boundary cases, and random sampling. The generated inputs can be
    used directly with behavioral_grounding().

    Attributes:
        spec: The TaskInputSpec describing input fields
        rng: Random instance for reproducible generation

    Example:
        >>> spec = TaskInputSpec.from_task_class(Calculator)
        >>> generator = TestInputGenerator(spec, seed=42)
        >>> inputs = generator.generate_all()
        >>> len(inputs)
        18  # Approximately 15-20 inputs
    """

    def __init__(self, spec: TaskInputSpec, seed: int | None = None):
        """Initialize the generator.

        Args:
            spec: Input specification for the task
            seed: Optional random seed for reproducibility
        """
        self.spec = spec
        self.rng = random_module.Random(seed)  # noqa: S311 — not used for security

    def generate_from_type(self, count: int = 3) -> list[dict[str, Any]]:
        """Generate inputs based on type annotations.

        Creates typical values for each input field based on its type.

        Args:
            count: Number of inputs to generate

        Returns:
            List of input dictionaries
        """
        results = []
        for _ in range(count):
            values = {}
            for name, type_hint in self.spec.fields.items():
                constraints = self.spec.constraints.get(name, {})
                values[name] = generate_for_type(type_hint, "signature", constraints, self.rng)
            results.append(values)
        return results

    def generate_boundary_cases(self) -> list[dict[str, Any]]:
        """Generate edge case inputs.

        For each input field, generates inputs with boundary values
        while using typical values for other fields.

        Returns:
            List of input dictionaries with boundary values
        """
        results = []

        for target_name, target_type in self.spec.fields.items():
            constraints = self.spec.constraints.get(target_name, {})
            boundaries = get_boundary_values(target_type, constraints, self.rng)

            for boundary_val in boundaries:
                values = {}
                for name, type_hint in self.spec.fields.items():
                    if name == target_name:
                        values[name] = boundary_val
                    elif name in self.spec.defaults:
                        values[name] = self.spec.defaults[name]
                    else:
                        field_constraints = self.spec.constraints.get(name, {})
                        values[name] = generate_for_type(type_hint, "signature", field_constraints, self.rng)
                results.append(values)

        return results

    def generate_random(self, count: int = 5) -> list[dict[str, Any]]:
        """Generate random inputs within type constraints.

        Args:
            count: Number of random inputs to generate

        Returns:
            List of random input dictionaries
        """
        results = []
        for _ in range(count):
            values = {}
            for name, type_hint in self.spec.fields.items():
                constraints = self.spec.constraints.get(name, {})
                values[name] = generate_for_type(type_hint, "random", constraints, self.rng)
            results.append(values)
        return results

    def generate_all(
        self,
        signature_count: int = 3,
        random_count: int = 5,
    ) -> list[dict[str, Any]]:
        """Generate comprehensive test set (~15-20 inputs).

        Combines:
        - Type-based generation (signature_count inputs)
        - Boundary cases (automatic, varies by field types)
        - Random cases (random_count inputs)

        Deduplicates to avoid redundant test cases.

        Args:
            signature_count: Number of signature-based inputs
            random_count: Number of random inputs

        Returns:
            List of diverse input dictionaries (typically 15-20)
        """
        all_inputs = []
        seen: set[str] = set()

        def add_unique(inputs: list[dict[str, Any]]) -> None:
            for inp in inputs:
                # Create a hashable key for deduplication
                key = str(sorted((k, _make_hashable(v)) for k, v in inp.items()))
                if key not in seen:
                    seen.add(key)
                    all_inputs.append(inp)

        # Add in order of priority
        add_unique(self.generate_boundary_cases())  # Most important
        add_unique(self.generate_from_type(signature_count))
        add_unique(self.generate_random(random_count))

        return all_inputs


def _make_hashable(value: Any) -> Any:
    """Convert a value to a hashable form for deduplication."""
    if isinstance(value, dict):
        return tuple(sorted((k, _make_hashable(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_make_hashable(v) for v in value)
    if isinstance(value, set):
        return frozenset(_make_hashable(v) for v in value)
    return value


# =============================================================================
# Coverage Analysis
# =============================================================================


@dataclass
class CoverageReport:
    """Report on test input coverage quality.

    Attributes:
        total_inputs: Total number of test inputs
        unique_combinations: Number of unique value combinations
        boundary_coverage: Per-field boundary coverage (0.0-1.0)
        strategy_diversity: Diversity of generation strategies (0.0-1.0)
        confidence: Overall confidence score (0.0-1.0)
    """

    total_inputs: int
    unique_combinations: int
    boundary_coverage: dict[str, float] = field(default_factory=dict)
    strategy_diversity: float = 0.0
    confidence: float = 0.0

    def __str__(self) -> str:
        """Human-readable coverage report."""
        lines = [
            "Coverage Report:",
            f"  Total inputs: {self.total_inputs}",
            f"  Unique combinations: {self.unique_combinations}",
            f"  Strategy diversity: {self.strategy_diversity:.0%}",
        ]
        if self.boundary_coverage:
            lines.append("  Boundary coverage:")
            for field_name, coverage in sorted(self.boundary_coverage.items()):
                lines.append(f"    {field_name}: {coverage:.0%}")
        lines.append(f"  Confidence: {self.confidence:.0%}")
        return "\n".join(lines)


def analyze_coverage(
    test_cases: list[dict[str, Any]],
    spec: TaskInputSpec,
) -> CoverageReport:
    """Analyze coverage quality of test inputs.

    Estimates confidence based on:
    - Boundary coverage (40% weight): How many boundary values are covered
    - Strategy diversity (30% weight): Variety in value patterns
    - Input count (30% weight): Number of test cases (target: 20)

    Args:
        test_cases: List of input dictionaries
        spec: TaskInputSpec describing the input fields

    Returns:
        CoverageReport with coverage metrics

    Example:
        >>> inputs = generator.generate_all()
        >>> report = analyze_coverage(inputs, spec)
        >>> print(report)
        Coverage Report:
          Total inputs: 18
          Unique combinations: 18
          Confidence: 75%
    """
    if not test_cases:
        return CoverageReport(
            total_inputs=0,
            unique_combinations=0,
            confidence=0.0,
        )

    # Count unique combinations
    seen: set[str] = set()
    for inp in test_cases:
        key = str(sorted((k, _make_hashable(v)) for k, v in inp.items()))
        seen.add(key)
    unique_count = len(seen)

    # Calculate boundary coverage per field
    boundary_coverage: dict[str, float] = {}
    for name, type_hint in spec.fields.items():
        constraints = spec.constraints.get(name, {})
        boundaries = get_boundary_values(type_hint, constraints)
        boundary_strs = {str(_make_hashable(b)) for b in boundaries}

        if not boundary_strs:
            boundary_coverage[name] = 1.0
            continue

        covered = set()
        for inp in test_cases:
            if name in inp:
                val_str = str(_make_hashable(inp[name]))
                if val_str in boundary_strs:
                    covered.add(val_str)

        boundary_coverage[name] = len(covered) / len(boundary_strs)

    # Calculate strategy diversity (estimate based on value patterns)
    # Higher diversity = values are more varied
    value_diversity = _estimate_value_diversity(test_cases, spec)

    # Calculate overall confidence
    avg_boundary = sum(boundary_coverage.values()) / len(boundary_coverage) if boundary_coverage else 0
    input_count_factor = min(1.0, len(test_cases) / 20)  # Target 20 inputs

    confidence = (avg_boundary * 0.4) + (value_diversity * 0.3) + (input_count_factor * 0.3)

    return CoverageReport(
        total_inputs=len(test_cases),
        unique_combinations=unique_count,
        boundary_coverage=boundary_coverage,
        strategy_diversity=value_diversity,
        confidence=confidence,
    )


def _estimate_value_diversity(
    test_cases: list[dict[str, Any]],
    spec: TaskInputSpec,
) -> float:
    """Estimate diversity of values across test cases."""
    if not test_cases or not spec.fields:
        return 0.0

    diversities = []
    for name in spec.fields:
        values = [str(_make_hashable(tc.get(name))) for tc in test_cases if name in tc]
        if values:
            unique_ratio = len(set(values)) / len(values)
            diversities.append(unique_ratio)

    return sum(diversities) / len(diversities) if diversities else 0.0
