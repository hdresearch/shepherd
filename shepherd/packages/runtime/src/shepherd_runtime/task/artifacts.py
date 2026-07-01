"""Runtime-owned artifact handling for file-based task outputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_origin

from shepherd_core.effects import ArtifactMissing, ArtifactWritten
from shepherd_core.errors import ArtifactNotFoundError

if TYPE_CHECKING:
    from .markers import ArtifactMarker


def should_parse_json(inner_type: Any) -> bool:
    """Determine if artifact content should be parsed as JSON."""
    origin = get_origin(inner_type)
    if inner_type in (dict, list):
        return True
    return origin in (dict, list)


def read_artifact(
    artifacts_dir: Path,
    filename: str,
    inner_type: Any,
) -> Any:
    """Read and parse an artifact file."""
    artifact_path = artifacts_dir / filename
    content = artifact_path.read_text()

    if should_parse_json(inner_type):
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Invalid JSON in artifact '{filename}': {e.msg}",
                e.doc,
                e.pos,
            ) from e

    return content


def collect_artifacts(
    artifacts_dir: Path | None,
    artifact_fields: dict[str, ArtifactMarker],
    clear_after: bool = True,
) -> tuple[dict[str, Any], list[ArtifactWritten | ArtifactMissing]]:
    """Collect artifacts from a directory and return values plus effects."""
    outputs: dict[str, Any] = {}
    effects: list[ArtifactWritten | ArtifactMissing] = []

    if not artifact_fields:
        return outputs, effects

    if artifacts_dir is None or not artifacts_dir.exists():
        for field_name, marker in artifact_fields.items():
            if marker.required:
                raise ArtifactNotFoundError(
                    marker.filename,
                    Path(f"<no artifacts_dir>/{marker.filename}"),
                    field_name,
                )
            effects.append(
                ArtifactMissing(
                    filename=marker.filename,
                    field_name=field_name,
                    required=marker.required,
                )
            )
            outputs[field_name] = None
        return outputs, effects

    for field_name, marker in artifact_fields.items():
        artifact_path = artifacts_dir / marker.filename

        if not artifact_path.exists():
            if marker.required:
                raise ArtifactNotFoundError(marker.filename, artifact_path, field_name)
            effects.append(
                ArtifactMissing(
                    filename=marker.filename,
                    field_name=field_name,
                    required=marker.required,
                )
            )
            outputs[field_name] = None
            continue

        content = read_artifact(artifacts_dir, marker.filename, marker.inner_type)
        outputs[field_name] = content

        content_str = content if isinstance(content, str) else json.dumps(content)
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        content_type = "json" if should_parse_json(marker.inner_type) else "text"
        effects.append(
            ArtifactWritten(
                filename=marker.filename,
                path=str(artifact_path),
                content_type=content_type,
                size_bytes=artifact_path.stat().st_size,
                field_name=field_name,
                content=content_str,
                content_hash=content_hash,
            )
        )

    if clear_after:
        for path in artifacts_dir.iterdir():
            if path.is_file():
                path.unlink()

    return outputs, effects


__all__ = [
    "ArtifactNotFoundError",
    "collect_artifacts",
    "read_artifact",
    "should_parse_json",
]
