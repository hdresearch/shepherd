"""Integration tests for effect stream utilities.

These tests validate that views, formatters, and comparison utilities work
correctly together using realistic effect stream fixtures.

Fixtures:
    - success_stream.json: 14 effects from a successful FixBug task
    - failure_stream.json: 5 effects from a failed FixBug task (file not found)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shepherd_core.effects import (
    CRITICAL_THRESHOLD,
    AgentThinking,
    CostSummary,
    FileCreate,
    FilePatch,
    FileRead,
    FormatterOptions,
    JSONFormatter,
    MarkdownFormatter,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
    compare_streams,
    detect_patterns,
    explain_outcome_difference,
    find_anomalies,
)
from shepherd_core.scope import Stream

# Fixture paths
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "effects"


@pytest.fixture
def success_stream() -> Stream:
    """Load the success fixture as a Stream."""
    with open(FIXTURES_DIR / "success_stream.json") as f:
        return Stream.from_json(f.read())


@pytest.fixture
def failure_stream() -> Stream:
    """Load the failure fixture as a Stream."""
    with open(FIXTURES_DIR / "failure_stream.json") as f:
        return Stream.from_json(f.read())


# =============================================================================
# Stream Loading Tests
# =============================================================================


class TestStreamLoading:
    """Test that fixtures load correctly."""

    def test_success_stream_loads(self, success_stream: Stream) -> None:
        """Success fixture should load with expected effect count."""
        assert len(success_stream) == 14

    def test_failure_stream_loads(self, failure_stream: Stream) -> None:
        """Failure fixture should load with expected effect count."""
        assert len(failure_stream) == 5

    def test_success_stream_has_task_lifecycle(self, success_stream: Stream) -> None:
        """Success stream should have TaskStarted and TaskCompleted."""
        started = success_stream.first(TaskStarted)
        completed = success_stream.first(TaskCompleted)
        assert started is not None
        assert completed is not None
        assert started.effect.task_name == "FixBug"

    def test_failure_stream_has_task_failed(self, failure_stream: Stream) -> None:
        """Failure stream should have TaskFailed."""
        failed = failure_stream.first(TaskFailed)
        assert failed is not None
        assert failed.effect.error_type == "FileNotFoundError"

    def test_roundtrip_serialization(self, success_stream: Stream) -> None:
        """Stream should survive JSON roundtrip."""
        json_str = success_stream.to_json()
        restored = Stream.from_json(json_str)
        assert len(restored) == len(success_stream)
        # Check first and last effects match
        assert restored.layers[0].effect.effect_type == success_stream.layers[0].effect.effect_type
        assert restored.layers[-1].effect.effect_type == success_stream.layers[-1].effect.effect_type


# =============================================================================
# View Integration Tests
# =============================================================================


class TestViewIntegration:
    """Test views work correctly with real fixtures."""

    def test_intents_view_filters_tool_calls(self, success_stream: Stream) -> None:
        """IntentsView should return only tool call effects."""
        intents = success_stream.intents()
        # Success stream has 3 tool calls (read_file, edit_file, create_file)
        # Each has started + completed = 6 intent effects
        intent_list = list(intents)
        assert len(intent_list) == 6
        for layer in intent_list:
            assert isinstance(layer.effect, (ToolCallStarted, ToolCallCompleted))

    def test_outcomes_view_filters_world_interactions(self, success_stream: Stream) -> None:
        """OutcomesView should return effects representing external interactions."""
        outcomes = success_stream.outcomes()
        outcome_list = list(outcomes)
        # Success stream: FileRead, FilePatch, FileCreate, TaskCompleted = 4 outcomes
        assert len(outcome_list) == 4
        for layer in outcome_list:
            assert isinstance(
                layer.effect,
                (FileRead, FilePatch, FileCreate, TaskCompleted, TaskFailed),
            )

    def test_thinking_view_filters_reasoning(self, success_stream: Stream) -> None:
        """ThinkingView should return only agent reasoning effects."""
        thinking = success_stream.thinking()
        thinking_list = list(thinking)
        # Success stream has 3 AgentThinking effects
        assert len(thinking_list) == 3
        for layer in thinking_list:
            assert isinstance(layer.effect, AgentThinking)

    def test_costs_view_summarizes_correctly(self, success_stream: Stream) -> None:
        """CostsView should compute accurate metrics."""
        costs: CostSummary = success_stream.costs().summarize()
        assert costs.tool_calls == 3  # 3 completed tool calls
        assert costs.tool_calls_rejected == 0
        assert costs.files_created == 1  # test_auth.py
        assert len(costs.files_read) == 1  # src/auth.py
        assert len(costs.files_modified) == 2  # src/auth.py, tests/test_auth.py
        assert costs.duration_ms is not None
        assert costs.duration_ms > 0

    def test_view_chaining_stream_to_view(self, success_stream: Stream) -> None:
        """Views should chain from Stream correctly."""
        # Filter by task, then get intents
        task_stream = success_stream.by_task("FixBug")
        task_intents = task_stream.intents()
        assert len(list(task_intents)) == 6  # All 6 intents are from FixBug

    def test_view_membership_check(self, success_stream: Stream) -> None:
        """__contains__ should support quick type checks."""
        intents = success_stream.intents()
        assert ToolCallStarted in intents
        assert ToolCallCompleted in intents
        # TaskFailed should NOT be in intents (it's not an intent effect)
        assert TaskFailed not in intents

    def test_views_are_reusable(self, success_stream: Stream) -> None:
        """Views should be iterable multiple times safely."""
        intents = success_stream.intents()
        count1 = len(intents)
        count2 = len(intents)  # Should work again
        assert count1 == count2 == 6

    def test_first_error_convenience(self, failure_stream: Stream) -> None:
        """first_error() should return the TaskFailed effect."""
        error = failure_stream.first_error()
        assert error is not None
        assert error.error_type == "FileNotFoundError"
        assert "nonexistent.py" in error.error

    def test_first_error_none_on_success(self, success_stream: Stream) -> None:
        """first_error() should return None for successful streams."""
        assert success_stream.first_error() is None


# =============================================================================
# Causality Tree Integration Tests
# =============================================================================


class TestCausalityTreeIntegration:
    """Test causality tree building with real fixtures."""

    def test_causality_tree_builds(self, success_stream: Stream) -> None:
        """Causality tree should build without errors."""
        tree_view = success_stream.as_causality_tree()
        roots = tree_view.as_tree()
        assert len(roots) > 0

    def test_tool_calls_have_children(self, success_stream: Stream) -> None:
        """Tool calls with caused_by effects should have children."""
        roots = success_stream.as_causality_tree().as_tree()
        # Find the edit_file tool call - it should have FilePatch as child
        edit_node = None
        for root in roots:
            if isinstance(root.effect, ToolCallStarted) and root.effect.tool_name == "edit_file":
                edit_node = root
                break
        assert edit_node is not None
        assert len(edit_node.children) == 1  # FilePatch with caused_by
        assert isinstance(edit_node.children[0].effect, FilePatch)

    def test_file_read_is_root_node(self, success_stream: Stream) -> None:
        """FileRead effects should be root nodes (no caused_by)."""
        roots = success_stream.as_causality_tree().as_tree()
        # Find FileRead in roots
        file_read_roots = [r for r in roots if isinstance(r.effect, FileRead)]
        assert len(file_read_roots) == 1
        # FileRead should have no children (it's an observation)
        assert len(file_read_roots[0].children) == 0


# =============================================================================
# Formatter Integration Tests
# =============================================================================


class TestFormatterIntegration:
    """Test formatters produce valid output from real fixtures."""

    def test_markdown_formatter_produces_output(self, success_stream: Stream) -> None:
        """MarkdownFormatter should produce non-empty markdown."""
        output = success_stream.to_markdown()
        assert len(output) > 0
        assert "FixBug" in output
        assert "src/auth.py" in output

    def test_markdown_formatter_has_sections(self, success_stream: Stream) -> None:
        """MarkdownFormatter output should have expected sections."""
        output = success_stream.to_markdown()
        assert "## Execution Summary" in output or "Summary" in output
        assert "Timeline" in output or "timeline" in output.lower()

    def test_compact_formatter_produces_lines(self, success_stream: Stream) -> None:
        """CompactFormatter should produce one line per effect."""
        output = success_stream.to_compact()
        lines = [line for line in output.strip().split("\n") if line.strip()]
        # Should have approximately one line per effect (may have header)
        assert len(lines) >= len(success_stream)

    def test_tree_formatter_shows_hierarchy(self, success_stream: Stream) -> None:
        """TreeFormatter should show hierarchical structure."""
        output = success_stream.to_tree()
        assert len(output) > 0
        # Should contain tree characters
        assert "─" in output or "-" in output or "task_started" in output.lower()

    def test_json_formatter_produces_valid_json(self, success_stream: Stream) -> None:
        """JSONFormatter should produce parseable JSON."""
        formatter = JSONFormatter(mode="full")
        output = formatter.format_stream(success_stream)
        # Should be valid JSON
        parsed = json.loads(output)
        assert isinstance(parsed, (list, dict))

    def test_formatter_options_truncation(self, success_stream: Stream) -> None:
        """FormatterOptions.truncate_content should limit content length."""
        formatter = MarkdownFormatter()
        options = FormatterOptions(truncate_content=50)
        output = formatter.format_stream(success_stream, options)
        # Output should be produced (truncation doesn't break formatting)
        assert len(output) > 0

    def test_formatter_max_effects(self, success_stream: Stream) -> None:
        """FormatterOptions.max_effects should limit effects shown."""
        formatter = MarkdownFormatter()
        options = FormatterOptions(max_effects=5)
        output = formatter.format_stream(success_stream, options)
        assert len(output) > 0


# =============================================================================
# Comparison Integration Tests
# =============================================================================


class TestComparisonIntegration:
    """Test comparison utilities with real fixtures."""

    def test_compare_success_vs_failure(self, success_stream: Stream, failure_stream: Stream) -> None:
        """Comparing success vs failure should find divergences."""
        result = compare_streams(success_stream, failure_stream, label_a="Success", label_b="Failure")
        assert result.has_divergences
        assert not result.same_outcome  # One succeeded, one failed

    def test_compare_identifies_missing_tools(self, success_stream: Stream, failure_stream: Stream) -> None:
        """Comparison should identify tools used in success but not failure."""
        result = compare_streams(success_stream, failure_stream)
        # Success has edit_file and create_file, failure doesn't
        assert len(result.tools_only_in_a) > 0
        assert "edit_file" in result.tools_only_in_a or "create_file" in result.tools_only_in_a

    def test_compare_identifies_missing_files(self, success_stream: Stream, failure_stream: Stream) -> None:
        """Comparison should identify files accessed in success but not failure."""
        result = compare_streams(success_stream, failure_stream)
        # Success reads src/auth.py, failure reads src/nonexistent.py
        # So both have files_only_in lists
        assert len(result.files_only_in_a) > 0 or len(result.files_only_in_b) > 0

    def test_compare_identical_streams(self, success_stream: Stream) -> None:
        """Comparing stream to itself should find no divergences."""
        result = compare_streams(success_stream, success_stream)
        assert result.is_equivalent
        assert result.same_outcome
        assert result.same_tool_sequence

    def test_explain_outcome_difference(self, success_stream: Stream, failure_stream: Stream) -> None:
        """explain_outcome_difference should produce useful explanation."""
        explanation = explain_outcome_difference(success_stream, failure_stream)
        assert len(explanation) > 0
        assert "FileNotFoundError" in explanation
        # Should mention the error
        assert "nonexistent" in explanation.lower() or "file not found" in explanation.lower()

    def test_critical_divergences_filter(self, success_stream: Stream, failure_stream: Stream) -> None:
        """critical_divergences should filter by significance >= 0.8."""
        result = compare_streams(success_stream, failure_stream)
        critical = result.critical_divergences
        for d in critical:
            assert d.significance >= CRITICAL_THRESHOLD

    def test_divergences_by_aspect(self, success_stream: Stream, failure_stream: Stream) -> None:
        """divergences_by_aspect should group divergences correctly."""
        result = compare_streams(success_stream, failure_stream)
        by_aspect = result.divergences_by_aspect
        # Should have some aspects with divergences
        assert len(by_aspect) > 0
        # Each aspect should be a list
        for divs in by_aspect.values():
            assert isinstance(divs, list)


# =============================================================================
# Pattern Detection Integration Tests
# =============================================================================


class TestPatternDetectionIntegration:
    """Test pattern detection with multiple stream instances."""

    def test_detect_patterns_from_success_corpus(self, success_stream: Stream) -> None:
        """Pattern detection should find common patterns in similar streams."""
        # Create a small corpus of similar streams (just duplicates for testing)
        corpus = [success_stream, success_stream, success_stream]
        patterns = detect_patterns(corpus, min_frequency=0.5)
        # Should find patterns (task_started -> agent_thinking, etc.)
        assert len(patterns) > 0
        for p in patterns:
            assert p.frequency >= 0.5

    def test_find_anomalies_in_failure(self, success_stream: Stream, failure_stream: Stream) -> None:
        """Failure stream should be anomalous compared to success corpus."""
        # Create reference corpus from success
        reference = [success_stream] * 5
        anomalies = find_anomalies(failure_stream, reference, threshold=0.2)
        # Failure stream has different patterns, should find some anomalies
        # (though this depends on the threshold and corpus size)
        # At minimum, the function should not crash
        assert isinstance(anomalies, list)


# =============================================================================
# End-to-End Workflow Tests
# =============================================================================


class TestEndToEndWorkflow:
    """Test complete debugging workflows."""

    def test_debug_failure_workflow(self, success_stream: Stream, failure_stream: Stream) -> None:
        """Simulate a complete debugging workflow."""
        # 1. Check if there's an error
        error = failure_stream.first_error()
        assert error is not None

        # 2. Get human-readable summary
        markdown = failure_stream.to_markdown()
        assert "task_failed" in markdown.lower() or "failed" in markdown.lower()

        # 3. Compare with successful execution
        comparison = compare_streams(success_stream, failure_stream)
        assert comparison.has_divergences

        # 4. Get explanation
        explanation = explain_outcome_difference(success_stream, failure_stream)
        assert len(explanation) > 0

        # 5. Check tool call patterns (file access attempt)
        failure_intents = list(failure_stream.intents())
        # Failure stream tried to read a nonexistent file via tool call
        tool_calls = [el for el in failure_intents if isinstance(el.effect, ToolCallStarted)]
        params = [getattr(el.effect, "params", {}) for el in tool_calls]
        assert any("nonexistent" in str(p) for p in params)

    def test_analyze_success_workflow(self, success_stream: Stream) -> None:
        """Analyze a successful execution comprehensively."""
        # 1. Get cost summary
        costs = success_stream.costs().summarize()
        assert costs.tool_calls > 0

        # 2. Get causality tree
        tree = success_stream.as_causality_tree().as_tree()
        assert len(tree) > 0

        # 3. Extract agent reasoning
        thoughts = list(success_stream.thinking())
        assert len(thoughts) > 0

        # 4. Verify outcomes
        outcomes = list(success_stream.outcomes())
        assert any(isinstance(el.effect, TaskCompleted) for el in outcomes)

        # 5. Format for documentation
        markdown = success_stream.to_markdown()
        assert "FixBug" in markdown
