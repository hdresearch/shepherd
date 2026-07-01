"""Public runtime step mock helpers."""

from __future__ import annotations

from typing import Any

from shepherd_core.output import generate_mock_value as _generate_output_mock_value
from shepherd_core.output import mock_execute_from_schema as _mock_execute_output_schema


def generate_mock_value(schema: dict[str, Any], field_name: str) -> Any:
    """Generate a mock value for a step schema field."""
    return _generate_output_mock_value(schema, field_name)


def mock_execute_from_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    """Generate mocked step output from a step schema."""
    return _mock_execute_output_schema(output_schema)


__all__ = [
    "generate_mock_value",
    "mock_execute_from_schema",
]
