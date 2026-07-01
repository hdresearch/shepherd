"""Tests for effect stream views."""

import pytest
from shepherd_core.effects import (
    AgentMessage,
    AgentThinking,
    FilePatch,
    FileRead,
    LLMResponseReceived,
    PromptSent,
    StepFailed,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    ToolCallCompleted,
    ToolCallRejected,
    ToolCallStarted,
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
    stream = stream.append(AgentThinking(content="I see the bug is on line 42"))
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
# StreamView Base Tests
# =============================================================================


class TestStreamViewBase:
    """Tests for StreamView base class behavior."""

    def test_view_is_reusable(self, simple_stream: Stream):
        """Views can be iterated multiple times."""
        view = simple_stream.intents()

        # First iteration
        first_count = sum(1 for _ in view)

        # Second iteration should work and give same count
        second_count = sum(1 for _ in view)

        assert first_count == second_count == 4  # 2 started + 2 completed

    def test_len_is_safe_to_call_multiple_times(self, simple_stream: Stream):
        """len() doesn't exhaust the view."""
        view = simple_stream.intents()

        len1 = len(view)
        len2 = len(view)

        assert len1 == len2 == 4

    def test_contains_checks_effect_type(self, simple_stream: Stream):
        """__contains__ checks for effect type presence."""
        view = simple_stream.intents()

        assert ToolCallStarted in view
        assert ToolCallCompleted in view
        assert ToolCallRejected not in view  # No rejected in simple_stream

    def test_contains_short_circuits(self, simple_stream: Stream):
        """__contains__ returns early on first match."""
        view = simple_stream.intents()

        # Should find ToolCallStarted quickly (it's first)
        assert ToolCallStarted in view

    def test_first_returns_first_match(self, simple_stream: Stream):
        """first() returns first matching layer."""
        view = simple_stream.intents()
        first = view.first()

        assert first is not None
        assert isinstance(first.effect, ToolCallStarted)
        assert first.effect.tool_name == "read_file"

    def test_first_returns_none_for_empty(self, empty_stream: Stream):
        """first() returns None for empty view."""
        view = empty_stream.intents()
        assert view.first() is None

    def test_last_returns_last_match(self, simple_stream: Stream):
        """last() returns last matching layer."""
        view = simple_stream.intents()
        last = view.last()

        assert last is not None
        assert isinstance(last.effect, ToolCallCompleted)
        assert last.effect.tool_name == "edit_file"

    def test_last_returns_none_for_empty(self, empty_stream: Stream):
        """last() returns None for empty view."""
        view = empty_stream.intents()
        assert view.last() is None

    def test_to_stream_materializes_view(self, simple_stream: Stream):
        """to_stream() creates a new Stream from view contents."""
        view = simple_stream.intents()
        materialized = view.to_stream()

        assert isinstance(materialized, Stream)
        assert len(materialized) == 4  # 2 started + 2 completed

        # Original stream unchanged
        assert len(simple_stream) == 9


# =============================================================================
# IntentsView Tests
# =============================================================================


class TestIntentsView:
    """Tests for IntentsView."""

    def test_filters_to_tool_calls(self, simple_stream: Stream):
        """IntentsView only includes tool call effects."""
        view = simple_stream.intents()
        effect_types = {layer.effect.effect_type for layer in view}

        assert effect_types == {"tool_call_started", "tool_call_completed"}

    def test_includes_rejected_tool_calls(self, failed_stream: Stream):
        """IntentsView includes ToolCallRejected."""
        view = failed_stream.intents()

        assert ToolCallRejected in view
        assert len(view) == 2  # 1 started + 1 rejected

    def test_empty_for_no_tool_calls(self, empty_stream: Stream):
        """IntentsView is empty when no tool calls."""
        view = empty_stream.intents()
        assert len(view) == 0


# =============================================================================
# OutcomesView Tests
# =============================================================================


class TestOutcomesView:
    """Tests for OutcomesView."""

    def test_includes_file_operations(self, simple_stream: Stream):
        """OutcomesView includes file read/create/patch/delete."""
        view = simple_stream.outcomes()
        effect_types = {layer.effect.effect_type for layer in view}

        assert "file_read" in effect_types
        assert "file_patch" in effect_types

    def test_includes_task_outcomes(self, simple_stream: Stream):
        """OutcomesView includes TaskCompleted and TaskFailed."""
        view = simple_stream.outcomes()
        assert TaskCompleted in view

    def test_includes_task_failed(self, failed_stream: Stream):
        """OutcomesView includes TaskFailed."""
        view = failed_stream.outcomes()
        assert TaskFailed in view

    def test_custom_include_types(self, simple_stream: Stream):
        """OutcomesView accepts custom effect types."""
        # Create a stream with agent thinking
        stream = simple_stream

        # Normal outcomes view doesn't include thinking
        normal_view = stream.outcomes()
        assert AgentThinking not in normal_view

        # With include_types, it does
        custom_view = stream.outcomes(include_types=(AgentThinking,))
        assert AgentThinking in custom_view

    def test_excludes_tool_calls(self, simple_stream: Stream):
        """OutcomesView excludes intent effects."""
        view = simple_stream.outcomes()

        assert ToolCallStarted not in view
        assert ToolCallCompleted not in view


# =============================================================================
# CostsView Tests
# =============================================================================


class TestCostsView:
    """Tests for CostsView and CostSummary."""

    def test_summarize_counts_tool_calls(self, simple_stream: Stream):
        """CostSummary counts tool calls."""
        costs = simple_stream.costs().summarize()

        assert costs.tool_calls == 2  # 2 completed

    def test_summarize_counts_rejected(self, failed_stream: Stream):
        """CostSummary counts rejected tool calls."""
        costs = failed_stream.costs().summarize()

        assert costs.tool_calls_rejected == 1

    def test_summarize_tracks_files_read(self, simple_stream: Stream):
        """CostSummary tracks files read."""
        costs = simple_stream.costs().summarize()

        assert "src/auth.py" in costs.files_read
        assert len(costs.files_read) == 1

    def test_summarize_tracks_files_modified(self, simple_stream: Stream):
        """CostSummary tracks files modified."""
        costs = simple_stream.costs().summarize()

        assert "src/auth.py" in costs.files_modified

    def test_summarize_tracks_duration(self, simple_stream: Stream):
        """CostSummary captures task duration."""
        costs = simple_stream.costs().summarize()

        assert costs.duration_ms == 1234.5

    def test_summarize_is_reusable(self, simple_stream: Stream):
        """CostsView.summarize() can be called multiple times."""
        view = simple_stream.costs()

        costs1 = view.summarize()
        costs2 = view.summarize()

        assert costs1 == costs2

    def test_cost_summary_is_frozen(self, simple_stream: Stream):
        """CostSummary is immutable."""
        costs = simple_stream.costs().summarize()

        with pytest.raises(AttributeError):
            costs.tool_calls = 999

    def test_empty_stream_costs(self, empty_stream: Stream):
        """CostSummary handles empty stream."""
        costs = empty_stream.costs().summarize()

        assert costs.tool_calls == 0
        assert costs.files_read == frozenset()
        assert costs.duration_ms is None

    def test_summarize_aggregates_llm_response_tokens(self):
        """CostSummary aggregates token counts from LLMResponseReceived."""
        stream = Stream()
        stream = stream.append(LLMResponseReceived(input_tokens=100, output_tokens=50, total_tokens=150))
        stream = stream.append(LLMResponseReceived(input_tokens=200, output_tokens=80, total_tokens=280))

        costs = stream.costs().summarize()

        assert costs.input_tokens == 300
        assert costs.output_tokens == 130
        assert costs.total_tokens == 430
        assert costs.llm_calls == 2

    def test_summarize_aggregates_cost_usd(self):
        """CostSummary sums cost_usd from LLMResponseReceived effects."""
        stream = Stream()
        stream = stream.append(LLMResponseReceived(cost_usd=0.01, input_tokens=10, output_tokens=5))
        stream = stream.append(LLMResponseReceived(cost_usd=0.025, input_tokens=20, output_tokens=10))

        costs = stream.costs().summarize()

        assert costs.cost_usd is not None
        assert abs(costs.cost_usd - 0.035) < 1e-9

    def test_summarize_cost_usd_none_when_unavailable(self):
        """CostSummary.cost_usd is None when no LLMResponseReceived has cost data."""
        stream = Stream()
        stream = stream.append(LLMResponseReceived(cost_usd=None, input_tokens=10, output_tokens=5))

        costs = stream.costs().summarize()

        assert costs.cost_usd is None

    def test_summarize_cost_usd_partial_availability(self):
        """CostSummary sums cost_usd even when only some responses have it."""
        stream = Stream()
        stream = stream.append(LLMResponseReceived(cost_usd=0.01, input_tokens=10, output_tokens=5))
        stream = stream.append(LLMResponseReceived(cost_usd=None, input_tokens=20, output_tokens=10))

        costs = stream.costs().summarize()

        assert costs.cost_usd is not None
        assert abs(costs.cost_usd - 0.01) < 1e-9

    def test_summarize_no_llm_responses(self, simple_stream: Stream):
        """CostSummary defaults when stream has no LLMResponseReceived."""
        costs = simple_stream.costs().summarize()

        assert costs.input_tokens == 0
        assert costs.output_tokens == 0
        assert costs.total_tokens == 0
        assert costs.cost_usd is None
        assert costs.llm_calls == 0

    def test_summarize_with_cache_tokens(self):
        """CostSummary aggregates correctly with cache token fields."""
        stream = Stream()
        stream = stream.append(
            LLMResponseReceived(
                input_tokens=500,
                output_tokens=200,
                total_tokens=700,
                cost_usd=0.05,
                cache_creation_input_tokens=100,
                cache_read_input_tokens=50,
            )
        )

        costs = stream.costs().summarize()

        assert costs.input_tokens == 500
        assert costs.output_tokens == 200
        assert costs.llm_calls == 1

    def test_summarize_mixed_effects_with_llm(self):
        """CostSummary correctly aggregates when LLM and tool effects coexist."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))
        stream = stream.append(ToolCallCompleted(tool_name="bash", tool_call_id="tc_1", duration_ms=50.0))
        stream = stream.append(LLMResponseReceived(input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=0.01))
        stream = stream.append(TaskCompleted(task_name="Test", duration_ms=500.0))

        costs = stream.costs().summarize()

        assert costs.tool_calls == 1
        assert costs.llm_calls == 1
        assert costs.input_tokens == 100
        assert costs.cost_usd == 0.01
        assert costs.duration_ms == 500.0

    def test_summarize_aggregates_tool_duration(self):
        """CostSummary sums duration_ms from ToolCallCompleted effects."""
        stream = Stream()
        stream = stream.append(ToolCallCompleted(tool_name="bash", tool_call_id="tc_1", duration_ms=120.5))
        stream = stream.append(ToolCallCompleted(tool_name="read_file", tool_call_id="tc_2", duration_ms=45.3))
        stream = stream.append(ToolCallCompleted(tool_name="write_file", tool_call_id="tc_3", duration_ms=80.0))

        costs = stream.costs().summarize()

        assert costs.tool_calls == 3
        assert abs(costs.tool_duration_ms - 245.8) < 1e-9

    def test_summarize_tool_duration_zero_when_no_tools(self, empty_stream: Stream):
        """CostSummary.tool_duration_ms is 0 when no tool calls."""
        costs = empty_stream.costs().summarize()
        assert costs.tool_duration_ms == 0.0

    def test_summarize_tool_duration_complements_llm_duration(self):
        """tool_duration_ms + LLM api time approximates total duration."""
        stream = Stream()
        stream = stream.append(ToolCallCompleted(tool_name="bash", tool_call_id="tc_1", duration_ms=200.0))
        stream = stream.append(
            LLMResponseReceived(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                duration_ms=500.0,
                duration_api_ms=300.0,
            )
        )
        stream = stream.append(TaskCompleted(task_name="Test", duration_ms=500.0))

        costs = stream.costs().summarize()

        assert costs.tool_duration_ms == 200.0
        assert costs.duration_ms == 500.0


# =============================================================================
# ThinkingView Tests
# =============================================================================


class TestThinkingView:
    """Tests for ThinkingView."""

    def test_includes_agent_thinking(self, simple_stream: Stream):
        """ThinkingView includes AgentThinking effects."""
        view = simple_stream.thinking()
        assert AgentThinking in view

    def test_includes_agent_message(self):
        """ThinkingView includes AgentMessage effects."""
        stream = Stream()
        stream = stream.append(AgentMessage(content="Hello!"))

        view = stream.thinking()
        assert AgentMessage in view

    def test_excludes_prompt_sent(self):
        """ThinkingView excludes PromptSent effects."""
        from shepherd_core.effects import PromptSent

        stream = Stream()
        stream = stream.append(PromptSent(system_prompt="system", user_prompt="user"))
        stream = stream.append(AgentThinking(content="thinking..."))

        view = stream.thinking()
        assert PromptSent not in view
        assert len(view) == 1

    def test_internal_only_filters_to_thinking(self, simple_stream: Stream):
        """internal_only() filters to just AgentThinking."""
        stream = Stream()
        stream = stream.append(AgentThinking(content="thinking..."))
        stream = stream.append(AgentMessage(content="response"))

        view = stream.thinking()
        internal = list(view.internal_only())

        assert len(internal) == 1
        assert isinstance(internal[0].effect, AgentThinking)


# =============================================================================
# CausalityTreeView Tests
# =============================================================================


class TestCausalityTreeView:
    """Tests for CausalityTreeView and CausalityNode."""

    def test_builds_tree_from_stream(self, simple_stream: Stream):
        """as_tree() builds causality tree."""
        view = simple_stream.as_causality_tree()
        roots = view.as_tree()

        assert len(roots) > 0

    def test_tool_calls_have_children(self, simple_stream: Stream):
        """ToolCallStarted nodes have result effects as children."""
        view = simple_stream.as_causality_tree()
        roots = view.as_tree()

        # Find the edit_file tool call
        tool_call_nodes = [r for r in roots if isinstance(r.effect, ToolCallStarted)]

        # Second tool call (edit_file) should have FilePatch child
        edit_node = next(n for n in tool_call_nodes if n.effect.tool_name == "edit_file")
        assert len(edit_node.children) == 1
        assert isinstance(edit_node.children[0].effect, FilePatch)

    def test_file_read_is_root(self, simple_stream: Stream):
        """FileRead appears as root (no caused_by)."""
        view = simple_stream.as_causality_tree()
        roots = view.as_tree()

        file_read_roots = [r for r in roots if isinstance(r.effect, FileRead)]
        assert len(file_read_roots) == 1

    def test_tool_call_completed_is_consumed(self, simple_stream: Stream):
        """ToolCallCompleted is not in roots (paired with Started)."""
        view = simple_stream.as_causality_tree()
        roots = view.as_tree()

        completed_roots = [r for r in roots if isinstance(r.effect, ToolCallCompleted)]
        assert len(completed_roots) == 0

    def test_empty_stream_tree(self, empty_stream: Stream):
        """as_tree() returns empty list for empty stream."""
        view = empty_stream.as_causality_tree()
        roots = view.as_tree()

        assert roots == []

    def test_node_walk_traversal(self, simple_stream: Stream):
        """CausalityNode.walk() does pre-order traversal."""
        view = simple_stream.as_causality_tree()
        roots = view.as_tree()

        # Find node with children
        edit_node = None
        for root in roots:
            if isinstance(root.effect, ToolCallStarted) and root.effect.tool_name == "edit_file":
                edit_node = root
                break

        assert edit_node is not None
        walked = list(edit_node.walk())

        # Should include self and child
        assert len(walked) == 2
        assert walked[0] is edit_node
        assert walked[1].effect_type == "file_patch"

    def test_node_repr(self, simple_stream: Stream):
        """CausalityNode has useful repr."""
        view = simple_stream.as_causality_tree()
        roots = view.as_tree()

        repr_str = repr(roots[0])
        assert "CausalityNode" in repr_str
        assert "children=" in repr_str


# =============================================================================
# View Chaining Tests
# =============================================================================


class TestViewChaining:
    """Tests for view composition and chaining."""

    def test_stream_to_view_chaining(self, simple_stream: Stream):
        """Stream filtering followed by view creation."""
        # Note: by_task() filters effects with matching task_name attribute.
        # Most effects (like tool calls) don't have task_name set, so
        # by_task() returns only effects that explicitly have that attribute.

        # Instead, let's test with filter() for more predictable behavior
        filtered = simple_stream.filter(lambda e: e.effect_type in ("tool_call_started", "tool_call_completed"))
        intents = filtered.intents()

        assert len(intents) == 4  # 2 started + 2 completed

    def test_view_to_view_chaining(self, simple_stream: Stream):
        """Views can chain to other views."""
        # Get intents, then filter to outcomes (should be empty - intents aren't outcomes)
        intents = simple_stream.intents()
        outcomes = intents.outcomes()

        assert len(outcomes) == 0

    def test_causality_tree_with_filtered_source(self, simple_stream: Stream):
        """CausalityTreeView works with filtered source."""
        # Filter to specific task
        task_stream = simple_stream.by_task("FixBug")
        tree = task_stream.as_causality_tree()

        roots = tree.as_tree()
        assert len(roots) > 0


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge case tests for views."""

    def test_single_effect_stream(self):
        """Views handle single-effect streams."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))

        assert len(stream.intents()) == 0  # TaskStarted is not an intent
        assert len(stream.outcomes()) == 0  # TaskStarted is not an outcome
        assert len(stream.thinking()) == 0

    def test_unknown_effect_type_in_view(self):
        """Views gracefully handle unknown effect types."""
        from shepherd_core.effects import Effect

        stream = Stream()
        # Base Effect class (unusual but valid)
        stream = stream.append(Effect())

        # Should not crash
        intents = list(stream.intents())
        outcomes = list(stream.outcomes())

        assert len(intents) == 0
        assert len(outcomes) == 0

    def test_task_failed_has_duration(self):
        """TaskFailed carries duration_ms for profiling."""
        effect = TaskFailed(error="boom", error_type="RuntimeError", duration_ms=42.5)
        assert effect.duration_ms == 42.5

    def test_task_failed_duration_defaults_to_zero(self):
        """TaskFailed.duration_ms defaults to 0."""
        effect = TaskFailed(error="boom", error_type="RuntimeError")
        assert effect.duration_ms == 0.0

    def test_prompt_sent_has_input_tokens(self):
        """PromptSent carries input_tokens for request-side token budget."""
        effect = PromptSent(system_prompt="sys", user_prompt="user", input_tokens=1500)
        assert effect.input_tokens == 1500

    def test_prompt_sent_input_tokens_defaults_to_zero(self):
        """PromptSent.input_tokens defaults to 0 (unknown)."""
        effect = PromptSent(system_prompt="sys", user_prompt="user")
        assert effect.input_tokens == 0

    def test_step_failed_has_duration(self):
        """StepFailed carries duration_ms for profiling."""
        effect = StepFailed(step_name="parse", parent_task="Review", error="bad input", duration_ms=15.3)
        assert effect.duration_ms == 15.3

    def test_step_failed_duration_defaults_to_zero(self):
        """StepFailed.duration_ms defaults to 0."""
        effect = StepFailed(step_name="parse", parent_task="Review", error="bad input")
        assert effect.duration_ms == 0.0

    def test_very_long_stream(self):
        """Views handle large streams efficiently."""
        stream = Stream()
        for i in range(1000):
            stream = stream.append(ToolCallStarted(tool_name=f"tool_{i}", tool_call_id=f"tc_{i}"))
            stream = stream.append(ToolCallCompleted(tool_name=f"tool_{i}", tool_call_id=f"tc_{i}"))

        # Should not hang or crash
        assert len(stream.intents()) == 2000

        # First should be efficient
        first = stream.intents().first()
        assert first is not None
        assert first.effect.tool_call_id == "tc_0"
