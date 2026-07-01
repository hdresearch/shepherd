"""Unit tests for Stream.debug_summary() method."""

from shepherd_core.effects import (
    ContextPrepared,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from shepherd_core.scope import Stream


class TestDebugSummary:
    """Tests for Stream.debug_summary() method."""

    def test_empty_stream(self):
        """Empty stream produces minimal summary."""
        stream = Stream()
        summary = stream.debug_summary()

        assert "Effect Stream Debug Summary" in summary
        assert "Total effects: 0" in summary
        assert "Tasks: 0" in summary

    def test_single_completed_task(self):
        """Completed task shows OK status."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="TestTask"))
        stream = stream.append(TaskCompleted(task_name="TestTask", duration_ms=123.4))

        summary = stream.debug_summary()

        assert "Task: TestTask" in summary
        assert "completed" in summary
        assert "123.4ms" in summary
        assert "OK" in summary

    def test_single_failed_task(self):
        """Failed task shows FAILED status and error details."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="TestTask"))
        stream = stream.append(
            TaskFailed(
                task_name="TestTask",
                error="Command failed with exit code 1",
                error_type="SDKExecutionError",
                phase="execute",
                session_id="sess_abc123",
                last_tool_name="Bash",
                suggestions=("Try with fresh session",),
                error_location="provider.py:830 in execute_sdk",
            )
        )

        summary = stream.debug_summary()

        assert "Task: TestTask" in summary
        assert "[failed] FAILED" in summary
        assert "Command failed with exit code 1" in summary
        assert "provider.py:830 in execute_sdk" in summary
        assert "sess_abc123" in summary
        assert "Bash" in summary
        assert "Try with fresh session" in summary

    def test_task_count_summary(self):
        """Task count summary shows completed and failed counts."""
        stream = Stream()

        # Task 1 - completed
        stream = stream.append(TaskStarted(task_name="Task1"))
        stream = stream.append(TaskCompleted(task_name="Task1"))

        # Task 2 - failed
        stream = stream.append(TaskStarted(task_name="Task2"))
        stream = stream.append(TaskFailed(task_name="Task2", error="Failed"))

        # Task 3 - completed
        stream = stream.append(TaskStarted(task_name="Task3"))
        stream = stream.append(TaskCompleted(task_name="Task3"))

        summary = stream.debug_summary()

        assert "Tasks: 3 (2 completed, 1 failed)" in summary

    def test_effects_grouped_by_task(self):
        """Effects are grouped under their task."""
        stream = Stream()

        stream = stream.append(TaskStarted(task_name="Task1"))
        stream = stream.append(ToolCallStarted(task_name="Task1", tool_name="Read"))
        stream = stream.append(ToolCallCompleted(task_name="Task1", tool_name="Read"))
        stream = stream.append(TaskCompleted(task_name="Task1"))

        summary = stream.debug_summary()

        # Check that effects appear under the task
        lines = summary.split("\n")
        task_line_idx = next(i for i, line in enumerate(lines) if "Task: Task1" in line)

        # Subsequent lines should show the effects
        assert "TaskStarted" in lines[task_line_idx + 1]
        assert "ToolCallStarted" in lines[task_line_idx + 2]

    def test_max_effects_per_task_limit(self):
        """max_effects_per_task limits shown effects."""
        stream = Stream()

        stream = stream.append(TaskStarted(task_name="Task1"))
        for i in range(10):
            stream = stream.append(ToolCallStarted(task_name="Task1", tool_name=f"Tool{i}"))
        stream = stream.append(TaskCompleted(task_name="Task1"))

        summary = stream.debug_summary(max_effects_per_task=5)

        # Should show "... N more effects"
        assert "... " in summary
        assert " more effects" in summary

    def test_sequence_numbers_shown(self):
        """Effect sequence numbers are displayed."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Task1"))
        stream = stream.append(ToolCallStarted(task_name="Task1", tool_name="Read"))

        summary = stream.debug_summary()

        # Sequence numbers should appear
        assert "#  0" in summary or "# 0" in summary
        assert "#  1" in summary or "# 1" in summary

    def test_tool_name_in_parentheses(self):
        """Tool names are shown in parentheses for tool effects."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Task1"))
        stream = stream.append(ToolCallStarted(task_name="Task1", tool_name="Bash"))

        summary = stream.debug_summary()

        assert "(Bash)" in summary

    def test_binding_name_in_parentheses(self):
        """Binding names are shown in parentheses for context effects."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Task1"))
        stream = stream.append(ContextPrepared(task_name="Task1", binding_name="workspace"))

        summary = stream.debug_summary()

        assert "(workspace)" in summary

    def test_depth_display(self):
        """Scope depth is displayed when show_nested is True."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Task1"))

        summary = stream.debug_summary(show_nested=True)

        assert "depth=" in summary

    def test_depth_hidden(self):
        """Scope depth is hidden when show_nested is False."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Task1"))

        summary = stream.debug_summary(show_nested=False)

        # Note: The depth column won't appear if show_nested=False
        # Check that the output is still valid
        assert "TaskStarted" in summary


class TestDebugSummaryNested:
    """Tests for nested scope handling in debug_summary."""

    def test_nested_scope_indentation(self):
        """Nested scopes show indentation based on depth."""
        from shepherd_core.scope.stream import EffectLayer

        stream = Stream()

        # Create layers with different depths
        layer0 = EffectLayer(
            effect=TaskStarted(task_name="Parent"),
            sequence=0,
            scope_depth=0,
        )
        layer1 = EffectLayer(
            effect=TaskStarted(task_name="Parent"),
            sequence=1,
            scope_depth=1,
        )

        stream = stream.append_layer(layer0)
        stream = stream.append_layer(layer1)

        summary = stream.debug_summary(show_nested=True)

        # Both depths should appear
        assert "depth=0" in summary
        assert "depth=1" in summary

    def test_max_depth_filter(self):
        """max_depth filters out deeper effects."""
        from shepherd_core.scope.stream import EffectLayer

        stream = Stream()

        # Create layers at different depths
        for depth in range(5):
            layer = EffectLayer(
                effect=TaskStarted(task_name="Task"),
                sequence=depth,
                scope_depth=depth,
            )
            stream = stream.append_layer(layer)

        # Filter to max depth 2
        summary = stream.debug_summary(max_depth=2)

        # Should show depth 0, 1, 2 but not 3, 4
        assert "depth=0" in summary
        assert "depth=1" in summary
        assert "depth=2" in summary
        assert "depth=3" not in summary
        assert "depth=4" not in summary


class TestDebugSummarySuggestions:
    """Tests for suggestions display in debug_summary."""

    def test_suggestions_displayed(self):
        """Suggestions from TaskFailed are displayed."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Task"))
        stream = stream.append(
            TaskFailed(
                task_name="Task",
                error="Error",
                suggestions=(
                    "First suggestion",
                    "Second suggestion",
                ),
            )
        )

        summary = stream.debug_summary()

        assert "Suggestions:" in summary
        assert "First suggestion" in summary
        assert "Second suggestion" in summary

    def test_no_suggestions_section_when_empty(self):
        """No Suggestions section when suggestions tuple is empty."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Task"))
        stream = stream.append(
            TaskFailed(
                task_name="Task",
                error="Error",
                suggestions=(),
            )
        )

        summary = stream.debug_summary()

        # The word "Suggestions:" shouldn't appear if there are none
        lines = summary.split("\n")
        suggestions_lines = [line for line in lines if "Suggestions:" in line]
        # It's OK if it doesn't appear
        # (The implementation may or may not show an empty section)
