"""Stream-layer codec for persistence serialization.

This module isolates all concrete ``EffectLayer`` construction and
serialization behind a single runtime-internal seam.  Persistence-facing
modules (``_persistence_writer``, ``_persistence_manager``) import codec
helpers from here instead of reaching into the kernel substrate directly.

See ``P0C-0-SCOPE-IMPORT-CONTRACTION-PLAN.md`` (PR 3) for the full rationale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shepherd_core.scope.stream import EffectLayer

from .effects import decode_effect

if TYPE_CHECKING:
    from shepherd_core.effects import EffectTypeRegistry


def layer_to_dict(layer: EffectLayer) -> dict[str, Any]:
    """Serialize an ``EffectLayer`` to a JSON-compatible dict."""
    return {
        "effect": layer.effect.model_dump(),
        "sequence": layer.sequence,
        "scope_id": layer.scope_id,
        "scope_depth": layer.scope_depth,
        "source_context": layer.source_context,
    }


def layer_from_dict(data: dict[str, Any], *, registry: EffectTypeRegistry | None = None) -> EffectLayer:
    """Deserialize an ``EffectLayer`` from a dict."""
    effect = decode_effect(data["effect"], registry=registry)
    return EffectLayer(
        effect=effect,
        sequence=data.get("sequence", 0),
        scope_id=data.get("scope_id"),
        scope_depth=data.get("scope_depth", 0),
        source_context=data.get("source_context"),
    )


def backfill_sequence(layer: EffectLayer, sequence: int) -> EffectLayer:
    """Re-create a layer with an assigned sequence number.

    Used for old persisted records that do not contain a ``sequence`` field.
    """
    return EffectLayer(
        effect=layer.effect,
        sequence=sequence,
        scope_id=layer.scope_id,
        scope_depth=layer.scope_depth,
        source_context=layer.source_context,
    )


__all__ = [
    "EffectLayer",
    "backfill_sequence",
    "layer_from_dict",
    "layer_to_dict",
]
