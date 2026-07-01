"""Runtime-owned persistence manager for project-level storage organization."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ._persistence_project import ProjectId, ProjectMetadata
from ._persistence_stream import StreamId, StreamIndex, StreamMetadata
from ._persistence_writer import StreamReader, StreamWriter
from .effects import compose_effect_registry

if TYPE_CHECKING:
    from shepherd_core.effects import EffectTypeRegistry

    from shepherd_runtime.scope_types import EffectLayerLike

logger = logging.getLogger(__name__)


@dataclass
class PersistenceConfig:
    """Configuration for automatic persistence and caching."""

    enabled: bool = True
    base_dir: Path = field(default_factory=lambda: Path.home() / ".shepherd")
    cache_enabled: bool = True
    cache_policy: str = "strict"
    cache_mode: str = "outputs_only"
    cache_ttl_hours: int = 24


@dataclass
class PersistenceManager:
    """Manages persistence for a single project."""

    base_dir: Path
    project_id: ProjectId
    registry: EffectTypeRegistry = field(default_factory=compose_effect_registry, repr=False)
    _stream_writer: StreamWriter | None = field(default=None, repr=False)
    _index: StreamIndex | None = field(default=None, repr=False)
    _current_stream_id: StreamId | None = field(default=None, repr=False)

    @property
    def project_dir(self) -> Path:
        return self.base_dir / "projects" / self.project_id.hash

    @property
    def streams_dir(self) -> Path:
        return self.project_dir / "streams"

    @property
    def cache_dir(self) -> Path:
        return self.project_dir / "cache"

    @property
    def index_path(self) -> Path:
        return self.streams_dir / "index.json"

    @property
    def project_metadata_path(self) -> Path:
        return self.project_dir / "project.json"

    @property
    def current_symlink_path(self) -> Path:
        return self.streams_dir / "current"

    def initialize(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.streams_dir.mkdir(exist_ok=True)

        if self.project_metadata_path.exists():
            metadata = ProjectMetadata.load(self.project_metadata_path)

            if metadata.canonical_path != self.project_id.canonical_path:
                logger.warning(
                    "Project path changed from %s to %s. Storage may contain effects from the old location.",
                    metadata.canonical_path,
                    self.project_id.canonical_path,
                )

            metadata = metadata.with_access_update()
            metadata.save(self.project_metadata_path)
        else:
            metadata = ProjectMetadata(canonical_path=self.project_id.canonical_path)
            metadata.save(self.project_metadata_path)

        self._index = StreamIndex.load(self.index_path)

    def start_stream(self, continues_from: str | None = None) -> StreamId:
        if self._stream_writer is not None:
            raise RuntimeError("A stream is already open. Close it before starting a new one.")

        if self._index is None:
            raise RuntimeError("Manager not initialized. Call initialize() first.")

        stream_id = StreamId.generate()
        self._current_stream_id = stream_id
        self._stream_writer = StreamWriter(self.streams_dir, stream_id)
        self._stream_writer.open()

        metadata = StreamMetadata(
            stream_id=str(stream_id),
            continues_from=continues_from,
        )
        self._index.add_stream(metadata)
        self._index.current_stream_id = str(stream_id)
        self._save_index()
        self._update_current_symlink(stream_id)

        logger.debug("Started stream %s", stream_id)
        return stream_id

    def append_layer(self, layer: EffectLayerLike) -> int:
        if self._stream_writer is None:
            raise RuntimeError("No active stream. Call start_stream() first.")
        return self._stream_writer.append(layer)

    def close_stream(self) -> StreamMetadata | None:
        if self._stream_writer is None:
            return None

        closed_metadata = self._stream_writer.close()
        self._stream_writer = None

        if self._index is not None:
            existing = self._index.get_stream(closed_metadata.stream_id)
            if existing:
                final_metadata = StreamMetadata(
                    stream_id=closed_metadata.stream_id,
                    created_at=existing.created_at,
                    closed_at=closed_metadata.closed_at,
                    continues_from=existing.continues_from,
                    effect_count=closed_metadata.effect_count,
                )
            else:
                final_metadata = closed_metadata

            self._index.update_stream(final_metadata)
            self._index.current_stream_id = None
            self._save_index()
        else:
            final_metadata = closed_metadata

        self._current_stream_id = None
        logger.debug("Closed stream %s with %d effects", final_metadata.stream_id, final_metadata.effect_count)
        return final_metadata

    def read_stream(self, stream_id: str) -> list[EffectLayerLike]:
        stream_path = self.streams_dir / f"{stream_id}.jsonl"
        reader = StreamReader(stream_path, registry=self.registry)
        return reader.read_all()

    def read_latest_stream(self) -> list[EffectLayerLike]:
        if self._index is None:
            self._index = StreamIndex.load(self.index_path)

        latest = self._index.get_latest_stream()
        if latest is None:
            return []

        return self.read_stream(latest.stream_id)

    def read_stream_chain(self, stream_id: str | None = None) -> list[EffectLayerLike]:
        if self._index is None:
            self._index = StreamIndex.load(self.index_path)

        if stream_id is None:
            start = self._index.get_latest_stream()
            if start is None:
                return []
            stream_id = start.stream_id
        else:
            start = self._index.get_stream(stream_id)
            if start is None:
                return []

        chain: list[str] = []
        current: StreamMetadata | None = start
        while current is not None:
            chain.append(current.stream_id)
            current = self._index.get_stream(current.continues_from) if current.continues_from else None

        all_layers: list[EffectLayerLike] = []
        for stream_id_value in reversed(chain):
            layers = self.read_stream(stream_id_value)
            all_layers.extend(layers)

        return all_layers

    def get_stream_info(self) -> dict[str, StreamMetadata]:
        if self._index is None:
            self._index = StreamIndex.load(self.index_path)

        return {stream.stream_id: stream for stream in self._index.streams}

    def _save_index(self) -> None:
        if self._index is not None:
            self._index.save(self.index_path)

    def _update_current_symlink(self, stream_id: StreamId) -> None:
        stream_file = f"{stream_id.value}.jsonl"

        if self.current_symlink_path.exists() or self.current_symlink_path.is_symlink():
            self.current_symlink_path.unlink()

        try:
            self.current_symlink_path.symlink_to(stream_file)
        except OSError as error:
            logger.debug("Could not create current symlink: %s", error)


__all__ = [
    "PersistenceConfig",
    "PersistenceManager",
]
