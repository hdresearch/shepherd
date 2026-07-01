"""Private helpers for registry-backed effect decode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .effects import Effect
    from .registry import EffectTypeRegistry


def resolve_effect_registry(registry: EffectTypeRegistry | None = None) -> EffectTypeRegistry:
    """Return the explicit registry or the kernel default."""
    from .effects import KERNEL_EFFECT_REGISTRY

    return registry or KERNEL_EFFECT_REGISTRY


def resolve_effect_class(name: str, *, registry: EffectTypeRegistry | None = None) -> type[Effect]:
    """Resolve an effect class using an explicit or kernel registry."""
    from .effects import Effect

    decode_registry = resolve_effect_registry(registry)
    return decode_registry.resolve(name, fallback=Effect)


def decode_effect(data: dict[str, Any], *, registry: EffectTypeRegistry | None = None) -> Effect:
    """Decode a serialized effect using an explicit or kernel registry."""
    payload = dict(data)
    effect_type = payload.get("effect_type", "base")
    effect_class = resolve_effect_class(effect_type, registry=registry)
    return effect_class.model_validate(payload)


__all__ = ["decode_effect", "resolve_effect_class", "resolve_effect_registry"]
