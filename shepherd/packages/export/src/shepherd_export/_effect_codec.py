"""Private effect codec helpers for export/import surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shepherd_core.effects import effect_from_dict
from shepherd_core.scope.stream import EffectLayer, Stream

if TYPE_CHECKING:
    from shepherd_core.effects import EffectTypeRegistry


def layer_to_jsonl_dict(layer: EffectLayer) -> dict[str, Any]:
    """Serialize an EffectLayer to the nested JSONL trajectory format."""
    return {
        "effect": layer.effect.model_dump(),
        "sequence": layer.sequence,
        "scope_id": layer.scope_id,
        "scope_depth": layer.scope_depth,
        "source_context": layer.source_context,
    }


def layer_from_jsonl_dict(
    data: dict[str, Any],
    *,
    registry: EffectTypeRegistry | None = None,
) -> EffectLayer:
    """Deserialize an EffectLayer from the nested JSONL trajectory format."""
    effect = effect_from_dict(data["effect"], registry=registry)
    return EffectLayer(
        effect=effect,
        sequence=data.get("sequence", 0),
        scope_id=data.get("scope_id"),
        scope_depth=data.get("scope_depth", 0),
        source_context=data.get("source_context"),
    )


def stream_from_timeline_dicts(
    data: list[dict[str, Any]],
    *,
    registry: EffectTypeRegistry | None = None,
) -> Stream:
    """Deserialize a flat exported timeline into a Stream."""
    layers: list[EffectLayer] = []
    for index, item in enumerate(data):
        layer_data = item.copy()
        sequence = layer_data.pop("_sequence", index)
        source_context = layer_data.pop("_source_context", None)
        scope_id = layer_data.pop("_scope_id", None)
        scope_depth = layer_data.pop("_scope_depth", 0)
        effect = effect_from_dict(layer_data, registry=registry)
        if source_context is None:
            source_context = getattr(effect, "context_id", None)
        layers.append(
            EffectLayer(
                effect=effect,
                sequence=sequence,
                source_context=source_context,
                scope_id=scope_id,
                scope_depth=scope_depth,
            )
        )
    return Stream(_layers=tuple(layers))


__all__ = ["layer_from_jsonl_dict", "layer_to_jsonl_dict", "stream_from_timeline_dicts"]
