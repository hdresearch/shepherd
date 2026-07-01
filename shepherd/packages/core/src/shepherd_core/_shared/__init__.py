"""Shared internal utilities for shepherd-core.

This package contains utilities shared across multiple subpackages:
- schema: JSON Schema generation utilities
- coerce: Value coercion for type conversion
- mock_value: Mock value generation (internal, for MockProvider)
"""

from __future__ import annotations

from .coerce import (
    _coerce_step_value,
    _coerce_to_bool,
    _coerce_to_enum,
    _coerce_to_list,
    _parse_single_output,
    _parse_step_output,
    _parse_tuple_output,
)
from .mock_value import (
    _generate_mock_from_schema,
    _generate_mock_value,
    _mock_execute_from_schema,
)
from .schema import (
    SINGLE_OUTPUT_KEY,
    merge_schema_defs,
    python_type_to_json_schema,
    type_to_json_schema,
    wrap_as_json_schema,
)

__all__ = [
    "SINGLE_OUTPUT_KEY",
    "_coerce_step_value",
    "_coerce_to_bool",
    "_coerce_to_enum",
    "_coerce_to_list",
    "_generate_mock_from_schema",
    "_generate_mock_value",
    "_mock_execute_from_schema",
    "_parse_single_output",
    "_parse_step_output",
    "_parse_tuple_output",
    "merge_schema_defs",
    "python_type_to_json_schema",
    "type_to_json_schema",
    "wrap_as_json_schema",
]
