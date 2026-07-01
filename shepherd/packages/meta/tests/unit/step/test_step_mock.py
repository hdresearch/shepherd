"""Tests for step mock value generation functionality."""

from shepherd_runtime.step.mock import (
    generate_mock_value,
    mock_execute_from_schema,
)


class TestMockValueGeneration:
    """Test mock value generation functions."""

    def test_generate_mock_value_string(self):
        """generate_mock_value generates correct string mock."""
        schema = {"type": "string"}
        value = generate_mock_value(schema, "test_field")
        assert value == "[mock test_field]"

    def test_generate_mock_value_integer(self):
        """generate_mock_value generates correct integer mock."""
        schema = {"type": "integer"}
        value = generate_mock_value(schema, "count")
        assert value == 0

    def test_generate_mock_value_boolean(self):
        """generate_mock_value generates correct boolean mock."""
        schema = {"type": "boolean"}
        value = generate_mock_value(schema, "flag")
        assert value is True

    def test_generate_mock_value_enum(self):
        """generate_mock_value returns first enum value."""
        schema = {"type": "string", "enum": ["alpha", "beta", "gamma"]}
        value = generate_mock_value(schema, "choice")
        assert value == "alpha"

    def test_generate_mock_value_array(self):
        """generate_mock_value generates empty array."""
        schema = {"type": "array"}
        value = generate_mock_value(schema, "items")
        assert value == []

    def test_generate_mock_value_object(self):
        """generate_mock_value generates empty object."""
        schema = {"type": "object"}
        value = generate_mock_value(schema, "data")
        assert value == {}

    def test_mock_execute_from_schema(self):
        """mock_execute_from_schema generates correct output dict."""
        output_schema = {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "enum": ["a", "b"]},
                },
            },
        }
        result = mock_execute_from_schema(output_schema)
        assert result == {"result": "a"}
