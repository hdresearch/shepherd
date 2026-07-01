"""The committed schema artifact stays in sync and validates durable fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from shepherd_trace_viewer.durable_reader import read_trace_payload_file
from shepherd_trace_viewer.schema import SCHEMA_PATH, build_json_schema
from shepherd_trace_viewer.serde import to_json

FIXTURES = Path(__file__).parent / "fixtures"


def test_committed_schema_matches_builder() -> None:
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == build_json_schema(), "run `python -m shepherd_trace_viewer.schema`"


@pytest.mark.parametrize("payload", sorted(FIXTURES.glob("durable-*.trace.json")))
def test_durable_payload_projects_to_valid_view(payload: Path) -> None:
    view = read_trace_payload_file(payload)
    jsonschema.validate(to_json(view), build_json_schema())
