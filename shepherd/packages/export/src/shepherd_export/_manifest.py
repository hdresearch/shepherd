"""Tier 3 manifest models for lossless trajectory export.

Defines the directory-based trajectory format:
  output/
  ├── manifest.json       ← TrajectoryManifest
  ├── scope_abc123.jsonl  ← merged scope
  ├── scope_def456.jsonl  ← forked scope
  └── scope_ghi789.jsonl  ← discarded scope (preserved!)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ScopeNode:
    """A single scope in the trajectory's scope tree."""

    scope_id: str
    parent_scope_id: str | None
    stream_file: str
    status: Literal["merged", "discarded", "active"]
    depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrajectoryManifest:
    """Manifest for a lossless trajectory export directory."""

    version: str = "1.0"
    root_scope_id: str = "root"
    scopes: tuple[ScopeNode, ...] = ()
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def manifest_to_dict(manifest: TrajectoryManifest) -> dict[str, Any]:
    """Serialize a TrajectoryManifest to a JSON-compatible dict."""
    return {
        "version": manifest.version,
        "root_scope_id": manifest.root_scope_id,
        "scopes": [
            {
                "scope_id": scope.scope_id,
                "parent_scope_id": scope.parent_scope_id,
                "stream_file": scope.stream_file,
                "status": scope.status,
                "depth": scope.depth,
                "metadata": scope.metadata,
            }
            for scope in manifest.scopes
        ],
        "created_at": manifest.created_at,
        "metadata": manifest.metadata,
    }


def manifest_from_dict(data: dict[str, Any]) -> TrajectoryManifest:
    """Deserialize a TrajectoryManifest from a dict."""
    scopes = tuple(
        ScopeNode(
            scope_id=scope["scope_id"],
            parent_scope_id=scope.get("parent_scope_id"),
            stream_file=scope["stream_file"],
            status=scope.get("status", "active"),
            depth=scope.get("depth", 0),
            metadata=scope.get("metadata", {}),
        )
        for scope in data.get("scopes", [])
    )
    return TrajectoryManifest(
        version=data.get("version", "1.0"),
        root_scope_id=data.get("root_scope_id", "root"),
        scopes=scopes,
        created_at=data.get("created_at", ""),
        metadata=data.get("metadata", {}),
    )


__all__ = ["ScopeNode", "TrajectoryManifest", "manifest_from_dict", "manifest_to_dict"]
