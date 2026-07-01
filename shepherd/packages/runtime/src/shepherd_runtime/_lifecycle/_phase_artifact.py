"""ArtifactPhase: Collect artifacts from .artifacts/ directory.

Phase 4 of the lifecycle pipeline. This phase collects artifact files
written by the provider during execution.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shepherd_core.errors import ArtifactNotFoundError

from shepherd_runtime.task.artifacts import read_artifact, should_parse_json

from ._phase_base import PhaseBase

if TYPE_CHECKING:
    from shepherd_core.effects import Effect

    from ._emitter import EffectEmitter
    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class ArtifactPhase(PhaseBase):
    """Phase 4: Collect artifacts from .artifacts/ directory.

    Reads: artifact_markers, composed_binding (for cwd)
    Writes: artifact_outputs, artifact_effects

    Artifact collection reads files written by the provider during execution.
    Validates required artifacts before deleting any files.
    """

    def __init__(self, emitter: EffectEmitter) -> None:
        self._emitter = emitter

    @property
    def name(self) -> str:
        return "artifact"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        # Skip if cache hit (no artifacts to collect)
        if ctx.cache_hit:
            logger.debug("Skipping artifact phase - cache hit")
            return ctx.with_artifacts(outputs={}, effects=())

        if not ctx.artifact_markers:
            return ctx.with_artifacts(outputs={}, effects=())

        from shepherd_core.effects import ArtifactWritten

        # Determine artifacts directory from composed binding
        cwd = ctx.composed_binding.cwd if ctx.composed_binding else None
        artifacts_dir = Path(cwd or ".") / ".artifacts"

        outputs: dict[str, Any] = {}
        effects: list[Effect] = []
        missing_required: list[str] = []
        collected_paths: list[Path] = []  # Track paths for deferred deletion

        for field_name, marker in ctx.artifact_markers.items():
            artifact_path = artifacts_dir / marker.filename

            if not artifact_path.exists():
                # Check if artifact is required (default is True for backward compat)
                is_required = getattr(marker, "required", True)
                if is_required:
                    missing_required.append(field_name)
                    logger.warning(
                        "Required artifact '%s' not found at %s",
                        field_name,
                        artifact_path,
                    )
                else:
                    logger.debug(
                        "Optional artifact '%s' not found at %s",
                        field_name,
                        artifact_path,
                    )
                continue

            try:
                # Use read_artifact to handle JSON parsing for dict/list types
                content = read_artifact(artifacts_dir, marker.filename, marker.inner_type)
                outputs[field_name] = content
                collected_paths.append(artifact_path)

                # Determine content type for effect metadata
                content_type = "json" if should_parse_json(marker.inner_type) else "text"

                # Serialize content for effect storage
                content_str = content if isinstance(content, str) else json.dumps(content)
                content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()

                effect = ArtifactWritten(
                    field_name=field_name,
                    filename=marker.filename,
                    size_bytes=artifact_path.stat().st_size,
                    content_type=content_type,
                    path=str(artifact_path),
                    content=content_str,
                    content_hash=content_hash,
                    task_name=ctx.task_name,
                    provider_id=ctx.effective_provider_id,
                )
                effects.append(effect)
                self._emitter.emit(effect)

                logger.debug(
                    "Collected artifact '%s' (%d bytes)",
                    field_name,
                    len(content),
                )

            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Failed to collect artifact '%s': %s",
                    field_name,
                    e,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
                # Treat read failures as missing if required
                is_required = getattr(marker, "required", True)
                if is_required:
                    missing_required.append(field_name)

        # Validate BEFORE deleting - don't delete artifacts if validation fails
        if missing_required:
            # Use the first missing artifact for the error
            first_missing = missing_required[0]
            marker = ctx.artifact_markers[first_missing]
            raise ArtifactNotFoundError(
                filename=marker.filename,
                expected_path=artifacts_dir / marker.filename,
                field_name=first_missing,
            )

        # Only delete artifacts after validation passes
        for artifact_path in collected_paths:
            try:
                artifact_path.unlink()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Failed to delete artifact file %s: %s",
                    artifact_path,
                    e,
                )

        return ctx.with_artifacts(outputs=outputs, effects=tuple(effects))


__all__ = ["ArtifactPhase"]
