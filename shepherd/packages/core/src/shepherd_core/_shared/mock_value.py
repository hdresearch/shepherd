"""Mock value generation utilities.

This module handles generating mock values for types and JSON schemas,
used when running in mock mode for testing.

This is a shared module used by step.py, _output_handler.py, and _execution.py.
"""

from __future__ import annotations

from enum import Enum
from typing import (
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
)

from pydantic import BaseModel

from .schema import SINGLE_OUTPUT_KEY

# =============================================================================
# Mock Value Generation
# =============================================================================


def _generate_mock_value(typ_or_schema: type | dict[str, Any] | None, field_name: str = "") -> Any:
    """Generate a mock value for a given type or JSON schema.

    Args:
        typ_or_schema: Either a Python type or a JSON schema dict
        field_name: Optional field name for more specific mock values

    Returns:
        A mock value appropriate for the type/schema
    """
    # Handle JSON schema dict format
    if isinstance(typ_or_schema, dict):
        return _generate_mock_from_schema(typ_or_schema, field_name)

    typ = typ_or_schema

    if typ is None:
        return "[mock: no type]"

    origin = get_origin(typ)
    args = get_args(typ)

    # Handle Literal types
    if origin is Literal:
        return args[0] if args else None

    # Handle Union types (including Optional)
    if origin is Union:
        # Filter out NoneType and use first non-None type
        non_none_args = [a for a in args if a is not type(None)]
        if non_none_args:
            return _generate_mock_value(non_none_args[0], field_name)
        return None

    # Handle list types
    if origin is list:
        return []  # Return empty list for mock

    # Handle dict types
    if origin is dict:
        return {}

    # Handle tuple types
    if origin is tuple:
        if args:
            return tuple(_generate_mock_value(a, f"tuple_{i}") for i, a in enumerate(args) if a is not ...)
        return ()

    # Handle Enum types
    if isinstance(typ, type) and issubclass(typ, Enum):
        members = list(typ)
        return members[0] if members else None

    # Handle Pydantic models
    if isinstance(typ, type) and issubclass(typ, BaseModel):
        # Generate mock values for all required fields
        field_values = {}
        for fname, field_info in typ.model_fields.items():
            if field_info.is_required():
                field_values[fname] = _generate_mock_value(field_info.annotation, fname)
            elif field_info.default is not None:
                field_values[fname] = field_info.default
        return typ(**field_values)

    # Primitive types
    if typ is str:
        return "[mock: string]"
    if typ is int:
        return 0
    if typ is float:
        return 0.0
    if typ is bool:
        return True
    if typ is bytes:
        return b""

    return f"[mock: {typ.__name__ if hasattr(typ, '__name__') else str(typ)}]"


def _generate_mock_from_schema(schema: dict[str, Any], field_name: str = "") -> Any:
    """Generate a mock value from a JSON schema dict.

    Args:
        schema: JSON schema describing the expected type
        field_name: Field name for more specific mock values
    """
    schema_type = schema.get("type", "string")

    if schema_type == "string":
        if "enum" in schema:
            # Return first enum value
            return schema["enum"][0]
        return f"[mock {field_name}]" if field_name else "[mock: string]"
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        return []
    if schema_type == "object":
        return {}
    if schema_type == "null":
        return None

    return None


def _mock_execute_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate mock output from a JSON schema.

    Returns a dict with the "result" key containing the mock value.
    """
    # Handle wrapped schema format (from _return_type_to_output_schema)
    if "schema" in schema and "properties" in schema.get("schema", {}):
        inner_schema = schema["schema"]
        properties = inner_schema.get("properties", {})
        result_schema = properties.get(SINGLE_OUTPUT_KEY, {"type": "string"})
        return {SINGLE_OUTPUT_KEY: _generate_mock_from_schema(result_schema, SINGLE_OUTPUT_KEY)}

    # For object type with properties, generate all properties
    if schema.get("type") == "object" and "properties" in schema:
        result = {}
        for prop_name, prop_schema in schema["properties"].items():
            result[prop_name] = _generate_mock_from_schema(prop_schema, prop_name)
        return {SINGLE_OUTPUT_KEY: result}

    # All other types: delegate to _generate_mock_from_schema
    return {SINGLE_OUTPUT_KEY: _generate_mock_from_schema(schema, SINGLE_OUTPUT_KEY)}


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "_generate_mock_from_schema",
    "_generate_mock_value",
    "_mock_execute_from_schema",
]
