"""ViewModel round-trip and schema-version handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shepherd_trace_viewer.durable_reader import read_trace_payload_file
from shepherd_trace_viewer.model import SCHEMA_VERSION
from shepherd_trace_viewer.serde import SchemaVersionError, from_json, to_json

FIXTURE = Path(__file__).parent / "fixtures" / "durable-basic.trace.json"


def test_round_trip_equality() -> None:
    tv = read_trace_payload_file(FIXTURE)
    assert from_json(to_json(tv)) == tv


def test_to_json_is_json_serializable() -> None:
    json.dumps(to_json(read_trace_payload_file(FIXTURE)))


def test_unknown_schema_version_rejected() -> None:
    payload = to_json(read_trace_payload_file(FIXTURE))
    payload["schema_version"] = "shepherd.trace-view.v99"
    with pytest.raises(SchemaVersionError):
        from_json(payload)


def test_schema_version_constant() -> None:
    assert to_json(read_trace_payload_file(FIXTURE))["schema_version"] == SCHEMA_VERSION
