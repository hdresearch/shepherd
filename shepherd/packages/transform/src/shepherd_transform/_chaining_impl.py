"""Task-transformation chaining with confidence tracking and rollback support.

This module provides infrastructure for applying multiple transformations
to a task in sequence, with:

- Confidence degradation tracking across the chain
- Automatic rollback on failure
- Provenance tracking for audit trails
- Configurable stopping criteria

Key Components:
- TransformSpec: Specification for a single transformation
- ChainResult: Result of a transformation chain
- TransformationEngine: Engine for applying chains with confidence tracking

Confidence Degradation:
    overall_confidence = Π(grounding_i * decay) for all transforms

    With 90% grounding per step and 95% decay factor (defaults):
    - Depth 1: 85.5%
    - Depth 3: 62.5%
    - Depth 5: 45.7%
    - Depth 7: 31.5% (requires human approval)

Example:
    >>> from shepherd_transform.chaining import TransformationEngine, TransformSpec
    >>>
    >>> engine = TransformationEngine(min_confidence=0.5, max_depth=7)
    >>> specs = [
    ...     TransformSpec(description="Add logging", expected_confidence=0.9),
    ...     TransformSpec(description="Add validation", expected_confidence=0.9),
    ... ]
    >>>
    >>> def transform_fn(source: str, description: str) -> tuple[str, float]:
    ...     new_source = llm_transform(source, description)
    ...     confidence = verify_grounding(source, new_source)
    ...     return new_source, confidence
    >>>
    >>> result = engine.apply_chain(initial_source, specs, transform_fn)
    >>> if result.success:
    ...     print(f"Final confidence: {result.overall_confidence:.1%}")

See Also:
    - shepherd_transform.transform_lock: Concurrent transformation safety
    - shepherd_transform.grounding: Behavioral grounding verification
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from shepherd_transform.source import ReconstructionError, reconstruct_task_class

if TYPE_CHECKING:
    from collections.abc import Callable

# =============================================================================
# Constants
# =============================================================================

DEFAULT_CONFIDENCE_DECAY = 0.95
"""Default multiplicative decay factor per transformation step."""

DEFAULT_MIN_CONFIDENCE = 0.5
"""Default minimum confidence threshold before stopping chain."""

DEFAULT_MAX_DEPTH = 7
"""Default maximum chain depth."""


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class TransformSpec:
    """Specification for a single transformation."""

    description: str
    expected_confidence: float = 0.9
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransformationResult:
    """Result of a single transformation."""

    spec: TransformSpec
    success: bool
    input_source: str
    output_source: str
    grounding_confidence: float
    error: str | None = None


@dataclass
class ChainResult:
    """Result of a transformation chain."""

    chain_id: str
    transformations: list[TransformationResult]
    final_source: str
    final_class: type | None
    overall_confidence: float
    success: bool
    stopped_at: int | None = None
    stop_reason: str | None = None
    checkpoints: list[str] = field(default_factory=list)

    @property
    def chain_depth(self) -> int:
        """Number of transformations attempted."""
        return len(self.transformations)

    @property
    def successful_transforms(self) -> int:
        """Number of successful transformations."""
        return sum(1 for t in self.transformations if t.success)

    @property
    def provenance(self) -> list[str]:
        """Provenance trail of successful transformation descriptions."""
        return [t.spec.description for t in self.transformations if t.success]


# =============================================================================
# Transform Function Protocol
# =============================================================================


class TransformFunction(Protocol):
    """Protocol for transformation functions."""

    def __call__(self, source: str, description: str) -> tuple[str, float]:
        """Apply transformation, return (new_source, grounding_confidence)."""
        ...


# =============================================================================
# Transformation Engine
# =============================================================================


class TransformationEngine:
    """Engine for applying transformation chains with confidence tracking.

    Provides sequential transformation with confidence degradation tracking,
    automatic stopping when confidence drops below threshold, and checkpointing
    for rollback support.
    """

    def __init__(
        self,
        confidence_decay: float = DEFAULT_CONFIDENCE_DECAY,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        if not 0 < confidence_decay <= 1:
            raise ValueError("confidence_decay must be in (0, 1]")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be in [0, 1]")
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")

        self.confidence_decay = confidence_decay
        self.min_confidence = min_confidence
        self.max_depth = max_depth

    def apply_chain(
        self,
        initial_source: str,
        specs: list[TransformSpec],
        transform_fn: TransformFunction,
        *,
        stop_on_failure: bool = True,
        reconstruct_final: bool = True,
    ) -> ChainResult:
        """Apply a chain of transformations.

        Args:
            initial_source: Starting source code
            specs: List of transformation specifications
            transform_fn: Function to apply each transformation
            stop_on_failure: If True, stop chain on first failure
            reconstruct_final: If True, reconstruct final class

        Returns:
            ChainResult with all transformation results and final state
        """
        chain_id = str(uuid.uuid4())[:8]
        results: list[TransformationResult] = []
        checkpoints: list[str] = [initial_source]
        current_source = initial_source
        overall_confidence = 1.0
        stopped_at = None
        stop_reason = None

        for i, spec in enumerate(specs):
            if i >= self.max_depth:
                stopped_at = i
                stop_reason = f"max_depth ({self.max_depth}) reached"
                break

            if overall_confidence < self.min_confidence:
                stopped_at = i
                stop_reason = f"confidence ({overall_confidence:.1%}) below threshold"
                break

            try:
                new_source, grounding = transform_fn(current_source, spec.description)
                result = TransformationResult(
                    spec=spec,
                    success=True,
                    input_source=current_source,
                    output_source=new_source,
                    grounding_confidence=grounding,
                )
            except Exception as e:  # noqa: BLE001
                result = TransformationResult(
                    spec=spec,
                    success=False,
                    input_source=current_source,
                    output_source=current_source,
                    grounding_confidence=0.0,
                    error=str(e),
                )

            results.append(result)

            if result.success:
                current_source = result.output_source
                step_confidence = result.grounding_confidence * self.confidence_decay
                overall_confidence *= step_confidence
                checkpoints.append(current_source)
            else:
                stopped_at = i
                stop_reason = f"transformation failed: {result.error}"
                if stop_on_failure:
                    break
                overall_confidence *= 0.1

        final_class = None
        if reconstruct_final and results and any(r.success for r in results):
            with contextlib.suppress(ReconstructionError, Exception):
                final_class = reconstruct_task_class(current_source, validate=True)

        return ChainResult(
            chain_id=chain_id,
            transformations=results,
            final_source=current_source,
            final_class=final_class,
            overall_confidence=overall_confidence,
            success=stopped_at is None and all(r.success for r in results),
            stopped_at=stopped_at,
            stop_reason=stop_reason,
            checkpoints=checkpoints,
        )

    def apply_chain_with_rollback(
        self,
        initial_source: str,
        specs: list[TransformSpec],
        transform_fn: TransformFunction,
        test_fn: Callable[[str], bool] | None = None,
    ) -> ChainResult:
        """Apply chain with automatic rollback on failure.

        On failure, rolls back to the last successful checkpoint.
        """
        chain_id = str(uuid.uuid4())[:8]
        results: list[TransformationResult] = []
        checkpoints: list[str] = [initial_source]
        checkpoint_confidences: list[float] = [1.0]
        current_source = initial_source
        overall_confidence = 1.0
        stopped_at = None
        stop_reason = None

        for i, spec in enumerate(specs):
            if i >= self.max_depth:
                stopped_at = i
                stop_reason = f"max_depth ({self.max_depth}) reached"
                break

            if overall_confidence < self.min_confidence:
                stopped_at = i
                stop_reason = "confidence below threshold"
                break

            try:
                new_source, grounding = transform_fn(current_source, spec.description)
                if test_fn is not None and not test_fn(new_source):
                    raise ValueError("Test function returned False")
                result = TransformationResult(
                    spec=spec,
                    success=True,
                    input_source=current_source,
                    output_source=new_source,
                    grounding_confidence=grounding,
                )
            except Exception as e:  # noqa: BLE001
                result = TransformationResult(
                    spec=spec,
                    success=False,
                    input_source=current_source,
                    output_source=current_source,
                    grounding_confidence=0.0,
                    error=str(e),
                )

            results.append(result)

            if result.success:
                current_source = result.output_source
                step_confidence = result.grounding_confidence * self.confidence_decay
                overall_confidence *= step_confidence
                checkpoints.append(current_source)
                checkpoint_confidences.append(overall_confidence)
            else:
                stopped_at = i
                stop_reason = f"transformation failed, rolled back: {result.error}"
                if checkpoint_confidences:
                    overall_confidence = checkpoint_confidences[-1]
                break

        final_class = None
        if results and any(r.success for r in results):
            with contextlib.suppress(ReconstructionError, Exception):
                final_class = reconstruct_task_class(current_source, validate=True)

        return ChainResult(
            chain_id=chain_id,
            transformations=results,
            final_source=current_source,
            final_class=final_class,
            overall_confidence=overall_confidence,
            success=stopped_at is None and all(r.success for r in results),
            stopped_at=stopped_at,
            stop_reason=stop_reason,
            checkpoints=checkpoints,
        )


# =============================================================================
# Utility Functions
# =============================================================================


def calculate_chain_confidence(
    grounding_scores: list[float],
    decay: float = DEFAULT_CONFIDENCE_DECAY,
) -> float:
    """Calculate overall confidence for a chain given grounding scores."""
    confidence = 1.0
    for score in grounding_scores:
        confidence *= score * decay
    return confidence


def estimate_safe_depth(
    expected_grounding: float = 0.9,
    decay: float = DEFAULT_CONFIDENCE_DECAY,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> int:
    """Estimate maximum safe chain depth for given parameters."""
    confidence = 1.0
    depth = 0
    step_factor = expected_grounding * decay

    while confidence >= min_confidence:
        confidence *= step_factor
        if confidence >= min_confidence:
            depth += 1
        else:
            break

    return depth


__all__ = [
    "DEFAULT_CONFIDENCE_DECAY",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MIN_CONFIDENCE",
    "ChainResult",
    "TransformFunction",
    "TransformSpec",
    "TransformationEngine",
    "TransformationResult",
    "calculate_chain_confidence",
    "estimate_safe_depth",
]
