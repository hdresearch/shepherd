"""Runtime-owned effect materialization protocols and registry helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from shepherd_core.effects import Effect

E_contra = TypeVar("E_contra", bound="Effect", contravariant=True)

logger = logging.getLogger(__name__)


class MaterializationError(Exception):
    """Raised when effect materialization fails."""

    def __init__(self, effect: Any, error: str):
        self.effect = effect
        self.error = error
        super().__init__(f"Failed to materialize {type(effect).__name__}: {error}")


class ReversalError(Exception):
    """Raised when effect reversal fails or is not supported."""

    def __init__(self, effect: Any, error: str):
        self.effect = effect
        self.error = error
        super().__init__(f"Failed to reverse {type(effect).__name__}: {error}")


@dataclass(frozen=True)
class MaterializationResult:
    """Result of materializing one effect."""

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
        return cls(success=True, paths_affected=paths_affected, metadata=metadata)

    @classmethod
    def fail(cls, error: str) -> MaterializationResult:
        return cls(success=False, error=error)


@runtime_checkable
class Materializer(Protocol[E_contra]):  # type: ignore[misc]
    """Protocol for effect materializers used by ``scope.materialize()``."""

    @property
    def effect_type(self) -> type[E_contra]: ...

    def materialize(self, effect: E_contra) -> MaterializationResult: ...

    def can_reverse(self, effect: E_contra) -> bool: ...

    def reverse(self, effect: E_contra) -> None: ...


class MaterializerRegistry:
    """Registry for effect materializers with type-based dispatch."""

    def __init__(self) -> None:
        self._materializers: dict[type, Materializer[Any]] = {}

    def register(self, materializer: Materializer[Any]) -> None:
        effect_type = materializer.effect_type
        if effect_type in self._materializers:
            logger.warning("Replacing existing materializer for %s", effect_type.__name__)
        self._materializers[effect_type] = materializer

    def unregister(self, effect_type: type) -> Materializer[Any] | None:
        return self._materializers.pop(effect_type, None)

    def get(self, effect: Effect) -> Materializer[Any] | None:
        effect_cls = type(effect)
        if effect_cls in self._materializers:
            return self._materializers[effect_cls]

        for base in effect_cls.__mro__[1:]:
            if base in self._materializers:
                return self._materializers[base]

        return None

    def has_materializer(self, effect: Effect) -> bool:
        return self.get(effect) is not None

    def materialize(self, effect: Effect) -> MaterializationResult:
        materializer = self.get(effect)
        if materializer is None:
            return MaterializationResult.ok()

        try:
            return materializer.materialize(effect)
        except Exception as e:
            logger.exception("Materializer for %s raised exception: %s", type(effect).__name__, e)
            return MaterializationResult.fail(str(e))

    def can_reverse(self, effect: Effect) -> bool:
        materializer = self.get(effect)
        if materializer is None:
            return False
        return materializer.can_reverse(effect)

    def reverse(self, effect: Effect) -> None:
        materializer = self.get(effect)
        if materializer is None:
            raise ValueError(f"No materializer registered for {type(effect).__name__}")
        materializer.reverse(effect)

    def registered_types(self) -> list[type]:
        return list(self._materializers.keys())

    def __len__(self) -> int:
        return len(self._materializers)

    def __contains__(self, effect_type: type) -> bool:
        return effect_type in self._materializers


_default_registry: MaterializerRegistry | None = None


def get_materializer_registry() -> MaterializerRegistry:
    """Get the global default registry, creating it lazily."""
    global _default_registry
    if _default_registry is None:
        _default_registry = MaterializerRegistry()
    return _default_registry


def reset_materializer_registry() -> None:
    """Reset the global registry. Intended for tests."""
    global _default_registry
    _default_registry = None


def register_materializer(materializer: Materializer[Any]) -> None:
    get_materializer_registry().register(materializer)


def get_materializer(effect: Effect) -> Materializer[Any] | None:
    return get_materializer_registry().get(effect)


__all__ = [
    "MaterializationError",
    "MaterializationResult",
    "Materializer",
    "MaterializerRegistry",
    "ReversalError",
    "get_materializer",
    "get_materializer_registry",
    "register_materializer",
    "reset_materializer_registry",
]
