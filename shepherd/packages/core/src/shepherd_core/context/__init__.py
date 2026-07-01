"""Kernel execution-context barrel."""

from __future__ import annotations

from .kernel import (
    CAPABILITY_TOOL_MAP,
    TOOL_CAPABILITY_REQUIREMENTS,
    ExecutionContext,
    ExecutionContextDefaults,
    compute_composite_reversibility,
    is_execution_context,
    is_reversible,
)

__all__ = [
    "CAPABILITY_TOOL_MAP",
    "TOOL_CAPABILITY_REQUIREMENTS",
    "ExecutionContext",
    "ExecutionContextDefaults",
    "compute_composite_reversibility",
    "is_execution_context",
    "is_reversible",
]
