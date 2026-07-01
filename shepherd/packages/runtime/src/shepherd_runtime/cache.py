"""Public runtime cache owner paths."""

from __future__ import annotations

from typing import Literal

from shepherd_core.effects import Effect

from ._cache_key import ExecutionKey
from ._cache_policy import CacheMode, CachePolicy, HashingScope
from ._cache_store import CachedOutputs, CacheEntry, CacheIndex, CacheStats, CacheStore


class CacheHit(Effect):
    """Effect emitted when a cached result is reused instead of executing."""

    effect_type: Literal["cache_hit"] = "cache_hit"
    execution_key: str = ""
    cache_mode: str = "outputs_only"
    created_at: str = ""
    age_seconds: float = 0.0


class CacheMiss(Effect):
    """Effect emitted when cache lookup does not find a usable entry."""

    effect_type: Literal["cache_miss"] = "cache_miss"
    execution_key: str = ""
    reason: str = "not_found"


class CacheStored(Effect):
    """Effect emitted after a task result is persisted into the cache."""

    effect_type: Literal["cache_stored"] = "cache_stored"
    execution_key: str = ""
    cache_mode: str = "outputs_only"
    size_bytes: int = 0


_RUNTIME_OWNED = (
    CacheEntry,
    CacheIndex,
    CacheMode,
    CachePolicy,
    CacheStats,
    CacheStore,
    CachedOutputs,
    ExecutionKey,
    HashingScope,
)
for _symbol in _RUNTIME_OWNED:
    _symbol.__module__ = __name__


def get_effect_types() -> dict[str, type[Effect]]:
    """Expose runtime-owned cache effects for explicit registry composition."""
    return {
        "cache_hit": CacheHit,
        "cache_miss": CacheMiss,
        "cache_stored": CacheStored,
    }


__all__ = [
    "CacheEntry",
    "CacheHit",
    "CacheIndex",
    "CacheMiss",
    "CacheMode",
    "CachePolicy",
    "CacheStats",
    "CacheStore",
    "CacheStored",
    "CachedOutputs",
    "ExecutionKey",
    "HashingScope",
    "get_effect_types",
]
