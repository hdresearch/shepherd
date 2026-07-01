"""Tests for kernel-level JSON schema generation."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from shepherd_core import type_to_json_schema

# =============================================================================
# Type coverage (M1 fixes)
# =============================================================================


class TestTypeCoverage:
    """Tests for types that previously fell back to string."""

    def test_datetime_produces_format(self):
        """Datetime should produce format: date-time."""
        schema = type_to_json_schema(datetime)
        assert schema.get("type") == "string"
        assert schema.get("format") == "date-time"

    def test_uuid_produces_format(self):
        """UUID should produce format: uuid."""
        schema = type_to_json_schema(UUID)
        assert schema.get("type") == "string"
        assert schema.get("format") == "uuid"

    def test_set_produces_unique_items(self):
        """set[str] should produce array with uniqueItems.

        Note: LLM providers may not enforce uniqueItems — it's a hint.
        """
        schema = type_to_json_schema(set[str])
        assert schema.get("type") == "array"
        assert schema.get("uniqueItems") is True

    def test_tuple_produces_prefix_items(self):
        """tuple[str, int] should produce prefixItems schema."""
        schema = type_to_json_schema(tuple[str, int])
        assert schema.get("type") == "array"
        assert "prefixItems" in schema
        assert len(schema["prefixItems"]) == 2
        assert schema["prefixItems"][0].get("type") == "string"
        assert schema["prefixItems"][1].get("type") == "integer"

    def test_bytes_produces_encoding_hint(self):
        """Bytes should produce string schema with encoding hint.

        Note: Pydantic's representation varies by version:
        - Pydantic 2.0-2.5: {"type": "string", "format": "binary"}
        - Pydantic 2.6+: {"type": "string", "contentEncoding": "base64"}
        """
        schema = type_to_json_schema(bytes)
        assert schema.get("type") == "string"
        has_encoding_hint = "format" in schema or "contentEncoding" in schema
        assert has_encoding_hint, "bytes should have format or contentEncoding"


# =============================================================================
# Edge cases and fallback behavior
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and fallback behavior."""

    def test_unsupported_type_falls_back_with_warning(self):
        """Unsupported types should fall back to string with a warning."""

        class WeirdType:
            pass

        with pytest.warns(UserWarning, match="Cannot generate JSON schema"):
            schema = type_to_json_schema(WeirdType)

        assert schema == {"type": "string"}

    def test_none_type_produces_null_schema(self):
        """None/NoneType should produce null schema."""
        assert type_to_json_schema(None) == {"type": "null"}
        assert type_to_json_schema(type(None)) == {"type": "null"}

    def test_any_type_produces_empty_schema(self):
        """Any type should produce empty schema (no constraints)."""
        assert type_to_json_schema(Any) == {}

    def test_annotated_type_unwrapped(self):
        """Annotated types should be handled correctly."""
        schema = type_to_json_schema(Annotated[int, "some metadata"])
        assert schema.get("type") == "integer"

        schema = type_to_json_schema(Annotated[str, "validator hint"])
        assert schema.get("type") == "string"

        schema = type_to_json_schema(Annotated[list[int], "list of ids"])
        assert schema.get("type") == "array"

    def test_optional_type_produces_anyof_with_null(self):
        """Optional[T] should produce anyOf with null (TypeAdapter behavior)."""
        schema = type_to_json_schema(str | None)
        # TypeAdapter produces anyOf for Optional
        assert "anyOf" in schema
        null_types = [s for s in schema["anyOf"] if s.get("type") == "null"]
        assert len(null_types) == 1, "Should have exactly one null type in anyOf"

    def test_literal_with_mixed_types(self):
        """Literal with mixed types should produce enum constraint."""
        schema = type_to_json_schema(Literal["a", 1, True])
        # TypeAdapter handles mixed literals
        assert "enum" in schema or "anyOf" in schema
        if "enum" in schema:
            assert set(schema["enum"]) == {"a", 1}
