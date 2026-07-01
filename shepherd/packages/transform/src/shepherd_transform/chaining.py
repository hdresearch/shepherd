"""Public transform-owned task-transformation chaining owner paths."""

from __future__ import annotations

from ._chaining_impl import (
    DEFAULT_CONFIDENCE_DECAY,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MIN_CONFIDENCE,
    ChainResult,
    TransformationEngine,
    TransformationResult,
    TransformFunction,
    TransformSpec,
    calculate_chain_confidence,
    estimate_safe_depth,
)

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

for _name in __all__:
    _value = globals()[_name]
    if hasattr(_value, "__module__"):
        _value.__module__ = __name__
