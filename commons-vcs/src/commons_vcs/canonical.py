r"""commons.canonical.v1 — byte-exact content addressing.

Reference implementation of the canonical encoding profile every cluster
member uses to compute content-addressed object identities. The normative
spec is `../encoding-spec.md`; this module is its executable form for the
kernel.

Encoding rules (summary):
- UTF-8 JSON, sorted keys (lexicographic by code point), compact separators
- ensure_ascii=True (\\uXXXX escapes for non-ASCII), lowercase hex
- Floats forbidden; encode fractional precision as strings
- Duplicate object keys forbidden (Python dicts can't represent them)
- Version tag prefix `commons.canonical.v1\\n` is mandatory
- Hash function is SHA-256; digests formatted as `sha256:<lowercase-hex>`

The full spec covers cross-language conformance notes (JS, Go, Rust, JVM),
determinism probes, and test vectors. See the encoding-spec markdown.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass
from typing import Any

CANONICAL_PREFIX = b"commons.canonical.v1\n"


def _validate_json_primitive(value: Any, *, path: str = "$") -> None:
    """Reject values outside the commons.canonical.v1 input boundary."""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        raise TypeError(
            f"floats are forbidden in canonical inputs at {path}; "
            "encode as strings or use schema-declared integer pairs"
        )
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical object keys must be strings at {path}")
            _validate_json_primitive(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_primitive(child, path=f"{path}[{index}]")
        return
    if isinstance(value, tuple):
        raise TypeError(f"tuples are not canonical JSON inputs at {path}; use lists")
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"bytes are not canonical JSON inputs at {path}; use a schema-declared encoding")
    if isinstance(value, (set, frozenset)):
        raise TypeError(f"sets are not canonical JSON inputs at {path}; use a sorted list")
    if is_dataclass(value) and not isinstance(value, type):
        raise TypeError(f"dataclasses are not canonical JSON inputs at {path}; project to primitives first")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview)):
        raise TypeError(f"{type(value).__name__} is not a canonical JSON array at {path}; use list")
    raise TypeError(f"{type(value).__name__} is not a canonical JSON input at {path}")


def canonical_bytes(value: Any) -> bytes:
    """Compute the canonical encoding of a value.

    Returns the prefix-tagged UTF-8 JSON bytes whose hash is the object's
    identity under this profile.

    Raises TypeError or ValueError if the value is outside the JSON-primitives
    input boundary. Profiles and authoring layers must project Python-specific
    values before calling into the canonicalizer.
    """
    _validate_json_primitive(value)
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return CANONICAL_PREFIX + body


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate canonical object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-canonical JSON constant {value!r}")


def canonical_value_from_bytes(payload: bytes) -> Any:
    """Decode strict ``commons.canonical.v1`` bytes back to JSON primitives.

    The decoder is intentionally stricter than ``json.loads`` alone: the
    version prefix is mandatory, duplicate object keys are rejected, floats and
    non-finite JSON constants are rejected, and the input must already be in the
    exact byte form produced by :func:`canonical_bytes`.
    """
    if not isinstance(payload, bytes):
        raise TypeError("canonical payload must be bytes")
    if not payload.startswith(CANONICAL_PREFIX):
        raise ValueError("canonical payload does not begin with commons.canonical.v1 prefix")
    body = payload[len(CANONICAL_PREFIX) :]
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError("canonical payload body is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("canonical payload body is not valid JSON") from exc
    _validate_json_primitive(value)
    if canonical_bytes(value) != payload:
        raise ValueError("canonical payload is not in canonical byte form")
    return value


def digest(value: Any) -> str:
    """Compute the cluster-canonical digest of a value.

    Returns a string of the form 'sha256:<lowercase-hex>'.
    """
    return f"sha256:{hashlib.sha256(canonical_bytes(value)).hexdigest()}"
