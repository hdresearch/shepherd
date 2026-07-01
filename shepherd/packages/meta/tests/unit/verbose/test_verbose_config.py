"""Tests for VerboseConfig model and factory methods."""

import io

from shepherd_providers import VerboseConfig


class TestVerboseConfig:
    """Test VerboseConfig model and factory methods."""

    def test_defaults(self):
        """VerboseConfig should have sensible defaults."""
        config = VerboseConfig()
        assert config.enabled is False
        assert config.stream_partial is True
        assert config.show_thinking is True
        assert config.show_text is True
        assert config.show_tool_calls is True
        assert config.show_tool_results is False
        assert config.show_tool_input_streaming is False
        assert config.show_prompts is False
        assert config.show_task_lifecycle is True
        assert config.show_context_info is False
        assert config.show_artifacts is True
        assert config.show_cost is True
        assert config.use_color is True
        assert config.use_emoji is True
        assert config.thinking_style == "dim"

    def test_enabled_config(self):
        """VerboseConfig should accept enabled=True."""
        config = VerboseConfig(enabled=True)
        assert config.enabled is True

    def test_custom_output_stream(self):
        """VerboseConfig should accept custom output stream."""
        stream = io.StringIO()
        config = VerboseConfig(output=stream)
        assert config.output is stream

    def test_thinking_style_options(self):
        """VerboseConfig should accept all thinking_style options."""
        for style in ["dim", "prefix", "hidden"]:
            config = VerboseConfig(thinking_style=style)
            assert config.thinking_style == style

    def test_from_env_defaults(self, monkeypatch):
        """from_env() should return disabled config by default."""
        # Clear any existing env vars
        monkeypatch.delenv("SHEPHERD_VERBOSE", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("SHEPHERD_NO_EMOJI", raising=False)

        config = VerboseConfig.from_env()
        assert config.enabled is False

    def test_from_env_verbose_enabled(self, monkeypatch):
        """from_env() should enable verbose when SHEPHERD_VERBOSE=1."""
        monkeypatch.setenv("SHEPHERD_VERBOSE", "1")
        config = VerboseConfig.from_env()
        assert config.enabled is True

    def test_from_env_verbose_true(self, monkeypatch):
        """from_env() should enable verbose when SHEPHERD_VERBOSE=true."""
        monkeypatch.setenv("SHEPHERD_VERBOSE", "true")
        config = VerboseConfig.from_env()
        assert config.enabled is True

    def test_from_env_verbose_yes(self, monkeypatch):
        """from_env() should enable verbose when SHEPHERD_VERBOSE=yes."""
        monkeypatch.setenv("SHEPHERD_VERBOSE", "yes")
        config = VerboseConfig.from_env()
        assert config.enabled is True

    def test_from_env_no_color(self, monkeypatch):
        """from_env() should disable colors when NO_COLOR is set."""
        monkeypatch.setenv("NO_COLOR", "1")
        config = VerboseConfig.from_env()
        assert config.use_color is False

    def test_from_env_no_emoji(self, monkeypatch):
        """from_env() should disable emoji when SHEPHERD_NO_EMOJI is set."""
        monkeypatch.setenv("SHEPHERD_NO_EMOJI", "1")
        config = VerboseConfig.from_env()
        assert config.use_emoji is False

    def test_from_env_stream_disabled(self, monkeypatch):
        """from_env() should disable streaming when SHEPHERD_STREAM=0."""
        monkeypatch.setenv("SHEPHERD_STREAM", "0")
        config = VerboseConfig.from_env()
        assert config.stream_partial is False
