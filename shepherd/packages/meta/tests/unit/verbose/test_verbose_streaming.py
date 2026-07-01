"""Tests for VerboseFormatter streaming delta handlers."""

import io

from shepherd_providers import VerboseConfig, VerboseFormatter


class TestVerboseFormatterStreaming:
    """Test VerboseFormatter streaming delta handlers."""

    def test_thinking_delta_output(self, formatter: VerboseFormatter, output_stream: io.StringIO):
        """Thinking delta should produce output."""
        formatter.on_thinking_delta("Let me analyze this...", block_index=0)
        formatter.finalize()
        assert "Let me analyze this..." in output_stream.getvalue()

    def test_thinking_delta_multiple_chunks(self, formatter: VerboseFormatter, output_stream: io.StringIO):
        """Multiple thinking deltas should concatenate."""
        formatter.on_thinking_delta("First ", block_index=0)
        formatter.on_thinking_delta("second ", block_index=0)
        formatter.on_thinking_delta("third.", block_index=0)
        formatter.finalize()
        assert "First second third." in output_stream.getvalue()

    def test_thinking_delta_disabled(self, output_stream: io.StringIO):
        """Thinking delta should be silent when show_thinking=False."""
        config = VerboseConfig(
            enabled=True,
            output=output_stream,
            show_thinking=False,
        )
        formatter = VerboseFormatter(config)
        formatter.on_thinking_delta("Should not appear", block_index=0)
        formatter.finalize()
        assert output_stream.getvalue() == ""

    def test_thinking_delta_hidden_style(self, output_stream: io.StringIO):
        """Thinking delta should be silent when thinking_style=hidden."""
        config = VerboseConfig(
            enabled=True,
            output=output_stream,
            thinking_style="hidden",
        )
        formatter = VerboseFormatter(config)
        formatter.on_thinking_delta("Should not appear", block_index=0)
        formatter.finalize()
        assert output_stream.getvalue() == ""

    def test_text_delta_output(self, formatter: VerboseFormatter, output_stream: io.StringIO):
        """Text delta should produce output."""
        formatter.on_text_delta("Hello, world!", block_index=0)
        formatter.finalize()
        assert "Hello, world!" in output_stream.getvalue()

    def test_text_delta_multiple_chunks(self, formatter: VerboseFormatter, output_stream: io.StringIO):
        """Multiple text deltas should concatenate."""
        formatter.on_text_delta("Hello, ", block_index=0)
        formatter.on_text_delta("world!", block_index=0)
        formatter.finalize()
        assert "Hello, world!" in output_stream.getvalue()

    def test_text_delta_disabled(self, output_stream: io.StringIO):
        """Text delta should be silent when show_text=False."""
        config = VerboseConfig(
            enabled=True,
            output=output_stream,
            show_text=False,
        )
        formatter = VerboseFormatter(config)
        formatter.on_text_delta("Should not appear", block_index=0)
        formatter.finalize()
        assert output_stream.getvalue() == ""

    def test_tool_input_delta_disabled_by_default(self, formatter: VerboseFormatter, output_stream: io.StringIO):
        """Tool input delta should be silent by default."""
        formatter.on_tool_input_delta('{"path": "test.py"}', block_index=0)
        formatter.finalize()
        assert output_stream.getvalue() == ""

    def test_tool_input_delta_enabled(self, output_stream: io.StringIO):
        """Tool input delta should produce output when enabled."""
        config = VerboseConfig(
            enabled=True,
            output=output_stream,
            show_tool_input_streaming=True,
            use_color=False,
        )
        formatter = VerboseFormatter(config)
        formatter.on_tool_input_delta('{"path": "test.py"}', block_index=0)
        formatter.finalize()
        assert '{"path": "test.py"}' in output_stream.getvalue()

    def test_block_transitions(self, formatter: VerboseFormatter, output_stream: io.StringIO):
        """Block transitions should handle newlines correctly."""
        formatter.on_block_start("thinking", 0)
        formatter.on_thinking_delta("Thinking...", block_index=0)
        formatter.on_block_stop(0)

        formatter.on_block_start("text", 1)
        formatter.on_text_delta("Response text", block_index=1)
        formatter.on_block_stop(1)

        formatter.finalize()

        output = output_stream.getvalue()
        assert "Thinking..." in output
        assert "Response text" in output
        # Each block should end with newline
        lines = output.strip().split("\n")
        assert len(lines) >= 2

    def test_multiple_concurrent_blocks(self, formatter: VerboseFormatter, output_stream: io.StringIO):
        """Formatter should handle multiple blocks with different indices."""
        # Start thinking block at index 0
        formatter.on_block_start("thinking", 0)
        formatter.on_thinking_delta("Thinking ", block_index=0)

        # Start text block at index 1 (concurrent)
        formatter.on_block_start("text", 1)

        # Continue thinking
        formatter.on_thinking_delta("more...", block_index=0)
        formatter.on_block_stop(0)

        # Now text
        formatter.on_text_delta("Text output", block_index=1)
        formatter.on_block_stop(1)

        formatter.finalize()

        output = output_stream.getvalue()
        assert "Thinking more..." in output
        assert "Text output" in output
