"""Explicit effect-type registry primitives."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .effects import Effect


class EffectTypeRegistry(Mapping[str, type["Effect"]]):
    """Immutable registry snapshot used for deterministic effect decode."""

    def __init__(self, effect_types: Mapping[str, type[Effect]] | None = None) -> None:
        self._effect_types = dict(effect_types or {})

    def __getitem__(self, effect_type: str) -> type[Effect]:
        return self._effect_types[effect_type]

    def __iter__(self) -> Iterator[str]:
        return iter(self._effect_types)

    def __len__(self) -> int:
        return len(self._effect_types)

    def get(self, effect_type: str, default: type[Effect] | None = None) -> type[Effect] | None:  # type: ignore[override]
        return self._effect_types.get(effect_type, default)

    def register(self, effect_type: str, effect_cls: type[Effect]) -> None:
        self._effect_types[effect_type] = effect_cls

    def resolve(self, effect_type: str, *, fallback: type[Effect]) -> type[Effect]:
        return self._effect_types.get(effect_type, fallback)

    def extend(
        self,
        effect_types: Mapping[str, type[Effect]] | EffectTypeRegistry,
    ) -> EffectTypeRegistry:
        merged = dict(self._effect_types)
        merged.update(dict(effect_types.items()))
        return EffectTypeRegistry(merged)

    def as_dict(self) -> dict[str, type[Effect]]:
        return dict(self._effect_types)


__all__ = ["EffectTypeRegistry"]
