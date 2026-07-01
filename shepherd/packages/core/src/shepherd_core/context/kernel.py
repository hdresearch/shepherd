"""Kernel-safe execution-context protocol surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from ..types import (
    CAPABILITY_TOOL_MAP,
    TOOL_CAPABILITY_REQUIREMENTS,
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..effects import Effect


@runtime_checkable
class ExecutionContext(Protocol):
    """Kernel lifecycle protocol for stateful execution contexts."""

    @property
    def context_id(self) -> str:
        """Stable identifier for effect attribution and correlation."""
        ...

    @property
    def reversibility(self) -> ReversibilityLevel:
        """How reversible are effects on this context?"""
        ...

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        """Return provider configuration. Must be pure."""
        ...

    def prepare(self) -> Self:
        """Prepare for execution. May have side effects."""
        ...

    def extract_effects(
        self,
        sandbox: object | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        """Extract effects from execution result. Must be pure."""
        ...

    def apply_effect(self, effect: Effect) -> Self:
        """Apply effect to derive new context state. Must be pure."""
        ...

    def cleanup(self, error: Exception | None = None) -> None:
        """Release resources. Always called."""
        ...


class ExecutionContextDefaults:
    """Mixin providing sensible defaults for the kernel lifecycle protocol."""

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        return ProviderBinding()

    def prepare(self) -> Self:
        return self

    def cleanup(self, error: Exception | None = None) -> None:
        return None

    def extract_effects(
        self,
        sandbox: object | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        return []

    def apply_effect(self, effect: Effect) -> Self:
        return self


def is_execution_context(value: object) -> bool:
    """Check if a value implements ExecutionContext."""
    return isinstance(value, ExecutionContext)


def compute_composite_reversibility(
    contexts: list[ExecutionContext],
) -> ReversibilityLevel:
    """Compute composite reversibility from multiple contexts."""
    return ReversibilityLevel.compose_all(ctx.reversibility for ctx in contexts)


def is_reversible(effect: object) -> bool:
    """Check if an effect implements the legacy reversible interface."""
    return hasattr(effect, "reverse") and callable(effect.reverse)


__all__ = [
    "CAPABILITY_TOOL_MAP",
    "TOOL_CAPABILITY_REQUIREMENTS",
    "ExecutionContext",
    "ExecutionContextDefaults",
    "compute_composite_reversibility",
    "is_execution_context",
    "is_reversible",
]
