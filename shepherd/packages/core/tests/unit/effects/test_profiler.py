"""Tests for ProfileView and ProfileSummary."""

import time

import pytest
from shepherd_core.effects import (
    ExecutionFailed,
    FileCreate,
    FileDelete,
    FilePatch,
    FileRead,
    LifecyclePhaseCompleted,
    LifecyclePhaseFailed,
    LLMResponseReceived,
    ModelProfile,
    RecoveryAttempted,
    TaskCacheSummary,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    ToolCallCompleted,
    ToolCallRejected,
    ToolProfile,
    format_profile,
)
from shepherd_core.scope.stream import EffectLayer, Stream
from shepherd_runtime.cache import CacheHit, CacheStored

# =============================================================================
# Helpers
# =============================================================================


def _ts() -> float:
    return time.time()


def _stream_with_scope(*pairs: tuple) -> Stream:
    """Build a stream from (effect, scope_id) pairs with proper EffectLayers."""
    stream = Stream()
    for i, (effect, scope_id) in enumerate(pairs):
        layer = EffectLayer(
            effect=effect,
            sequence=i,
            source_context=None,
            scope_id=scope_id,
            scope_depth=0,
        )
        stream = stream.append_layer(layer)
    return stream


def _make_llm_response(**overrides) -> LLMResponseReceived:
    defaults = {
        "model_id": "claude-sonnet-4-20250514",
        "input_tokens": 1000,
        "output_tokens": 200,
        "total_tokens": 1200,
        "cost_usd": 0.01,
        "duration_ms": 500.0,
        "duration_api_ms": 400.0,
        "num_turns": 2,
        "is_error": False,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "timestamp": _ts(),
    }
    defaults.update(overrides)
    return LLMResponseReceived(**defaults)


# =============================================================================
# TestProfileSummary
# =============================================================================


class TestProfileSummary:
    """Basic aggregation, empty stream, frozen immutability."""

    def test_empty_stream(self):
        summary = Stream().profile().summarize()
        assert summary.cost_summary.llm_calls == 0
        assert summary.cost_summary.tool_calls == 0
        assert summary.time_breakdown.total_ms is None
        assert summary.models == ()
        assert summary.tools == ()
        assert summary.tasks == ()
        assert summary.task_tree == ()

    def test_frozen_immutability(self):
        summary = Stream().profile().summarize()
        with pytest.raises(AttributeError):
            summary.cost_summary = None  # type: ignore[misc]

    def test_basic_aggregation(self):
        ts = _ts()
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T", scope_id="s1", timestamp=ts))
        stream = stream.append(_make_llm_response(timestamp=ts + 1))
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=ts + 2)
        )
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=2000.0, timestamp=ts + 3))

        summary = stream.profile().summarize()
        assert summary.cost_summary.llm_calls == 1
        assert summary.cost_summary.tool_calls == 1
        assert summary.cost_summary.input_tokens == 1000
        assert summary.cost_summary.duration_ms == 2000.0

    def test_cost_summary_includes_file_data(self):
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", timestamp=_ts()))
        stream = stream.append(FileRead(path="b.py", timestamp=_ts()))
        stream = stream.append(FileRead(path="a.py", timestamp=_ts()))  # duplicate
        stream = stream.append(FilePatch(path="c.py", old_content="x", new_content="y", timestamp=_ts()))
        stream = stream.append(FileCreate(path="d.py", timestamp=_ts()))
        stream = stream.append(FileDelete(path="e.py", timestamp=_ts()))

        summary = stream.profile().summarize()
        assert summary.cost_summary.files_read == frozenset({"a.py", "b.py"})
        assert summary.cost_summary.files_modified == frozenset({"c.py", "d.py"})
        assert summary.cost_summary.files_created == 1
        assert summary.cost_summary.files_deleted == 1

    def test_prompt_cache_read_ratio(self):
        stream = Stream()
        stream = stream.append(
            _make_llm_response(
                input_tokens=1000,
                cache_read_input_tokens=700,
            )
        )
        summary = stream.profile().summarize()
        assert summary.prompt_cache_read_ratio == pytest.approx(0.7)

    def test_prompt_cache_read_ratio_none_when_no_tokens(self):
        summary = Stream().profile().summarize()
        assert summary.prompt_cache_read_ratio is None


# =============================================================================
# TestTimeBreakdown
# =============================================================================


class TestTimeBreakdown:
    """Time decomposition logic."""

    def test_llm_timing(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(duration_ms=500, duration_api_ms=400))
        stream = stream.append(_make_llm_response(duration_ms=300, duration_api_ms=200))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()))

        tb = stream.profile().summarize().time_breakdown
        assert tb.llm_api_ms == 600.0
        assert tb.llm_wall_ms == 800.0
        assert tb.total_ms == 1000.0

    def test_tool_execution_ms(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=150.0, timestamp=_ts())
        )
        stream = stream.append(
            ToolCallCompleted(tool_name="read", tool_call_id="tc2", success=True, duration_ms=50.0, timestamp=_ts())
        )

        tb = stream.profile().summarize().time_breakdown
        assert tb.tool_execution_ms == 200.0

    def test_overhead_computed(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(duration_ms=700, duration_api_ms=600))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()))

        tb = stream.profile().summarize().time_breakdown
        assert tb.overhead_ms == pytest.approx(300.0)

    def test_overhead_clamped_to_zero(self):
        """When llm_wall_ms > total_ms (parallel execution), overhead clamps to 0."""
        stream = Stream()
        stream = stream.append(_make_llm_response(duration_ms=1200, duration_api_ms=1000))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()))

        tb = stream.profile().summarize().time_breakdown
        assert tb.overhead_ms == 0.0

    def test_overhead_none_when_no_total(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(duration_ms=500, duration_api_ms=400))

        tb = stream.profile().summarize().time_breakdown
        assert tb.total_ms is None
        assert tb.overhead_ms is None

    def test_intra_turn_overhead(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(duration_ms=1000, duration_api_ms=700))
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=200.0, timestamp=_ts())
        )
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=2000.0, timestamp=_ts()))

        tb = stream.profile().summarize().time_breakdown
        # intra_turn = llm_wall(1000) - llm_api(700) - tool_exec(200) = 100
        assert tb.intra_turn_overhead_ms == pytest.approx(100.0)

    def test_phase_durations(self):
        stream = Stream()
        stream = stream.append(LifecyclePhaseCompleted(phase="prepare", duration_ms=200.0, timestamp=_ts()))
        stream = stream.append(LifecyclePhaseCompleted(phase="extract", duration_ms=100.0, timestamp=_ts()))
        stream = stream.append(LifecyclePhaseCompleted(phase="execute", duration_ms=800.0, timestamp=_ts()))
        stream = stream.append(
            LifecyclePhaseFailed(
                phase="cleanup",
                duration_ms=50.0,
                error_type="RuntimeError",
                error_message="oops",
                timestamp=_ts(),
            )
        )

        tb = stream.profile().summarize().time_breakdown
        assert tb.phase_durations["prepare"] == 200.0
        assert tb.phase_durations["extract"] == 100.0
        assert tb.phase_durations["execute"] == 800.0
        assert tb.phase_durations["cleanup"] == 50.0


# =============================================================================
# TestModelProfile
# =============================================================================


class TestModelProfile:
    """Per-model breakdown."""

    def test_single_model(self):
        stream = Stream()
        stream = stream.append(
            _make_llm_response(model_id="m1", input_tokens=500, output_tokens=100, total_tokens=600, cost_usd=0.01)
        )
        stream = stream.append(
            _make_llm_response(model_id="m1", input_tokens=300, output_tokens=50, total_tokens=350, cost_usd=0.005)
        )

        summary = stream.profile().summarize()
        assert len(summary.models) == 1
        m = summary.models[0]
        assert m.model_id == "m1"
        assert m.llm_calls == 2
        assert m.input_tokens == 800
        assert m.output_tokens == 150
        assert m.cost_usd == pytest.approx(0.015)

    def test_multi_model_separation(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(model_id="m1", cost_usd=0.01))
        stream = stream.append(_make_llm_response(model_id="m2", cost_usd=0.02))

        summary = stream.profile().summarize()
        assert len(summary.models) == 2
        ids = {m.model_id for m in summary.models}
        assert ids == {"m1", "m2"}

    def test_models_by_cost_ordering(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(model_id="cheap", cost_usd=0.001))
        stream = stream.append(_make_llm_response(model_id="expensive", cost_usd=0.1))

        summary = stream.profile().summarize()
        assert summary.models_by_cost[0].model_id == "expensive"

    def test_models_by_tokens_ordering(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(model_id="small", total_tokens=100))
        stream = stream.append(_make_llm_response(model_id="large", total_tokens=10000))

        summary = stream.profile().summarize()
        assert summary.models_by_tokens[0].model_id == "large"

    def test_cache_tokens(self):
        stream = Stream()
        stream = stream.append(
            _make_llm_response(
                input_tokens=1000,
                cache_creation_input_tokens=200,
                cache_read_input_tokens=500,
            )
        )

        m = stream.profile().summarize().models[0]
        assert m.cache_creation_input_tokens == 200
        assert m.cache_read_input_tokens == 500
        assert m.cache_read_ratio == pytest.approx(0.5)

    def test_cache_read_ratio_none_zero_input(self):
        m = ModelProfile(model_id="m", input_tokens=0)
        assert m.cache_read_ratio is None

    def test_error_calls(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(is_error=True))
        stream = stream.append(_make_llm_response(is_error=False))

        m = stream.profile().summarize().models[0]
        assert m.error_calls == 1
        assert m.llm_calls == 2

    def test_total_turns(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(num_turns=3))
        stream = stream.append(_make_llm_response(num_turns=5))

        m = stream.profile().summarize().models[0]
        assert m.total_turns == 8


# =============================================================================
# TestToolProfile
# =============================================================================


class TestToolProfile:
    """Per-tool usage stats."""

    def test_call_count_includes_rejected(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=_ts())
        )
        stream = stream.append(ToolCallRejected(tool_name="bash", tool_call_id="tc2", reason="denied", timestamp=_ts()))

        t = stream.profile().summarize().tools[0]
        assert t.tool_name == "bash"
        assert t.call_count == 2  # 1 completed + 1 rejected
        assert t.success_count == 1
        assert t.rejected_count == 1

    def test_success_rate_over_completions_only(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=_ts())
        )
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc2", success=False, duration_ms=50.0, timestamp=_ts())
        )
        stream = stream.append(ToolCallRejected(tool_name="bash", tool_call_id="tc3", reason="denied", timestamp=_ts()))

        t = stream.profile().summarize().tools[0]
        # success_rate = 1 / (1 + 1) = 0.5 (rejected excluded from denominator)
        assert t.success_rate == pytest.approx(0.5)

    def test_avg_duration_over_completions(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=_ts())
        )
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc2", success=True, duration_ms=200.0, timestamp=_ts())
        )
        stream = stream.append(ToolCallRejected(tool_name="bash", tool_call_id="tc3", reason="x", timestamp=_ts()))

        t = stream.profile().summarize().tools[0]
        # avg = 300 / 2 = 150 (rejected has no duration)
        assert t.avg_duration_ms == pytest.approx(150.0)

    def test_success_rate_default_no_completions(self):
        t = ToolProfile(tool_name="t", call_count=1, rejected_count=1)
        assert t.success_rate == 1.0
        assert t.avg_duration_ms == 0.0

    def test_tools_by_calls_ordering(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="a", tool_call_id="tc1", success=True, duration_ms=10.0, timestamp=_ts())
        )
        stream = stream.append(
            ToolCallCompleted(tool_name="b", tool_call_id="tc2", success=True, duration_ms=10.0, timestamp=_ts())
        )
        stream = stream.append(
            ToolCallCompleted(tool_name="b", tool_call_id="tc3", success=True, duration_ms=10.0, timestamp=_ts())
        )

        summary = stream.profile().summarize()
        assert summary.tools_by_calls[0].tool_name == "b"

    def test_tools_by_duration_ordering(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="fast", tool_call_id="tc1", success=True, duration_ms=10.0, timestamp=_ts())
        )
        stream = stream.append(
            ToolCallCompleted(tool_name="slow", tool_call_id="tc2", success=True, duration_ms=5000.0, timestamp=_ts())
        )

        summary = stream.profile().summarize()
        assert summary.tools_by_duration[0].tool_name == "slow"


# =============================================================================
# TestTaskProfile
# =============================================================================


class TestTaskProfile:
    """Per-task instance breakdown."""

    def test_completed_task(self):
        stream = _stream_with_scope(
            (TaskStarted(task_name="T", scope_id="s1", timestamp=_ts()), "s1"),
            (_make_llm_response(), "s1"),
            (
                ToolCallCompleted(
                    tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=_ts()
                ),
                "s1",
            ),
            (TaskCompleted(task_name="T", duration_ms=2000.0, timestamp=_ts()), "s1"),
        )

        summary = stream.profile().summarize()
        assert len(summary.tasks) == 1
        tp = summary.tasks[0]
        assert tp.task_name == "T"
        assert tp.scope_id == "s1"
        assert tp.status == "completed"
        assert tp.llm_calls == 1
        assert tp.tool_calls == 1
        assert tp.cost_summary.duration_ms == 2000.0

    def test_failed_task(self):
        stream = _stream_with_scope(
            (TaskStarted(task_name="T", scope_id="s1", timestamp=_ts()), "s1"),
            (
                TaskFailed(
                    task_name="T",
                    error="boom",
                    error_type="RuntimeError",
                    phase="execute",
                    last_tool_name="bash",
                    tool_calls_completed=3,
                    duration_ms=500.0,
                    timestamp=_ts(),
                ),
                "s1",
            ),
        )

        tp = stream.profile().summarize().tasks[0]
        assert tp.status == "failed"
        assert tp.error_type == "RuntimeError"
        assert tp.error_phase == "execute"
        assert tp.last_tool_name == "bash"
        assert tp.tool_calls_completed == 3

    def test_keyed_by_scope_id_not_task_name(self):
        """Multiple instances of same task type get separate profiles."""
        stream = _stream_with_scope(
            (TaskStarted(task_name="T", scope_id="s1", timestamp=_ts()), "s1"),
            (_make_llm_response(cost_usd=0.01), "s1"),
            (TaskCompleted(task_name="T", duration_ms=100.0, timestamp=_ts()), "s1"),
            (TaskStarted(task_name="T", scope_id="s2", timestamp=_ts()), "s2"),
            (_make_llm_response(cost_usd=0.02), "s2"),
            (TaskCompleted(task_name="T", duration_ms=200.0, timestamp=_ts()), "s2"),
        )

        summary = stream.profile().summarize()
        assert len(summary.tasks) == 2
        sids = {t.scope_id for t in summary.tasks}
        assert sids == {"s1", "s2"}

    def test_per_task_file_data(self):
        stream = _stream_with_scope(
            (TaskStarted(task_name="T", scope_id="s1", timestamp=_ts()), "s1"),
            (FileRead(path="a.py", timestamp=_ts()), "s1"),
            (FilePatch(path="b.py", old_content="x", new_content="y", timestamp=_ts()), "s1"),
            (TaskCompleted(task_name="T", duration_ms=100.0, timestamp=_ts()), "s1"),
        )

        tp = stream.profile().summarize().tasks[0]
        assert tp.cost_summary.files_read == frozenset({"a.py"})
        assert tp.cost_summary.files_modified == frozenset({"b.py"})

    def test_per_task_time_breakdown(self):
        stream = _stream_with_scope(
            (TaskStarted(task_name="T", scope_id="s1", timestamp=_ts()), "s1"),
            (_make_llm_response(duration_ms=500, duration_api_ms=400), "s1"),
            (LifecyclePhaseCompleted(phase="prepare", duration_ms=50.0, timestamp=_ts()), "s1"),
            (TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()), "s1"),
        )

        tp = stream.profile().summarize().tasks[0]
        assert tp.time_breakdown.llm_wall_ms == 500.0
        assert tp.time_breakdown.llm_api_ms == 400.0
        assert tp.time_breakdown.phase_durations["prepare"] == 50.0

    def test_attribution_via_scope_id(self):
        """Effects with unknown scope_id contribute to global only."""
        stream = _stream_with_scope(
            (TaskStarted(task_name="T", scope_id="s1", timestamp=_ts()), "s1"),
            (_make_llm_response(cost_usd=0.01), "s1"),
            (_make_llm_response(cost_usd=0.02), "unknown"),
            (TaskCompleted(task_name="T", duration_ms=100.0, timestamp=_ts()), "s1"),
        )

        summary = stream.profile().summarize()
        assert summary.cost_summary.llm_calls == 2
        assert summary.cost_summary.cost_usd == pytest.approx(0.03)
        # Only the first LLM call attributed to task
        tp = summary.tasks[0]
        assert tp.llm_calls == 1
        assert tp.cost_summary.cost_usd == pytest.approx(0.01)


# =============================================================================
# TestTaskTree
# =============================================================================


class TestTaskTree:
    """Hierarchical task linking."""

    def test_parent_child_linking(self):
        stream = _stream_with_scope(
            (TaskStarted(task_name="Root", scope_id="s1", parent_scope_id=None, timestamp=_ts()), "s1"),
            (TaskStarted(task_name="Child", scope_id="s2", parent_scope_id="s1", timestamp=_ts()), "s2"),
            (TaskCompleted(task_name="Child", duration_ms=50.0, timestamp=_ts()), "s2"),
            (TaskCompleted(task_name="Root", duration_ms=100.0, timestamp=_ts()), "s1"),
        )

        summary = stream.profile().summarize()
        assert len(summary.task_tree) == 1
        root = summary.task_tree[0]
        assert root.profile.task_name == "Root"
        assert len(root.children) == 1
        assert root.children[0].profile.task_name == "Child"

    def test_multiple_children(self):
        stream = _stream_with_scope(
            (TaskStarted(task_name="Root", scope_id="s1", timestamp=_ts()), "s1"),
            (TaskStarted(task_name="A", scope_id="s2", parent_scope_id="s1", timestamp=_ts()), "s2"),
            (TaskStarted(task_name="B", scope_id="s3", parent_scope_id="s1", timestamp=_ts()), "s3"),
            (TaskCompleted(task_name="Root", duration_ms=100.0, timestamp=_ts()), "s1"),
        )

        root = stream.profile().summarize().task_tree[0]
        assert len(root.children) == 2
        names = {c.profile.task_name for c in root.children}
        assert names == {"A", "B"}

    def test_orphan_tasks_become_roots(self):
        stream = _stream_with_scope(
            (TaskStarted(task_name="Orphan", scope_id="s1", parent_scope_id="nonexistent", timestamp=_ts()), "s1"),
            (TaskCompleted(task_name="Orphan", duration_ms=100.0, timestamp=_ts()), "s1"),
        )

        tree = stream.profile().summarize().task_tree
        assert len(tree) == 1
        assert tree[0].profile.task_name == "Orphan"


# =============================================================================
# TestRecoverySummary
# =============================================================================


class TestRecoverySummary:
    """Failure and recovery aggregation."""

    def test_counts(self):
        stream = Stream()
        stream = stream.append(
            ExecutionFailed(
                error_type="buffer_overflow",
                error_message="too big",
                last_tool_name="bash",
                recoverable=True,
                timestamp=_ts(),
            )
        )
        stream = stream.append(
            RecoveryAttempted(
                original_session_id="sess1",
                error_type="buffer_overflow",
                last_tool_name="bash",
                recovery_strategy="fork_and_retry",
                timestamp=_ts(),
            )
        )

        r = stream.profile().summarize().recovery
        assert r.execution_failures == 1
        assert r.recoveries_attempted == 1

    def test_failure_types_bucketing(self):
        stream = Stream()
        stream = stream.append(
            ExecutionFailed(
                error_type="buffer_overflow",
                error_message="a",
                last_tool_name="bash",
                recoverable=True,
                timestamp=_ts(),
            )
        )
        stream = stream.append(
            ExecutionFailed(
                error_type="timeout",
                error_message="b",
                last_tool_name="curl",
                recoverable=False,
                timestamp=_ts(),
            )
        )
        stream = stream.append(
            ExecutionFailed(
                error_type="buffer_overflow",
                error_message="c",
                last_tool_name="bash",
                recoverable=True,
                timestamp=_ts(),
            )
        )

        r = stream.profile().summarize().recovery
        assert r.failure_types == {"buffer_overflow": 2, "timeout": 1}
        assert r.triggering_tools == {"bash": 2, "curl": 1}

    def test_recovery_strategies(self):
        stream = Stream()
        stream = stream.append(
            RecoveryAttempted(
                original_session_id="s1",
                error_type="e",
                last_tool_name="t",
                recovery_strategy="fork_and_retry",
                timestamp=_ts(),
            )
        )
        stream = stream.append(
            RecoveryAttempted(
                original_session_id="s2",
                error_type="e",
                last_tool_name="t",
                recovery_strategy="fork_and_retry",
                timestamp=_ts(),
            )
        )

        r = stream.profile().summarize().recovery
        assert r.recovery_strategies == {"fork_and_retry": 2}

    def test_empty_when_no_failures(self):
        stream = Stream()
        stream = stream.append(_make_llm_response())

        r = stream.profile().summarize().recovery
        assert r.execution_failures == 0
        assert r.recoveries_attempted == 0
        assert r.failure_types == {}


# =============================================================================
# TestTaskCacheSummary
# =============================================================================


class TestTaskCacheSummary:
    """Task-level cache metrics."""

    def test_hit_store_counts(self):
        stream = Stream()
        stream = stream.append(CacheHit(execution_key="k1", task_name="T", timestamp=_ts()))
        stream = stream.append(CacheStored(execution_key="k2", task_name="T2", size_bytes=1024, timestamp=_ts()))
        stream = stream.append(CacheStored(execution_key="k3", task_name="T3", size_bytes=2048, timestamp=_ts()))

        tc = stream.profile().summarize().task_cache
        assert tc.hits == 1
        assert tc.stores == 2
        assert tc.total_stored_bytes == 3072

    def test_hit_rate(self):
        stream = Stream()
        stream = stream.append(CacheHit(execution_key="k1", task_name="T", timestamp=_ts()))
        stream = stream.append(CacheHit(execution_key="k2", task_name="T", timestamp=_ts()))
        stream = stream.append(CacheStored(execution_key="k3", task_name="T2", size_bytes=100, timestamp=_ts()))

        tc = stream.profile().summarize().task_cache
        # hit_rate = 2 / (2 + 1) = 0.667
        assert tc.hit_rate == pytest.approx(2 / 3)

    def test_hit_rate_none_no_activity(self):
        tc = TaskCacheSummary()
        assert tc.hit_rate is None


# =============================================================================
# TestProfileViewComposition
# =============================================================================


class TestProfileViewComposition:
    """View chaining and reusability."""

    def test_chains_with_by_task(self):
        ts = _ts()
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="A", scope_id="s1", timestamp=ts))
        stream = stream.append(_make_llm_response(task_name="A", cost_usd=0.01, timestamp=ts + 0.1))
        stream = stream.append(TaskCompleted(task_name="A", duration_ms=100.0, timestamp=ts + 0.2))
        stream = stream.append(TaskStarted(task_name="B", scope_id="s2", timestamp=ts + 0.3))
        stream = stream.append(_make_llm_response(task_name="B", cost_usd=0.05, timestamp=ts + 0.4))
        stream = stream.append(TaskCompleted(task_name="B", duration_ms=200.0, timestamp=ts + 0.5))

        # Profile just task A (by_task filters on effect.task_name)
        summary_a = stream.by_task("A").profile().summarize()
        assert summary_a.cost_summary.cost_usd == pytest.approx(0.01)
        assert summary_a.cost_summary.llm_calls == 1

    def test_reusable_multiple_summarize_calls(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(cost_usd=0.01))

        view = stream.profile()
        s1 = view.summarize()
        s2 = view.summarize()
        assert s1.cost_summary.cost_usd == s2.cost_summary.cost_usd


# =============================================================================
# TestFormatProfile
# =============================================================================


class TestFormatProfile:
    """CLI rendering."""

    def test_no_crash_on_empty(self):
        summary = Stream().profile().summarize()
        text = format_profile(summary)
        assert "Execution Profile" in text

    def test_output_includes_sections(self):
        ts = _ts()
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T", scope_id="s1", timestamp=ts))
        stream = stream.append(_make_llm_response(timestamp=ts + 0.1))
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=ts + 0.2)
        )
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=ts + 0.3))

        summary = stream.profile().summarize()
        text = format_profile(summary)
        assert "Execution Profile" in text
        assert "Time Breakdown" in text
        assert "Models" in text
        assert "Tools" in text
        assert "Tasks" in text

    def test_errors_section_when_present(self):
        stream = Stream()
        stream = stream.append(
            ExecutionFailed(
                error_type="buffer_overflow",
                error_message="big",
                last_tool_name="bash",
                recoverable=True,
                timestamp=_ts(),
            )
        )

        text = format_profile(stream.profile().summarize())
        assert "Errors & Recovery" in text

    def test_errors_section_omitted_when_empty(self):
        stream = Stream()
        stream = stream.append(_make_llm_response())
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=100.0, timestamp=_ts()))

        text = format_profile(stream.profile().summarize())
        assert "Errors & Recovery" not in text

    def test_task_cache_section_when_present(self):
        stream = Stream()
        stream = stream.append(CacheHit(execution_key="k", task_name="T", timestamp=_ts()))

        text = format_profile(stream.profile().summarize())
        assert "Task Cache" in text

    def test_task_cache_section_omitted_when_empty(self):
        stream = Stream()
        stream = stream.append(_make_llm_response())

        text = format_profile(stream.profile().summarize())
        assert "Task Cache" not in text

    def test_toggle_show_tree_false(self):
        ts = _ts()
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T", scope_id="s1", timestamp=ts))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=100.0, timestamp=ts + 0.1))

        text = format_profile(stream.profile().summarize(), show_tree=False)
        assert "Tasks" not in text

    def test_toggle_show_tools_false(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=_ts())
        )
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()))

        text = format_profile(stream.profile().summarize(), show_tools=False)
        assert "Tools" not in text

    def test_toggle_show_bar_chart_false(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(duration_ms=700, duration_api_ms=600))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()))

        text = format_profile(stream.profile().summarize(), show_bar_chart=False)
        assert "\u2588" not in text  # no bar chart blocks

    def test_toggle_show_turn_detail_false(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(duration_ms=700, duration_api_ms=600))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()))

        text = format_profile(stream.profile().summarize(), show_turn_detail=False)
        assert "API wait" not in text

    def test_execute_phase_excluded_from_framework(self):
        stream = Stream()
        stream = stream.append(LifecyclePhaseCompleted(phase="prepare", duration_ms=100.0, timestamp=_ts()))
        stream = stream.append(LifecyclePhaseCompleted(phase="execute", duration_ms=800.0, timestamp=_ts()))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=_ts()))

        text = format_profile(stream.profile().summarize())
        # "execute" should not appear in the framework phase detail
        lines = text.split("\n")
        framework_idx = None
        for i, line in enumerate(lines):
            if "Framework" in line:
                framework_idx = i
                break
        # After Framework line, "execute" should not appear as a sub-item
        if framework_idx is not None:
            phase_lines = []
            for line in lines[framework_idx + 1 :]:
                if line.startswith("    "):
                    phase_lines.append(line)
                else:
                    break
            phase_text = "\n".join(phase_lines)
            assert "execute" not in phase_text


# =============================================================================
# TestCostSummaryEquivalence
# =============================================================================


class TestCostSummaryEquivalence:
    """ProfileView and CostsView produce equivalent CostSummary."""

    def _assert_equivalent(self, stream: Stream) -> None:
        profile_cs = stream.profile().summarize().cost_summary
        costs_cs = stream.costs().summarize()
        assert profile_cs.tool_calls == costs_cs.tool_calls
        assert profile_cs.tool_calls_rejected == costs_cs.tool_calls_rejected
        assert profile_cs.files_created == costs_cs.files_created
        assert profile_cs.files_deleted == costs_cs.files_deleted
        assert profile_cs.files_read == costs_cs.files_read
        assert profile_cs.files_modified == costs_cs.files_modified
        assert profile_cs.input_tokens == costs_cs.input_tokens
        assert profile_cs.output_tokens == costs_cs.output_tokens
        assert profile_cs.total_tokens == costs_cs.total_tokens
        assert profile_cs.llm_calls == costs_cs.llm_calls
        assert profile_cs.tool_duration_ms == pytest.approx(costs_cs.tool_duration_ms)
        assert profile_cs.duration_ms == costs_cs.duration_ms
        # cost_usd: both None or both equal
        if costs_cs.cost_usd is None:
            assert profile_cs.cost_usd is None
        else:
            assert profile_cs.cost_usd == pytest.approx(costs_cs.cost_usd)
        # timestamps
        assert profile_cs.start_time == costs_cs.start_time
        assert profile_cs.end_time == costs_cs.end_time

    def test_empty_stream(self):
        self._assert_equivalent(Stream())

    def test_llm_effects_only(self):
        stream = Stream()
        stream = stream.append(_make_llm_response(cost_usd=0.01))
        stream = stream.append(_make_llm_response(cost_usd=None))
        self._assert_equivalent(stream)

    def test_tool_effects_only(self):
        stream = Stream()
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=_ts())
        )
        stream = stream.append(ToolCallRejected(tool_name="read", tool_call_id="tc2", reason="no", timestamp=_ts()))
        self._assert_equivalent(stream)

    def test_file_effects(self):
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", timestamp=_ts()))
        stream = stream.append(FilePatch(path="b.py", old_content="x", new_content="y", timestamp=_ts()))
        stream = stream.append(FileCreate(path="c.py", timestamp=_ts()))
        stream = stream.append(FileDelete(path="d.py", timestamp=_ts()))
        self._assert_equivalent(stream)

    def test_mixed_stream(self):
        ts = _ts()
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T", scope_id="s1", timestamp=ts))
        stream = stream.append(_make_llm_response(cost_usd=0.01, timestamp=ts + 0.1))
        stream = stream.append(
            ToolCallCompleted(tool_name="bash", tool_call_id="tc1", success=True, duration_ms=100.0, timestamp=ts + 0.2)
        )
        stream = stream.append(FileRead(path="a.py", timestamp=ts + 0.3))
        stream = stream.append(FilePatch(path="b.py", old_content="x", new_content="y", timestamp=ts + 0.4))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=500.0, timestamp=ts + 0.5))
        self._assert_equivalent(stream)


# =============================================================================
# TestToProfile
# =============================================================================


class TestToProfile:
    """stream.to_profile() convenience method."""

    def test_returns_same_as_format_profile(self):
        ts = _ts()
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T", scope_id="s1", timestamp=ts))
        stream = stream.append(_make_llm_response(timestamp=ts + 0.1))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=ts + 0.2))

        via_method = stream.to_profile()
        via_function = format_profile(stream.profile().summarize())
        assert via_method == via_function

    def test_toggle_kwargs_pass_through(self):
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T", scope_id="s1", timestamp=_ts()))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=100.0, timestamp=_ts()))

        text = stream.to_profile(show_tree=False)
        assert "Tasks" not in text


# =============================================================================
# TestVerboseProfileIntegration
# =============================================================================


class TestVerboseProfileIntegration:
    """VerboseConfig.show_profile integration with VerboseFormatter."""

    def test_show_profile_true_renders(self):
        import io

        from shepherd_providers.verbose import VerboseConfig, VerboseFormatter

        ts = _ts()
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T", scope_id="s1", timestamp=ts))
        stream = stream.append(_make_llm_response(timestamp=ts + 0.1))
        stream = stream.append(TaskCompleted(task_name="T", duration_ms=1000.0, timestamp=ts + 0.2))

        buf = io.StringIO()
        config = VerboseConfig(enabled=True, show_profile=True, output=buf)
        formatter = VerboseFormatter(config, effects_stream=stream)
        formatter.finalize()

        output = buf.getvalue()
        assert "Execution Profile" in output

    def test_show_profile_false_no_output(self):
        import io

        from shepherd_providers.verbose import VerboseConfig, VerboseFormatter

        stream = Stream()
        stream = stream.append(_make_llm_response())

        buf = io.StringIO()
        config = VerboseConfig(enabled=True, show_profile=False, output=buf)
        formatter = VerboseFormatter(config, effects_stream=stream)
        formatter.finalize()

        output = buf.getvalue()
        assert "Execution Profile" not in output

    def test_show_profile_no_stream_no_crash(self):
        import io

        from shepherd_providers.verbose import VerboseConfig, VerboseFormatter

        buf = io.StringIO()
        config = VerboseConfig(enabled=True, show_profile=True, output=buf)
        formatter = VerboseFormatter(config)  # no effects_stream
        formatter.finalize()  # should not crash
