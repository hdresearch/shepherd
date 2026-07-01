"""Runtime-owned cache storage implementation."""

from __future__ import annotations

import contextlib
import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ._cache_policy import CacheMode

logger = logging.getLogger(__name__)


@dataclass
class CachedOutputs:
    """Stored outputs (and optionally effects) for a cached execution."""

    outputs: dict[str, Any]
    effects: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    task_name: str = ""
    execution_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputs": self.outputs,
            "effects": self.effects,
            "created_at": self.created_at,
            "task_name": self.task_name,
            "execution_key": self.execution_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedOutputs:
        return cls(
            outputs=data.get("outputs", {}),
            effects=data.get("effects", []),
            created_at=data.get("created_at", ""),
            task_name=data.get("task_name", ""),
            execution_key=data.get("execution_key", ""),
        )


@dataclass
class CacheEntry:
    """Metadata about a cached execution."""

    execution_key: str
    task_name: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    access_count: int = 0
    size_bytes: int = 0
    mode: str = "outputs_only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_key": self.execution_key,
            "task_name": self.task_name,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "size_bytes": self.size_bytes,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheEntry:
        return cls(
            execution_key=data.get("execution_key", ""),
            task_name=data.get("task_name", ""),
            created_at=data.get("created_at", ""),
            last_accessed_at=data.get("last_accessed_at", ""),
            access_count=data.get("access_count", 0),
            size_bytes=data.get("size_bytes", 0),
            mode=data.get("mode", "outputs_only"),
        )


@dataclass
class CacheIndex:
    """Index of all cached executions for a project."""

    entries: dict[str, CacheEntry] = field(default_factory=dict)
    total_size_bytes: int = 0
    last_cleanup_at: str = ""

    def add_entry(self, entry: CacheEntry) -> None:
        if entry.execution_key in self.entries:
            old_entry = self.entries[entry.execution_key]
            self.total_size_bytes -= old_entry.size_bytes
        self.entries[entry.execution_key] = entry
        self.total_size_bytes += entry.size_bytes

    def get_entry(self, execution_key: str) -> CacheEntry | None:
        return self.entries.get(execution_key)

    def remove_entry(self, execution_key: str) -> None:
        if execution_key in self.entries:
            entry = self.entries.pop(execution_key)
            self.total_size_bytes -= entry.size_bytes

    def save(self, path: Path) -> None:
        data = {
            "entries": {k: v.to_dict() for k, v in self.entries.items()},
            "total_size_bytes": self.total_size_bytes,
            "last_cleanup_at": self.last_cleanup_at,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with open(fd, "w") as f:
                f.write(content)
            Path(tmp_path).replace(path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise

    @classmethod
    def load(cls, path: Path) -> CacheIndex:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            entries = {k: CacheEntry.from_dict(v) for k, v in data.get("entries", {}).items()}
            return cls(
                entries=entries,
                total_size_bytes=data.get("total_size_bytes", 0),
                last_cleanup_at=data.get("last_cleanup_at", ""),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load cache index: %s", e)
            return cls()


@dataclass
class CacheStats:
    """Statistics about cache usage."""

    entry_count: int = 0
    total_size_mb: float = 0.0
    hit_count: int = 0
    miss_count: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hit_count + self.miss_count
        if total == 0:
            return 0.0
        return self.hit_count / total


@dataclass
class CacheStore:
    """Manages cache storage operations for a project."""

    cache_dir: Path
    _index: CacheIndex | None = field(default=None, repr=False)
    _hit_count: int = field(default=0, repr=False)
    _miss_count: int = field(default=0, repr=False)

    @property
    def index_path(self) -> Path:
        return self.cache_dir / "index.json"

    @property
    def entries_dir(self) -> Path:
        return self.cache_dir / "entries"

    def initialize(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.entries_dir.mkdir(exist_ok=True)
        self._index = CacheIndex.load(self.index_path)

    def get(self, execution_key: str) -> CachedOutputs | None:
        if self._index is None:
            self._miss_count += 1
            return None

        entry = self._index.get_entry(execution_key)
        if entry is None:
            self._miss_count += 1
            return None

        entry_path = self.entries_dir / f"{execution_key}.json"
        if not entry_path.exists():
            self._index.remove_entry(execution_key)
            self._save_index()
            self._miss_count += 1
            return None

        try:
            data = json.loads(entry_path.read_text())
            cached = CachedOutputs.from_dict(data)
            entry.access_count += 1
            entry.last_accessed_at = datetime.now(timezone.utc).isoformat()
            self._save_index()
            self._hit_count += 1
            logger.debug("Cache hit for %s (%s)", entry.task_name, execution_key)
            return cached
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load cached entry %s: %s", execution_key, e)
            self._miss_count += 1
            return None

    def put(
        self,
        execution_key: str,
        cached: CachedOutputs,
        mode: CacheMode = CacheMode.OUTPUTS_ONLY,
    ) -> None:
        if self._index is None:
            logger.warning("Cache not initialized, skipping put")
            return

        cached.execution_key = execution_key
        entry_path = self.entries_dir / f"{execution_key}.json"
        content = json.dumps(cached.to_dict(), indent=2)
        entry_path.write_text(content)

        entry = CacheEntry(
            execution_key=execution_key,
            task_name=cached.task_name,
            size_bytes=len(content.encode()),
            mode=mode.value,
        )
        self._index.add_entry(entry)
        self._save_index()
        logger.debug("Cached %s (%s, %d bytes)", cached.task_name, execution_key, entry.size_bytes)

    def invalidate(
        self,
        execution_key: str | None = None,
        task: type | None = None,
        older_than: timedelta | None = None,
    ) -> int:
        if self._index is None:
            return 0

        to_remove: list[str] = []

        if execution_key is not None:
            if execution_key in self._index.entries:
                to_remove.append(execution_key)
        elif task is not None:
            task_name = task.__name__
            for key, entry in self._index.entries.items():
                if entry.task_name == task_name:
                    to_remove.append(key)
        elif older_than is not None:
            cutoff = datetime.now(timezone.utc) - older_than
            for key, entry in self._index.entries.items():
                try:
                    created = datetime.fromisoformat(entry.created_at)
                    if created < cutoff:
                        to_remove.append(key)
                except ValueError:
                    pass
        else:
            to_remove = list(self._index.entries.keys())

        for key in to_remove:
            self._index.remove_entry(key)
            entry_path = self.entries_dir / f"{key}.json"
            if entry_path.exists():
                entry_path.unlink()

        if to_remove:
            self._save_index()

        return len(to_remove)

    def cleanup_expired(self, ttl_hours: int = 24) -> int:
        return self.invalidate(older_than=timedelta(hours=ttl_hours))

    def stats(self) -> CacheStats:
        entry_count = len(self._index.entries) if self._index else 0
        total_bytes = self._index.total_size_bytes if self._index else 0
        return CacheStats(
            entry_count=entry_count,
            total_size_mb=total_bytes / (1024 * 1024),
            hit_count=self._hit_count,
            miss_count=self._miss_count,
        )

    def _save_index(self) -> None:
        if self._index is not None:
            self._index.save(self.index_path)


__all__ = [
    "CacheEntry",
    "CacheIndex",
    "CacheStats",
    "CacheStore",
    "CachedOutputs",
]
