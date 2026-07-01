"""Tests for runtime-owned cache and persistence entrypoints."""

from __future__ import annotations

from shepherd_runtime.cache import CachedOutputs, CacheHit, CachePolicy, CacheStore, ExecutionKey
from shepherd_runtime.persistence import (
    PersistenceConfig,
    PersistenceManager,
    ProjectId,
    ProjectMetadata,
    StreamId,
    StreamIndex,
    StreamMetadata,
    StreamReader,
    StreamWriter,
)


def test_runtime_cache_owner_path_exposes_runtime_symbols() -> None:
    assert CacheHit.__module__ == "shepherd_runtime.cache"
    assert CachePolicy.__module__ == "shepherd_runtime.cache"
    assert CacheStore.__module__ == "shepherd_runtime.cache"
    assert CachedOutputs.__module__ == "shepherd_runtime.cache"
    assert ExecutionKey.__module__ == "shepherd_runtime.cache"


def test_runtime_persistence_owner_path_exposes_runtime_symbols() -> None:
    assert PersistenceConfig.__module__ == "shepherd_runtime.persistence"
    assert PersistenceManager.__module__ == "shepherd_runtime.persistence"
    assert ProjectId.__module__ == "shepherd_runtime.persistence"
    assert ProjectMetadata.__module__ == "shepherd_runtime.persistence"
    assert StreamId.__module__ == "shepherd_runtime.persistence"
    assert StreamIndex.__module__ == "shepherd_runtime.persistence"
    assert StreamMetadata.__module__ == "shepherd_runtime.persistence"
    assert StreamReader.__module__ == "shepherd_runtime.persistence"
    assert StreamWriter.__module__ == "shepherd_runtime.persistence"
