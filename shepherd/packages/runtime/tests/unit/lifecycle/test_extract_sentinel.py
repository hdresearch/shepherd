"""Tests for extract_effects with sentinel ExecutionResult (Spike 3).

Validates that WorkspaceRef and SimpleWorkspace hybrid extraction
works correctly when given a sentinel ExecutionResult (empty tool_calls).
These are the two contexts that extract effects independently of tool calls
via sandbox filesystem diffs.
"""

from __future__ import annotations

from shepherd_core.types import ExecutionResult

# ---------------------------------------------------------------------------
# Sentinel construction test
# ---------------------------------------------------------------------------


class TestSentinelResult:
    """Validate that the sentinel ExecutionResult has the expected shape."""

    def test_sentinel_has_empty_tool_calls(self) -> None:
        sentinel = ExecutionResult(
            success=True,
            output_text="",
            metadata={"task_name": "TestTask"},
        )
        assert sentinel.tool_calls == ()
        assert sentinel.tool_results == ()
        assert sentinel.success is True
        assert sentinel.output_text == ""
        assert sentinel.metadata == {"task_name": "TestTask"}

    def test_sentinel_zip_produces_empty_iterator(self) -> None:
        """The standard extract_effects pattern: zip(tool_calls, tool_results) yields nothing."""
        sentinel = ExecutionResult()
        pairs = list(zip(sentinel.tool_calls, sentinel.tool_results, strict=False))
        assert pairs == []

    def test_sentinel_metadata_get_task_name(self) -> None:
        """result.metadata.get('task_name') returns the task name from sentinel."""
        sentinel = ExecutionResult(metadata={"task_name": "MyTask"})
        assert sentinel.metadata.get("task_name") == "MyTask"

    def test_bare_sentinel_metadata_get_returns_none(self) -> None:
        """Bare sentinel's metadata.get('task_name') returns None."""
        sentinel = ExecutionResult()
        assert sentinel.metadata.get("task_name") is None

    def test_sentinel_session_id_is_none(self) -> None:
        """SessionState would see session_id=None from the sentinel."""
        sentinel = ExecutionResult()
        assert sentinel.session_id is None
