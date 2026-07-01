"""Tier 3: Lossless trajectory export/import with scope tree.

Directory-based format preserving the full scope tree including discarded branches.
Each scope gets its own JSONL file using the same format as StreamWriter.
No external dependencies — works standalone.

Output format:
    output/
    ├── manifest.json
    ├── scope_root.jsonl
    ├── scope_fork1.jsonl
    └── scope_discarded.jsonl  (preserved!)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from shepherd_core.scope.stream import EffectLayer, Stream

if TYPE_CHECKING:
    from shepherd_core.effects import EffectTypeRegistry

from ._effect_codec import layer_from_jsonl_dict, layer_to_jsonl_dict
from ._manifest import (
    ScopeNode,
    TrajectoryManifest,
    manifest_from_dict,
    manifest_to_dict,
)


@dataclass
class ScopeInfo:
    """Information about a single scope in a trajectory."""

    scope_id: str
    parent_scope_id: str | None
    stream: Stream
    status: Literal["merged", "discarded", "active"] = "active"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrajectoryResult:
    """Result from loading a trajectory directory."""

    root_stream: Stream
    scope_streams: dict[str, Stream]
    manifest: TrajectoryManifest


def _write_stream_jsonl(stream: Stream, path: Path) -> None:
    """Write a stream to a JSONL file."""
    with open(path, "w", encoding="utf-8") as handle:
        for layer in stream:
            line = json.dumps(layer_to_jsonl_dict(layer), separators=(",", ":"), default=str)
            handle.write(line + "\n")


def _read_stream_jsonl(path: Path, *, registry: EffectTypeRegistry | None = None) -> Stream:
    """Read a stream from a JSONL file."""
    layers: list[EffectLayer] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            layers.append(layer_from_jsonl_dict(data, registry=registry))
    return Stream(_layers=tuple(layers))


def _sanitize_scope_id(scope_id: str) -> str:
    """Make a scope_id safe for use as a filename."""
    return scope_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def to_trajectory(
    stream: Stream,
    output_dir: str | Path,
    *,
    scope_tree: dict[str, ScopeInfo] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Export a stream, and optionally its scope tree, to a trajectory directory."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(tz=timezone.utc).isoformat()

    if scope_tree is None:
        stream_file = "scope_root.jsonl"
        _write_stream_jsonl(stream, output_dir / stream_file)

        manifest = TrajectoryManifest(
            root_scope_id="root",
            scopes=(
                ScopeNode(
                    scope_id="root",
                    parent_scope_id=None,
                    stream_file=stream_file,
                    status="active",
                    depth=0,
                ),
            ),
            created_at=now,
            metadata=metadata or {},
        )
    else:
        scope_nodes: list[ScopeNode] = []
        root_scope_id: str | None = None

        for scope_id, info in scope_tree.items():
            safe_id = _sanitize_scope_id(scope_id)
            stream_file = f"scope_{safe_id}.jsonl"
            _write_stream_jsonl(info.stream, output_dir / stream_file)

            scope_nodes.append(
                ScopeNode(
                    scope_id=scope_id,
                    parent_scope_id=info.parent_scope_id,
                    stream_file=stream_file,
                    status=info.status,
                    depth=0 if info.parent_scope_id is None else 1,
                    metadata=info.metadata,
                )
            )

            if info.parent_scope_id is None:
                root_scope_id = scope_id

        if root_scope_id is None:
            root_scope_id = next(iter(scope_tree))

        _write_stream_jsonl(stream, output_dir / "scope_root.jsonl")

        manifest = TrajectoryManifest(
            root_scope_id=root_scope_id,
            scopes=tuple(scope_nodes),
            created_at=now,
            metadata=metadata or {},
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_to_dict(manifest), indent=2, default=str),
        encoding="utf-8",
    )

    return output_dir


def from_trajectory(
    trajectory_dir: str | Path,
    *,
    registry: EffectTypeRegistry | None = None,
) -> TrajectoryResult:
    """Import a trajectory from a directory."""
    trajectory_dir = Path(trajectory_dir)
    manifest_path = trajectory_dir / "manifest.json"

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = manifest_from_dict(manifest_data)

    scope_streams: dict[str, Stream] = {}
    root_stream = Stream()

    for scope_node in manifest.scopes:
        stream_path = trajectory_dir / scope_node.stream_file
        if stream_path.exists():
            scope_streams[scope_node.scope_id] = _read_stream_jsonl(stream_path, registry=registry)
        else:
            scope_streams[scope_node.scope_id] = Stream()

    if manifest.root_scope_id in scope_streams:
        root_stream = scope_streams[manifest.root_scope_id]

    return TrajectoryResult(
        root_stream=root_stream,
        scope_streams=scope_streams,
        manifest=manifest,
    )


__all__ = ["ScopeInfo", "TrajectoryResult", "from_trajectory", "to_trajectory"]
