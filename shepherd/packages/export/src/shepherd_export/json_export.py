"""Tier 1: Flat JSON summary export/import.

Produces a JSON object with metadata + timeline array.
Each timeline entry is the full effect model_dump() with layer metadata.
No external dependencies — works standalone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shepherd_core.effects import EffectTypeRegistry
    from shepherd_core.scope.stream import Stream

from ._effect_codec import stream_from_timeline_dicts


def to_json(
    stream: Stream,
    path: str | Path | None = None,
    *,
    indent: int = 2,
) -> str:
    """Export a Stream as a flat JSON summary.

    Produces:
        {
            "total_effects": N,
            "effect_types": ["task_started", ...],
            "timeline": [{_sequence, _scope_id, effect_type, ...}, ...]
        }

    Args:
        stream: The effect stream to export.
        path: Optional file path to write to.
        indent: JSON indentation level.

    Returns:
        JSON string.
    """
    timeline = stream.to_dicts()

    seen: dict[str, None] = {}
    for entry in timeline:
        effect_type = entry.get("effect_type", "base")
        if effect_type not in seen:
            seen[effect_type] = None

    doc: dict[str, Any] = {
        "total_effects": len(timeline),
        "effect_types": list(seen),
        "timeline": timeline,
    }

    result = json.dumps(doc, indent=indent, default=str)

    if path is not None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result, encoding="utf-8")

    return result


def from_json(source: str | Path, *, registry: EffectTypeRegistry | None = None) -> Stream:
    """Import a Stream from a JSON summary.

    Accepts either a JSON string or a file path.

    Args:
        source: JSON string or path to a JSON file.
        registry: Optional explicit registry snapshot for effect decode.

    Returns:
        Reconstructed Stream.
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    elif isinstance(source, str):
        stripped = source.lstrip()
        if stripped.startswith(("{", "[")):
            text = source
        else:
            text = Path(source).read_text(encoding="utf-8")
    else:
        text = str(source)

    doc = json.loads(text)

    if isinstance(doc, list):
        timeline = doc
    else:
        timeline = doc.get("timeline", [])

    return stream_from_timeline_dicts(timeline, registry=registry)


__all__ = ["from_json", "to_json"]
