"""Runtime-owned stream identification and metadata for persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class StreamId:
    """Unique identifier for an effect stream."""

    value: str

    @classmethod
    def generate(cls) -> StreamId:
        return cls(value=uuid4().hex)

    @classmethod
    def from_string(cls, value: str) -> StreamId:
        return cls(value=value)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"StreamId({self.value[:8]}...)"


@dataclass
class StreamMetadata:
    """Metadata about a single stream."""

    stream_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    continues_from: str | None = None
    effect_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "created_at": self.created_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "continues_from": self.continues_from,
            "effect_count": self.effect_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamMetadata:
        return cls(
            stream_id=data["stream_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            closed_at=(datetime.fromisoformat(data["closed_at"]) if data.get("closed_at") else None),
            continues_from=data.get("continues_from"),
            effect_count=data.get("effect_count", 0),
        )

    def with_closed(self, effect_count: int) -> StreamMetadata:
        return StreamMetadata(
            stream_id=self.stream_id,
            created_at=self.created_at,
            closed_at=datetime.now(timezone.utc),
            continues_from=self.continues_from,
            effect_count=effect_count,
        )


@dataclass
class StreamIndex:
    """Index of all streams for a project."""

    streams: list[StreamMetadata] = field(default_factory=list)
    current_stream_id: str | None = None

    def add_stream(self, metadata: StreamMetadata) -> None:
        self.streams.append(metadata)

    def get_stream(self, stream_id: str) -> StreamMetadata | None:
        for stream in self.streams:
            if stream.stream_id == stream_id:
                return stream
        return None

    def update_stream(self, metadata: StreamMetadata) -> None:
        for index, stream in enumerate(self.streams):
            if stream.stream_id == metadata.stream_id:
                self.streams[index] = metadata
                return
        raise ValueError(f"Stream {metadata.stream_id} not found in index")

    def get_latest_stream(self) -> StreamMetadata | None:
        if not self.streams:
            return None
        sorted_streams = sorted(self.streams, key=lambda stream: stream.created_at, reverse=True)
        return sorted_streams[0]

    def get_current_stream(self) -> StreamMetadata | None:
        if self.current_stream_id is None:
            return None
        return self.get_stream(self.current_stream_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "streams": [stream.to_dict() for stream in self.streams],
            "current_stream_id": self.current_stream_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamIndex:
        return cls(
            streams=[StreamMetadata.from_dict(stream) for stream in data.get("streams", [])],
            current_stream_id=data.get("current_stream_id"),
        )

    def save(self, path: Path) -> None:
        with open(path, "w") as file_handle:
            json.dump(self.to_dict(), file_handle, indent=2)

    @classmethod
    def load(cls, path: Path) -> StreamIndex:
        if not path.exists():
            return cls()
        with open(path) as file_handle:
            data = json.load(file_handle)
        return cls.from_dict(data)


__all__ = [
    "StreamId",
    "StreamIndex",
    "StreamMetadata",
]
