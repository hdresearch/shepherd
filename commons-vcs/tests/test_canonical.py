from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from commons_vcs import CANONICAL_PREFIX, canonical_bytes, canonical_value_from_bytes, digest


@dataclass(frozen=True)
class _Record:
    value: str


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        {"value": 1.0},
        ("a", "b"),
        {"items": ("a", "b")},
        _Record("x"),
        {"record": _Record("x")},
        b"bytes",
        bytearray(b"bytes"),
        {"values": {1, 2}},
        {1: "non-string key"},
    ],
)
def test_canonical_boundary_rejects_non_json_native_values(value: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_bytes(value)


def test_canonical_vectors_are_stable() -> None:
    vector_path = Path(__file__).parent / "vectors" / "commons_canonical_v1.json"
    vectors = json.loads(vector_path.read_text(encoding="utf-8"))

    for vector in vectors:
        value = vector["input"]
        assert canonical_bytes(value).decode("utf-8") == vector["canonical_text"], vector["name"]
        assert digest(value) == vector["digest"], vector["name"]
        assert canonical_value_from_bytes(vector["canonical_text"].encode("utf-8")) == value, vector["name"]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b'{"a":1}', "prefix"),
        (CANONICAL_PREFIX + b'{"a":1}\n', "canonical byte form"),
        (CANONICAL_PREFIX + b'{"b":2,"a":1}', "canonical byte form"),
        (CANONICAL_PREFIX + b'{"a":1,"a":2}', "duplicate"),
        (CANONICAL_PREFIX + b'{"value":1.25}', "floats are forbidden"),
        (CANONICAL_PREFIX + b'{"value":NaN}', "non-canonical JSON constant"),
        (CANONICAL_PREFIX + b"\xff", "UTF-8"),
    ],
)
def test_canonical_value_from_bytes_rejects_non_canonical_payloads(
    payload: bytes,
    match: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        canonical_value_from_bytes(payload)


def test_canonical_value_from_bytes_rejects_non_bytes() -> None:
    with pytest.raises(TypeError, match="bytes"):
        canonical_value_from_bytes("commons.canonical.v1\nnull")  # type: ignore[arg-type]
