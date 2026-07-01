"""Structured-output and serialization tests for OpenAIProvider."""

from __future__ import annotations

from shepherd_providers.openai.provider import OpenAIProvider, _sanitize_schema_name, _try_parse_json


class TestStructuredOutputParsing:
    """Verify structured output JSON parsing with fallback."""

    def test_parse_valid_json(self):
        result = _try_parse_json('{"summary": "hello", "confidence": 0.9}')
        assert result == {"summary": "hello", "confidence": 0.9}

    def test_parse_markdown_fenced_json(self):
        text = '```json\n{"summary": "hello"}\n```'
        result = _try_parse_json(text)
        assert result == {"summary": "hello"}

    def test_parse_invalid_json_returns_empty(self):
        result = _try_parse_json("this is not json")
        assert result == {}

    def test_parse_empty_string_returns_empty(self):
        result = _try_parse_json("")
        assert result == {}


class TestSchemaNameSanitization:
    """Verify the text.format name field sanitization."""

    def test_simple_name(self):
        assert _sanitize_schema_name("my_task") == "my_task"

    def test_special_characters_replaced(self):
        assert _sanitize_schema_name("ctx:main/task") == "ctx_main_task"

    def test_none_returns_default(self):
        assert _sanitize_schema_name(None) == "task_output"

    def test_empty_returns_default(self):
        assert _sanitize_schema_name("") == "task_output"

    def test_long_name_truncated(self):
        long_name = "a" * 100
        assert len(_sanitize_schema_name(long_name)) <= 64


class TestSerializationRoundTrip:
    """Verify to_config/from_config round-trip."""

    def test_default_config_round_trip(self):
        p = OpenAIProvider(name="test", model="gpt-4o")
        config = p.to_config()
        p2 = OpenAIProvider.from_config(config)
        assert p2.name == "test"
        assert p2.model == "gpt-4o"
        assert p2.max_turns == 30

    def test_custom_config_round_trip(self):
        p = OpenAIProvider(name="prod", model="gpt-5.3-codex", max_turns=10, base_url="https://custom.api")
        config = p.to_config()
        p2 = OpenAIProvider.from_config(config)
        assert p2.model == "gpt-5.3-codex"
        assert p2.max_turns == 10
        assert p2.base_url == "https://custom.api"

    def test_minimal_config_uses_defaults(self):
        p = OpenAIProvider.from_config({"provider_type": "openai"})
        assert p.name == "container"
        assert p.model == "gpt-4o"
        assert p.max_turns == 30
        assert p.verbose is None

    def test_verbose_excluded_from_config(self):
        from shepherd_providers.verbose import VerboseConfig

        p = OpenAIProvider(name="test", verbose=VerboseConfig(enabled=True))
        config = p.to_config()
        assert "verbose" not in config
        p2 = OpenAIProvider.from_config(config)
        assert p2.verbose is None

    def test_max_turns_default_omitted(self):
        p = OpenAIProvider(name="test", max_turns=30)
        config = p.to_config()
        assert "max_turns" not in config

    def test_provider_type_is_openai(self):
        p = OpenAIProvider(name="test")
        config = p.to_config()
        assert config["provider_type"] == "openai"


class TestProviderCapabilities:
    """Verify capability declarations match implementation state."""

    def test_supports_fork_session(self):
        p = OpenAIProvider(name="test")
        assert p.capabilities.supports_fork_session is True

    def test_streaming_enabled(self):
        p = OpenAIProvider(name="test")
        assert p.capabilities.supports_streaming is True

    def test_supports_structured_output(self):
        p = OpenAIProvider(name="test")
        assert p.capabilities.supports_structured_output is True
