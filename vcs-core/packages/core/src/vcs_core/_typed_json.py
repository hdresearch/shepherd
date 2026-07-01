"""Private typed-JSON helpers for vcs-core transport boundaries."""

from __future__ import annotations

import base64
import binascii
import math


def encode_typed_json(value: object) -> object:
    """Convert supported Python values into JSON-safe transport values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("JSON transport values must not contain NaN or infinity.")
        return value
    if isinstance(value, bytes):
        return {
            "__type__": "bytes",
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, list):
        return [encode_typed_json(item) for item in value]
    if isinstance(value, tuple):
        return [encode_typed_json(item) for item in value]
    if isinstance(value, dict):
        encoded: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON transport object keys must be strings.")
            encoded[key] = encode_typed_json(item)
        return encoded
    raise TypeError(f"Unsupported JSON transport value type: {type(value).__name__}.")


def decode_typed_json(value: object) -> object:
    """Decode private typed-JSON transport values back into Python values."""
    if isinstance(value, list):
        return [decode_typed_json(item) for item in value]
    if isinstance(value, dict):
        if value.get("__type__") == "bytes":
            return _decode_bytes(value)
        return {key: decode_typed_json(item) for key, item in value.items()}
    return value


def _decode_bytes(value: dict[object, object]) -> bytes:
    encoding = value.get("encoding")
    data = value.get("data")
    if encoding != "base64" or not isinstance(data, str):
        raise TypeError("Invalid typed JSON bytes payload.")
    try:
        return base64.b64decode(data, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 data in typed JSON bytes payload.") from exc
