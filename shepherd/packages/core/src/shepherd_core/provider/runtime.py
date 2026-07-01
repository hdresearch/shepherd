"""Provider runtime protocol and adapters.

This module defines the narrow runtime surface that providers depend on during
execution. It intentionally exposes only effect emission and task attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..effects import Effect


@runtime_checkable
class EffectSink(Protocol):
    """Minimal effect-emission interface for providers."""

    def emit(self, effect: Effect) -> None:
        """Emit an effect."""
        ...


@runtime_checkable
class ProviderRuntime(Protocol):
    """Minimal runtime information available to providers."""

    @property
    def effects(self) -> EffectSink:
        """Effect sink used for provider emission."""
        ...

    @property
    def task_name(self) -> str | None:
        """Task name for effect attribution."""
        ...


@dataclass(frozen=True)
class DefaultProviderRuntime:
    """Concrete ProviderRuntime adapter for existing emitters."""

    _effects: EffectSink
    _task_name: str | None = None

    @property
    def effects(self) -> EffectSink:
        return self._effects

    @property
    def task_name(self) -> str | None:
        return self._task_name

    @classmethod
    def from_emitter(
        cls,
        emitter: EffectSink,
        task_name: str | None = None,
    ) -> DefaultProviderRuntime:
        """Build a provider runtime from any object with an ``emit()`` method."""
        return cls(_effects=emitter, _task_name=task_name)


__all__ = [
    "DefaultProviderRuntime",
    "EffectSink",
    "ProviderRuntime",
]
