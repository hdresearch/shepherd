"""Runtime-owned stream writer and reader for JSONL effect persistence."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, BinaryIO

from typing_extensions import Self

from ._persistence_layers import backfill_sequence, layer_from_dict, layer_to_dict
from ._persistence_stream import StreamId, StreamMetadata
from .effects import compose_effect_registry

if TYPE_CHECKING:
    import types
    from collections.abc import Iterator
    from pathlib import Path

    from shepherd_core.effects import EffectTypeRegistry

    from ._persistence_layers import EffectLayer

logger = logging.getLogger(__name__)

try:
    from filelock import FileLock  # type: ignore[import-not-found,import-untyped,unused-ignore]
    from filelock import Timeout as FileLockTimeout

    HAS_FILELOCK = True
except ImportError:
    HAS_FILELOCK = False
    FileLock = None  # type: ignore[assignment,misc,unused-ignore]
    FileLockTimeout = None  # type: ignore[assignment,misc,unused-ignore]


@dataclass
class StreamWriter:
    """Append-only writer for effect streams."""

    stream_dir: Path
    stream_id: StreamId
    _file: BinaryIO | None = field(default=None, repr=False)
    _lock: Any = field(default=None, repr=False)
    _effect_count: int = field(default=0, repr=False)
    _closed: bool = field(default=False, repr=False)

    @property
    def stream_path(self) -> Path:
        return self.stream_dir / f"{self.stream_id.value}.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.stream_dir / f"{self.stream_id.value}.lock"

    @property
    def effect_count(self) -> int:
        return self._effect_count

    def open(self) -> StreamWriter:
        if self._file is not None:
            raise RuntimeError("Stream already open")

        self.stream_dir.mkdir(parents=True, exist_ok=True)

        if HAS_FILELOCK:
            self._lock = FileLock(self.lock_path)
            try:
                self._lock.acquire(timeout=10)
            except FileLockTimeout as error:
                raise TimeoutError(
                    f"Could not acquire lock for stream {self.stream_id} within 10 seconds. "
                    f"Another process may be writing to this stream."
                ) from error
        else:
            logger.warning(
                "filelock not installed. Stream writes are not protected from concurrent access. "
                "Install with: pip install filelock"
            )

        self._file = open(self.stream_path, "ab")  # noqa: SIM115
        self._closed = False

        if self.stream_path.stat().st_size > 0:
            with open(self.stream_path, "rb") as stream_file:
                self._effect_count = sum(1 for _ in stream_file)

        return self

    def append(self, layer: EffectLayer) -> int:
        if self._closed:
            raise RuntimeError("Stream is closed. Cannot append to a closed stream.")
        if self._file is None:
            raise RuntimeError("Stream not open. Call open() first.")

        layer_dict = layer_to_dict(layer)
        line = json.dumps(layer_dict, separators=(",", ":")).encode("utf-8") + b"\n"

        self._file.write(line)
        self._file.flush()
        os.fsync(self._file.fileno())

        index = self._effect_count
        self._effect_count += 1
        return index

    def close(self) -> StreamMetadata:
        if self._file is not None:
            self._file.close()
            self._file = None

        if self._lock is not None:
            self._lock.release()
            self._lock = None

        self._closed = True

        return StreamMetadata(
            stream_id=str(self.stream_id),
            effect_count=self._effect_count,
        ).with_closed(self._effect_count)

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.close()


@dataclass
class StreamReader:
    """Reader for effect streams."""

    stream_path: Path
    registry: EffectTypeRegistry | None = None

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = compose_effect_registry()

    def read_all(self) -> list[EffectLayer]:
        return list(self.iter_layers())

    def iter_layers(self) -> Iterator[EffectLayer]:
        if not self.stream_path.exists():
            return

        with open(self.stream_path, encoding="utf-8") as file_handle:
            for line_num, line in enumerate(file_handle, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    layer = layer_from_dict(data, registry=self.registry)
                    if "sequence" not in data:
                        layer = backfill_sequence(layer, line_num - 1)
                    yield layer
                except json.JSONDecodeError as error:
                    logger.warning(
                        "Skipping corrupted JSON on line %d of %s: %s",
                        line_num,
                        self.stream_path,
                        error,
                    )
                    continue
                except KeyError as error:
                    logger.warning(
                        "Skipping malformed layer on line %d of %s: missing %s",
                        line_num,
                        self.stream_path,
                        error,
                    )
                    continue
                except Exception as error:  # noqa: BLE001
                    logger.warning(
                        "Skipping unreadable line %d of %s: %s",
                        line_num,
                        self.stream_path,
                        error,
                    )

    def count_effects(self) -> int:
        if not self.stream_path.exists():
            return 0

        count = 0
        with open(self.stream_path, encoding="utf-8") as file_handle:
            for line in file_handle:
                if line.strip():
                    count += 1
        return count


__all__ = [
    "StreamReader",
    "StreamWriter",
    "layer_from_dict",
    "layer_to_dict",
]
