"""Runtime-owned cache policy and mode enums."""

from __future__ import annotations

from enum import Enum


class CachePolicy(str, Enum):
    """Controls what is included in the execution key (cache key)."""

    STRICT = "strict"
    RELAXED = "relaxed"
    INPUTS_ONLY = "inputs_only"
    DISABLED = "disabled"


class CacheMode(str, Enum):
    """Controls what is cached and restored on cache hit."""

    OUTPUTS_ONLY = "outputs_only"
    FULL = "full"


class HashingScope(str, Enum):
    """Controls depth of context state hashing."""

    FULL = "full"
    TRACKED_ONLY = "tracked_only"


__all__ = [
    "CacheMode",
    "CachePolicy",
    "HashingScope",
]
