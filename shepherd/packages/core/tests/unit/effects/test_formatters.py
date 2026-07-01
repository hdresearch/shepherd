"""Tests for effect stream formatters."""

import json

import pytest
from shepherd_core.effects import (
    AgentThinking,
    FilePatch,
    FileRead,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    ToolCallCompleted,
    ToolCallRejected,
    ToolCallStarted,
)
from shepherd_core.effects.formatters import (
    CompactFormatter,
    FormatterOptions,
    JSONFormatter,
    MarkdownFormatter,
    TreeFormatter,
)
from shepherd_core.scope.stream import Stream

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def empty_stream() -> Stream:
    """Empty stream for edge case testing."""
    return Stream()


@pytest.fixture
def simple_stream() -> Stream:
    """Stream with a simple successful task execution."""
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="FixBug"))
    stream = stream.append(
        ToolCallStarted(tool_name="read_file", tool_call_id="tc_001", params={"path": "src/auth.py"})
    )
    stream = stream.append(FileRead(path="src/auth.py"))
    stream = stream.append(ToolCallCompleted(tool_name="read_file", tool_call_id="tc_001", success=True))
    stream = stream.append(AgentThinking(content="I see the bug is on line 42. Let me fix it."))
    stream = stream.append(
        ToolCallStarted(tool_name="edit_file", tool_call_id="tc_002", params={"path": "src/auth.py"})
    )
    stream = stream.append(FilePatch(path="src/auth.py", old_content="old", new_content="new", caused_by="tc_002"))
    stream = stream.append(ToolCallCompleted(tool_name="edit_file", tool_call_id="tc_002", success=True))
    return stream.append(TaskCompleted(task_name="FixBug", duration_ms=1234.5))


@pytest.fixture
def failed_stream() -> Stream:
    """Stream with a failed task execution."""
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="FixBug"))
    stream = stream.append(
        ToolCallStarted(tool_name="read_file", tool_call_id="tc_001", params={"path": "src/missing.py"})
    )
    stream = stream.append(ToolCallRejected(tool_name="read_file", tool_call_id="tc_001", reason="File not found"))
    return stream.append(
        TaskFailed(
            task_name="FixBug",
            error="FileNotFoundError: File not found",
            error_type="FileNotFoundError",
            error_location="provider.py:123",
            suggestions=("Check the file path", "Verify file exists"),
        )
    )


# =============================================================================
# FormatterOptions Tests
# =============================================================================


class TestFormatterOptions:
    """Tests for FormatterOptions dataclass."""

    def test_default_values(self):
        """FormatterOptions has sensible defaults."""
        opts = FormatterOptions()

        assert opts.max_effects is None
        assert opts.include_types is None
        assert opts.exclude_types is None
        assert opts.include_metadata is False
        assert opts.include_timestamps is True
        assert opts.relative_timestamps is True
        assert opts.truncate_content == 200

    def test_custom_values(self):
        """FormatterOptions accepts custom values."""
        opts = FormatterOptions(
            max_effects=10,
            include_timestamps=False,
            truncate_content=50,
        )

        assert opts.max_effects == 10
        assert opts.include_timestamps is False
        assert opts.truncate_content == 50


# =============================================================================
# MarkdownFormatter Tests
# =============================================================================


class TestMarkdownFormatter:
    """Tests for MarkdownFormatter."""

    def test_formats_header(self, simple_stream: Stream):
        """MarkdownFormatter includes execution summary header."""
        output = MarkdownFormatter().format_stream(simple_stream)

        assert "## Execution Summary: FixBug" in output
        assert "**Status**: Completed" in output
        assert "**Tool Calls**: 2" in output

    def test_formats_timeline(self, simple_stream: Stream):
        """MarkdownFormatter includes timeline table."""
        output = MarkdownFormatter().format_stream(simple_stream)

        assert "### Timeline" in output
        assert "| # | Event | Details |" in output
        assert "Task Started" in output
        assert "Tool Call Started" in output

    def test_formats_files_accessed(self, simple_stream: Stream):
        """MarkdownFormatter includes files accessed section."""
        output = MarkdownFormatter().format_stream(simple_stream)

        assert "### Files Accessed" in output
        assert "**Read**: `src/auth.py`" in output
        assert "**Modified**: `src/auth.py`" in output

    def test_formats_errors(self, failed_stream: Stream):
        """MarkdownFormatter includes error section for failures."""
        output = MarkdownFormatter().format_stream(failed_stream)

        assert "**Status**: Failed" in output
        assert "### Errors" in output
        assert "**Type**: FileNotFoundError" in output
        assert "**Location**: provider.py:123" in output
        assert "**Suggestions**:" in output

    def test_formats_agent_reasoning(self, simple_stream: Stream):
        """MarkdownFormatter includes agent reasoning section."""
        output = MarkdownFormatter().format_stream(simple_stream)

        assert "### Agent Reasoning" in output
        assert "I see the bug is on line 42" in output

    def test_handles_empty_stream(self, empty_stream: Stream):
        """MarkdownFormatter handles empty stream."""
        output = MarkdownFormatter().format_stream(empty_stream)

        assert "No effects recorded" in output

    def test_respects_max_effects(self, simple_stream: Stream):
        """MarkdownFormatter respects max_effects option."""
        opts = FormatterOptions(max_effects=3)
        output = MarkdownFormatter().format_stream(simple_stream, opts)

        assert "more effects" in output

    def test_format_effect_single(self, simple_stream: Stream):
        """MarkdownFormatter can format single effect."""
        formatter = MarkdownFormatter()
        effect = simple_stream[0].effect

        output = formatter.format_effect(effect)
        assert "task_started" in output


# =============================================================================
# CompactFormatter Tests
# =============================================================================


class TestCompactFormatter:
    """Tests for CompactFormatter."""

    def test_one_line_per_effect(self, simple_stream: Stream):
        """CompactFormatter outputs one line per effect."""
        output = CompactFormatter().format_stream(simple_stream)
        lines = [line for line in output.split("\n") if line.strip()]

        assert len(lines) == 9  # 9 effects in simple_stream

    def test_includes_timestamps(self, simple_stream: Stream):
        """CompactFormatter includes relative timestamps."""
        output = CompactFormatter().format_stream(simple_stream)

        # Should have bracketed timestamps
        assert "[" in output
        assert "]" in output

    def test_includes_effect_details(self, simple_stream: Stream):
        """CompactFormatter includes key effect details."""
        output = CompactFormatter().format_stream(simple_stream)

        assert "TaskStarted" in output
        assert "task=FixBug" in output
        assert "ToolCallStarted" in output
        assert "tool=read_file" in output

    def test_handles_empty_stream(self, empty_stream: Stream):
        """CompactFormatter handles empty stream."""
        output = CompactFormatter().format_stream(empty_stream)

        assert "No effects recorded" in output

    def test_respects_max_effects(self, simple_stream: Stream):
        """CompactFormatter respects max_effects option."""
        opts = FormatterOptions(max_effects=3)
        output = CompactFormatter().format_stream(simple_stream, opts)
        lines = [line for line in output.split("\n") if line.strip()]

        assert len(lines) == 4  # 3 effects + "more effects" line
        assert "more effects" in output

    def test_format_effect_single(self, simple_stream: Stream):
        """CompactFormatter can format single effect."""
        formatter = CompactFormatter()
        effect = simple_stream[0].effect

        output = formatter.format_effect(effect)
        assert "TaskStarted" in output
        assert "task=FixBug" in output


# =============================================================================
# JSONFormatter Tests
# =============================================================================


class TestJSONFormatter:
    """Tests for JSONFormatter."""

    def test_full_mode(self, simple_stream: Stream):
        """JSONFormatter full mode outputs complete data."""
        formatter = JSONFormatter(mode="full")
        output = formatter.format_stream(simple_stream)

        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) == 9

        # Full mode includes all fields
        first = data[0]
        assert "effect_type" in first
        assert "task_name" in first
        assert "timestamp" in first

    def test_compact_mode(self, simple_stream: Stream):
        """JSONFormatter compact mode outputs essential fields."""
        formatter = JSONFormatter(mode="compact")
        output = formatter.format_stream(simple_stream)

        data = json.loads(output)
        assert isinstance(data, list)

        # Compact mode has fewer fields
        first = data[0]
        assert "type" in first
        assert "seq" in first

    def test_summary_mode(self, simple_stream: Stream):
        """JSONFormatter summary mode outputs statistics."""
        formatter = JSONFormatter(mode="summary")
        output = formatter.format_stream(simple_stream)

        data = json.loads(output)
        assert "total_effects" in data
        assert data["total_effects"] == 9
        assert "tool_calls" in data
        assert data["tool_calls"] == 2
        assert "files_read" in data
        assert "src/auth.py" in data["files_read"]
        assert data["succeeded"] is True
        assert data["failed"] is False

    def test_summary_mode_failed(self, failed_stream: Stream):
        """JSONFormatter summary mode tracks failures."""
        formatter = JSONFormatter(mode="summary")
        output = formatter.format_stream(failed_stream)

        data = json.loads(output)
        assert data["succeeded"] is False
        assert data["failed"] is True

    def test_handles_empty_stream(self, empty_stream: Stream):
        """JSONFormatter handles empty stream."""
        formatter = JSONFormatter(mode="summary")
        output = formatter.format_stream(empty_stream)

        data = json.loads(output)
        assert data["total_effects"] == 0

    def test_format_effect_single(self, simple_stream: Stream):
        """JSONFormatter can format single effect."""
        formatter = JSONFormatter()
        effect = simple_stream[0].effect

        output = formatter.format_effect(effect)
        data = json.loads(output)
        assert data["effect_type"] == "task_started"


# =============================================================================
# TreeFormatter Tests
# =============================================================================


class TestTreeFormatter:
    """Tests for TreeFormatter."""

    def test_formats_tree_structure(self, simple_stream: Stream):
        """TreeFormatter outputs tree structure."""
        output = TreeFormatter().format_stream(simple_stream)

        # Should have tree connectors
        assert "+--" in output

    def test_shows_task_boundaries(self, simple_stream: Stream):
        """TreeFormatter shows task start and complete."""
        output = TreeFormatter().format_stream(simple_stream)

        assert "TaskStarted: FixBug" in output
        assert "TaskCompleted: FixBug" in output

    def test_shows_tool_calls(self, simple_stream: Stream):
        """TreeFormatter shows tool calls."""
        output = TreeFormatter().format_stream(simple_stream)

        assert "ToolCallStarted: read_file" in output
        assert "ToolCallStarted: edit_file" in output

    def test_shows_file_operations(self, simple_stream: Stream):
        """TreeFormatter shows file operations."""
        output = TreeFormatter().format_stream(simple_stream)

        assert "FileRead: src/auth.py" in output
        assert "FilePatch: src/auth.py" in output

    def test_handles_empty_stream(self, empty_stream: Stream):
        """TreeFormatter handles empty stream."""
        output = TreeFormatter().format_stream(empty_stream)

        assert "No effects recorded" in output

    def test_format_effect_single(self, simple_stream: Stream):
        """TreeFormatter can format single effect."""
        formatter = TreeFormatter()
        effect = simple_stream[0].effect

        output = formatter.format_effect(effect)
        assert "TaskStarted: FixBug" in output


# =============================================================================
# Stream Convenience Methods Tests
# =============================================================================


class TestStreamConvenienceMethods:
    """Tests for Stream.to_markdown(), to_compact(), to_tree()."""

    def test_to_markdown(self, simple_stream: Stream):
        """Stream.to_markdown() works."""
        output = simple_stream.to_markdown()

        assert "## Execution Summary" in output
        assert "FixBug" in output

    def test_to_compact(self, simple_stream: Stream):
        """Stream.to_compact() works."""
        output = simple_stream.to_compact()
        lines = output.split("\n")

        assert len(lines) == 9

    def test_to_tree(self, simple_stream: Stream):
        """Stream.to_tree() works."""
        output = simple_stream.to_tree()

        assert "+--" in output

    def test_to_markdown_with_options(self, simple_stream: Stream):
        """Stream.to_markdown() accepts options."""
        output = simple_stream.to_markdown(max_effects=3)

        assert "more effects" in output

    def test_first_error(self, failed_stream: Stream):
        """Stream.first_error() returns first TaskFailed."""
        error = failed_stream.first_error()

        assert error is not None
        assert error.error_type == "FileNotFoundError"

    def test_first_error_none(self, simple_stream: Stream):
        """Stream.first_error() returns None when no errors."""
        error = simple_stream.first_error()

        assert error is None


# =============================================================================
# Edge Cases
# =============================================================================


class TestFormatterEdgeCases:
    """Edge case tests for formatters."""

    def test_very_long_content_truncation(self):
        """Formatters truncate very long content."""
        stream = Stream()
        long_content = "x" * 1000
        stream = stream.append(AgentThinking(content=long_content))

        opts = FormatterOptions(truncate_content=50)
        output = MarkdownFormatter().format_stream(stream, opts)

        # Should be truncated
        assert "x" * 100 not in output

    def test_special_characters_in_content(self):
        """Formatters handle special characters."""
        stream = Stream()
        stream = stream.append(AgentThinking(content="Hello | World | Test\nNewline"))
        stream = stream.append(TaskStarted(task_name="Test"))

        # Should not crash
        markdown = MarkdownFormatter().format_stream(stream)
        compact = CompactFormatter().format_stream(stream)
        json_out = JSONFormatter().format_stream(stream)
        tree = TreeFormatter().format_stream(stream)

        assert len(markdown) > 0
        assert len(compact) > 0
        assert len(json_out) > 0
        assert len(tree) > 0

    def test_many_files_accessed(self):
        """Formatters handle many file operations."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))
        for i in range(50):
            stream = stream.append(FileRead(path=f"src/file_{i}.py"))
        stream = stream.append(TaskCompleted(task_name="Test", duration_ms=100))

        # Should not crash or hang
        output = MarkdownFormatter().format_stream(stream)
        assert "### Files Accessed" in output
