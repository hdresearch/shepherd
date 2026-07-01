"""Reference helpers for kernel identities."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def canonical_json(value: Any) -> str:
    """Canonical JSON encoding shared by content addressing and the wire contract.

    Sorted keys, no insignificant whitespace, finite numbers only, UTF-8 with
    non-ASCII preserved. Per 260521-0600-kernel.md §"Canonical Encoding Rules".
    Input must be JSON-compatible; use the `wire.*_to_wire(...)` serializers to
    convert dataclasses before calling this.
    """
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )


def content_ref(kind: str, payload: Any) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{kind}:sha256:{digest}"


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"non-finite float is not content-addressable: {value!r}")
        return value
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"content-addressed mappings require string keys, got {type(key).__name__}")
        return {key: _normalize(item) for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))}
    raise TypeError(f"value of type {type(value).__name__} is not content-addressable; use JSON-compatible values")
