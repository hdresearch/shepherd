"""Public runtime context-materialization owner paths."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, Self, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class MaterializationIntent:
    """Base class for materialization intents."""

    context_type: str
    context_id: str
    target_path: Path

    def with_commit_message(self, message: str) -> Self:
        """Return intent with commit message (if applicable)."""
        return self


@dataclass(frozen=True)
class MaterializationResult:
    """Result of a materialization attempt."""

    success: bool
    paths_affected: tuple[str, ...] = ()
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        paths_affected: tuple[str, ...] = (),
        **metadata: Any,
    ) -> MaterializationResult:
        """Create a successful result."""
        return cls(success=True, paths_affected=paths_affected, metadata=metadata)

    @classmethod
    def failure(cls, error: str) -> MaterializationResult:
        """Create a failed result."""
        return cls(success=False, error=error)


@runtime_checkable
class Materializable(Protocol):
    """Protocol for contexts that can be materialized."""

    @property
    def has_pending_changes(self) -> bool: ...

    def materialization_intent(self) -> MaterializationIntent: ...

    def with_materialized(self, result: MaterializationResult) -> Self: ...


def is_materializable(value: object) -> bool:
    """Check if a value implements the Materializable protocol."""
    return isinstance(value, Materializable)


class ContextMaterializer(Protocol):
    """Protocol for context-based materializers that execute intents."""

    def materialize(self, intent: MaterializationIntent) -> MaterializationResult: ...

    def can_rollback(self) -> bool: ...

    def rollback(
        self,
        intent: MaterializationIntent,
        result: MaterializationResult,
    ) -> None: ...


Materializer = ContextMaterializer
MaterializerFactory = Callable[[], Materializer]
MaterializationAdmissionHook = Callable[[MaterializationIntent], None]
_CONTEXT_MATERIALIZER_REGISTRY: dict[str, ContextMaterializer] = {}
_MATERIALIZATION_ADMISSION_HOOKS: list[MaterializationAdmissionHook] = []


def register_context_materializer(context_type: str, materializer: ContextMaterializer) -> None:
    """Register a context materializer for a context type."""
    _CONTEXT_MATERIALIZER_REGISTRY[context_type] = materializer


def get_context_materializer(context_type: str) -> ContextMaterializer | None:
    """Get context materializer for a context type."""
    return _CONTEXT_MATERIALIZER_REGISTRY.get(context_type)


def clear_context_materializer_registry() -> None:
    """Clear all registered context materializers. For testing only."""
    _CONTEXT_MATERIALIZER_REGISTRY.clear()


def register_materialization_admission_hook(hook: MaterializationAdmissionHook) -> None:
    """Register a hook that can reject context materialization before I/O."""
    if hook not in _MATERIALIZATION_ADMISSION_HOOKS:
        _MATERIALIZATION_ADMISSION_HOOKS.append(hook)


def clear_materialization_admission_hooks() -> None:
    """Clear materialization admission hooks. For testing only."""
    _MATERIALIZATION_ADMISSION_HOOKS.clear()


def run_materialization_admission_hooks(intent: MaterializationIntent) -> None:
    """Run registered materialization admission hooks for one intent."""
    for hook in tuple(_MATERIALIZATION_ADMISSION_HOOKS):
        hook(intent)


register_materializer = register_context_materializer
get_materializer = get_context_materializer
clear_materializer_registry = clear_context_materializer_registry

__all__ = [
    "ContextMaterializer",
    "Materializable",
    "MaterializationAdmissionHook",
    "MaterializationIntent",
    "MaterializationResult",
    "Materializer",
    "clear_context_materializer_registry",
    "clear_materialization_admission_hooks",
    "clear_materializer_registry",
    "get_context_materializer",
    "get_materializer",
    "is_materializable",
    "register_context_materializer",
    "register_materialization_admission_hook",
    "register_materializer",
    "run_materialization_admission_hooks",
]
