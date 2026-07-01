"""Tests for stage profiling fixes (Gaps A, B, C).

Gap A: Skipped/defaulted stages now visible in ProfileSummary.stages
Gap B: stage_name on TaskStarted/TaskCompleted/TaskFailed enables retry grouping
Gap C: stage_overhead_ms computed from StageCompleted envelope vs task duration
"""

import pytest
from shepherd_core.effects import (
    LLMResponseReceived,
    StageCompleted,
    StageSkipped,
    StageStarted,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    ToolCallCompleted,
)
from shepherd_core.scope.stream import EffectLayer, Stream

# =============================================================================
# Helpers
# =============================================================================


def _stream_with_scope(*pairs: tuple) -> Stream:
    """Build a stream from (effect, scope_id) pairs."""
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


# =============================================================================
# Gap A: Skipped/defaulted stages visible in ProfileSummary
# =============================================================================


class TestGapA_SkippedStagesVisible:
    """StageSkipped and StageCompleted(defaulted/partial) now appear
    in ProfileSummary.stages."""

    def test_skipped_stage_in_summary(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (StageStarted(stage_name="optional", task_name="Pipeline"), "p"),
            (StageSkipped(stage_name="optional", task_name="Pipeline", reason="data empty"), "p"),
            (TaskCompleted(duration_ms=50.0), "p"),
        )
        summary = stream.profile().summarize()

        assert len(summary.stages) == 1
        sr = summary.stages[0]
        assert sr.stage_name == "optional"
        assert sr.pipeline_task_name == "Pipeline"
        assert sr.status == "skipped"
        assert sr.reason == "data empty"
        assert sr.duration_ms is None

    def test_defaulted_stage_in_summary(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (StageStarted(stage_name="enrich", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="EnrichTask", parent_scope_id="p"), "s1"),
            (TaskFailed(duration_ms=500.0, error="API down", error_type="ConnectionError"), "s1"),
            (StageCompleted(stage_name="enrich", task_name="Pipeline", duration_ms=510.0, defaulted=True), "p"),
            (TaskCompleted(duration_ms=600.0), "p"),
        )
        summary = stream.profile().summarize()

        assert len(summary.stages) == 1
        sr = summary.stages[0]
        assert sr.stage_name == "enrich"
        assert sr.status == "defaulted"
        assert sr.duration_ms == pytest.approx(510.0)

    def test_partial_stage_in_summary(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (StageCompleted(stage_name="step", task_name="Pipeline", duration_ms=50.0, partial=True), "p"),
            (TaskCompleted(duration_ms=100.0), "p"),
        )
        summary = stream.profile().summarize()

        assert len(summary.stages) == 1
        assert summary.stages[0].status == "partial"
        assert summary.stages[0].duration_ms == pytest.approx(50.0)

    def test_normal_stage_not_in_stages_list(self):
        """Normal (non-defaulted, non-partial) StageCompleted should NOT
        produce a StageRecord — the subtask's TaskProfile is sufficient."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (StageStarted(stage_name="extract", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="Extract", parent_scope_id="p"), "s1"),
            (TaskCompleted(duration_ms=1500.0), "s1"),
            (StageCompleted(stage_name="extract", task_name="Pipeline", duration_ms=1600.0), "p"),
            (TaskCompleted(duration_ms=2000.0), "p"),
        )
        summary = stream.profile().summarize()
        assert len(summary.stages) == 0

    def test_multiple_stage_types_in_summary(self):
        """Mix of skipped, defaulted, and normal stages."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            # Normal stage (no StageRecord)
            (StageStarted(stage_name="extract", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="Extract", parent_scope_id="p"), "s1"),
            (TaskCompleted(duration_ms=100.0), "s1"),
            (StageCompleted(stage_name="extract", task_name="Pipeline", duration_ms=110.0), "p"),
            # Skipped stage
            (StageStarted(stage_name="transform", task_name="Pipeline"), "p"),
            (StageSkipped(stage_name="transform", task_name="Pipeline", reason="disabled"), "p"),
            # Defaulted stage
            (StageStarted(stage_name="validate", task_name="Pipeline"), "p"),
            (StageCompleted(stage_name="validate", task_name="Pipeline", duration_ms=50.0, defaulted=True), "p"),
            (TaskCompleted(duration_ms=300.0), "p"),
        )
        summary = stream.profile().summarize()

        assert len(summary.stages) == 2
        statuses = {sr.stage_name: sr.status for sr in summary.stages}
        assert statuses["transform"] == "skipped"
        assert statuses["validate"] == "defaulted"

    def test_empty_pipeline_no_stages(self):
        """No stage effects → empty stages tuple."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="s1", task_name="SimpleTask"), "s1"),
            (TaskCompleted(duration_ms=100.0), "s1"),
        )
        summary = stream.profile().summarize()
        assert summary.stages == ()


# =============================================================================
# Gap B: stage_name on task effects enables retry grouping
# =============================================================================


class TestGapB_StageNameOnTaskEffects:
    """TaskStarted/TaskCompleted/TaskFailed carry stage_name."""

    def test_stage_name_field_exists(self):
        e = TaskStarted(scope_id="s1", stage_name="extract")
        assert e.stage_name == "extract"

    def test_stage_name_default_none(self):
        e = TaskStarted(scope_id="s1")
        assert e.stage_name is None

    def test_stage_name_on_completed(self):
        e = TaskCompleted(stage_name="extract", duration_ms=100.0)
        assert e.stage_name == "extract"

    def test_stage_name_on_failed(self):
        e = TaskFailed(stage_name="extract", error="boom")
        assert e.stage_name == "extract"

    def test_backward_compat_deserialization(self):
        """Old serialized data without stage_name still works."""
        old_data = {"effect_type": "task_started", "scope_id": "s1", "inputs": {}}
        e = TaskStarted.model_validate(old_data)
        assert e.stage_name is None

    def test_stage_name_survives_round_trip(self):
        e = TaskStarted(scope_id="s1", stage_name="extract")
        dumped = e.model_dump()
        restored = TaskStarted.model_validate(dumped)
        assert restored.stage_name == "extract"

    def test_retry_attempts_grouped_by_stage_name(self):
        """With stage_name, retry attempts can be distinguished from
        different stages using the same task class."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            # Stage "flaky" attempt 1 (fails)
            (TaskStarted(scope_id="c1", task_name="FlakyTask", parent_scope_id="p", stage_name="flaky"), "c1"),
            (TaskFailed(duration_ms=50.0, error="transient", error_type="TimeoutError", stage_name="flaky"), "c1"),
            # Stage "flaky" attempt 2 (succeeds)
            (TaskStarted(scope_id="c2", task_name="FlakyTask", parent_scope_id="p", stage_name="flaky"), "c2"),
            (TaskCompleted(duration_ms=80.0, stage_name="flaky"), "c2"),
            # Stage "other" uses same task class
            (TaskStarted(scope_id="c3", task_name="FlakyTask", parent_scope_id="p", stage_name="other"), "c3"),
            (TaskCompleted(duration_ms=40.0, stage_name="other"), "c3"),
            (TaskCompleted(duration_ms=300.0), "p"),
        )
        summary = stream.profile().summarize()

        flaky_stage = [t for t in summary.tasks if t.stage_name == "flaky"]
        other_stage = [t for t in summary.tasks if t.stage_name == "other"]

        assert len(flaky_stage) == 2  # c1 (failed) + c2 (success)
        assert len(other_stage) == 1  # c3
        assert flaky_stage[0].status == "failed"
        assert flaky_stage[1].status == "completed"

    def test_stage_name_on_task_profile(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="Extract", parent_scope_id="p", stage_name="extract"), "s1"),
            (TaskCompleted(duration_ms=100.0, stage_name="extract"), "s1"),
            (TaskCompleted(duration_ms=200.0), "p"),
        )
        summary = stream.profile().summarize()
        extract = next(t for t in summary.tasks if t.task_name == "Extract")
        assert extract.stage_name == "extract"

        pipeline = next(t for t in summary.tasks if t.task_name == "Pipeline")
        assert pipeline.stage_name is None  # Pipeline itself has no stage


# =============================================================================
# Gap C: Stage overhead attribution
# =============================================================================


class TestGapC_StageOverhead:
    """stage_overhead_ms computed from StageCompleted envelope minus task duration."""

    def test_stage_overhead_computed(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (StageStarted(stage_name="extract", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="Extract", parent_scope_id="p", stage_name="extract"), "s1"),
            (TaskCompleted(duration_ms=1500.0, stage_name="extract"), "s1"),
            (StageCompleted(stage_name="extract", task_name="Pipeline", duration_ms=1600.0), "p"),
            (TaskCompleted(duration_ms=2000.0), "p"),
        )
        summary = stream.profile().summarize()
        extract = next(t for t in summary.tasks if t.task_name == "Extract")
        assert extract.stage_overhead_ms == pytest.approx(100.0)

    def test_no_overhead_for_non_stage_tasks(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="s1", task_name="Standalone"), "s1"),
            (TaskCompleted(duration_ms=100.0), "s1"),
        )
        summary = stream.profile().summarize()
        assert summary.tasks[0].stage_overhead_ms is None

    def test_no_overhead_when_no_stage_completed(self):
        """If StageCompleted is missing (e.g., fatal failure), overhead is None."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="Extract", parent_scope_id="p", stage_name="extract"), "s1"),
            (TaskCompleted(duration_ms=100.0, stage_name="extract"), "s1"),
            # No StageCompleted because pipeline failed
            (TaskFailed(duration_ms=200.0, error="boom"), "p"),
        )
        summary = stream.profile().summarize()
        extract = next(t for t in summary.tasks if t.task_name == "Extract")
        assert extract.stage_overhead_ms is None

    def test_overhead_zero_when_identical_durations(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="Fast", parent_scope_id="p", stage_name="fast"), "s1"),
            (TaskCompleted(duration_ms=100.0, stage_name="fast"), "s1"),
            (StageCompleted(stage_name="fast", task_name="Pipeline", duration_ms=100.0), "p"),
            (TaskCompleted(duration_ms=200.0), "p"),
        )
        summary = stream.profile().summarize()
        fast = next(t for t in summary.tasks if t.task_name == "Fast")
        assert fast.stage_overhead_ms == pytest.approx(0.0)

    def test_overhead_with_retry(self):
        """When retries happen, StageCompleted covers all attempts.
        Only the last (successful) subtask gets overhead attributed."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            # Attempt 1 fails (50ms)
            (TaskStarted(scope_id="c1", task_name="Task", parent_scope_id="p", stage_name="flaky"), "c1"),
            (TaskFailed(duration_ms=50.0, stage_name="flaky"), "c1"),
            # Attempt 2 succeeds (80ms)
            (TaskStarted(scope_id="c2", task_name="Task", parent_scope_id="p", stage_name="flaky"), "c2"),
            (TaskCompleted(duration_ms=80.0, stage_name="flaky"), "c2"),
            # Stage envelope covers all attempts (150ms total)
            (StageCompleted(stage_name="flaky", task_name="Pipeline", duration_ms=150.0), "p"),
            (TaskCompleted(duration_ms=200.0), "p"),
        )
        summary = stream.profile().summarize()

        failed = next(t for t in summary.tasks if t.task_name == "Task" and t.status == "failed")
        succeeded = next(t for t in summary.tasks if t.task_name == "Task" and t.status == "completed")

        # Failed attempt: overhead is 150 - 50 = 100 (includes retry gap + second attempt)
        assert failed.stage_overhead_ms == pytest.approx(100.0)
        # Succeeded attempt: overhead is 150 - 80 = 70 (includes first attempt + retry gap)
        assert succeeded.stage_overhead_ms == pytest.approx(70.0)

    def test_overhead_cross_pipeline_isolation(self):
        """Two pipelines with same stage name don't interfere."""
        stream = _stream_with_scope(
            # Pipeline 1
            (TaskStarted(scope_id="p1", task_name="PipeA"), "p1"),
            (TaskStarted(scope_id="s1", task_name="Task", parent_scope_id="p1", stage_name="step"), "s1"),
            (TaskCompleted(duration_ms=100.0, stage_name="step"), "s1"),
            (StageCompleted(stage_name="step", task_name="PipeA", duration_ms=120.0), "p1"),
            (TaskCompleted(duration_ms=200.0), "p1"),
            # Pipeline 2
            (TaskStarted(scope_id="p2", task_name="PipeB"), "p2"),
            (TaskStarted(scope_id="s2", task_name="Task", parent_scope_id="p2", stage_name="step"), "s2"),
            (TaskCompleted(duration_ms=500.0, stage_name="step"), "s2"),
            (StageCompleted(stage_name="step", task_name="PipeB", duration_ms=600.0), "p2"),
            (TaskCompleted(duration_ms=700.0), "p2"),
        )
        summary = stream.profile().summarize()

        s1_task = next(t for t in summary.tasks if t.scope_id == "s1")
        s2_task = next(t for t in summary.tasks if t.scope_id == "s2")

        # Each gets its own overhead from its own pipeline's StageCompleted
        assert s1_task.stage_overhead_ms == pytest.approx(20.0)
        assert s2_task.stage_overhead_ms == pytest.approx(100.0)


# =============================================================================
# Regression: existing profiling unaffected
# =============================================================================


class TestRegression_ExistingProfilingUnaffected:
    """Verify the new code doesn't break any existing ProfileSummary behavior."""

    def test_existing_profile_fields_unchanged(self):
        """A simple task without stages produces the same ProfileSummary shape."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="s1", task_name="Simple"), "s1"),
            (
                LLMResponseReceived(
                    model_id="opus",
                    input_tokens=100,
                    output_tokens=50,
                    total_tokens=150,
                    duration_ms=500.0,
                    duration_api_ms=400.0,
                    cost_usd=0.01,
                    num_turns=1,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                ),
                "s1",
            ),
            (ToolCallCompleted(tool_name="bash", duration_ms=100.0, success=True), "s1"),
            (TaskCompleted(duration_ms=700.0), "s1"),
        )
        summary = stream.profile().summarize()

        assert len(summary.tasks) == 1
        tp = summary.tasks[0]
        assert tp.task_name == "Simple"
        assert tp.stage_name is None
        assert tp.stage_overhead_ms is None
        assert tp.cost_summary.total_tokens == 150
        assert tp.cost_summary.duration_ms == 700.0
        assert tp.llm_calls == 1
        assert tp.tool_calls == 1
        assert summary.stages == ()

    def test_task_tree_still_works(self):
        """task_tree hierarchy is preserved."""
        stream = _stream_with_scope(
            (TaskStarted(scope_id="p", task_name="Pipeline"), "p"),
            (TaskStarted(scope_id="s1", task_name="Sub1", parent_scope_id="p", stage_name="step1"), "s1"),
            (TaskCompleted(duration_ms=100.0, stage_name="step1"), "s1"),
            (TaskStarted(scope_id="s2", task_name="Sub2", parent_scope_id="p", stage_name="step2"), "s2"),
            (TaskCompleted(duration_ms=200.0, stage_name="step2"), "s2"),
            (TaskCompleted(duration_ms=500.0), "p"),
        )
        summary = stream.profile().summarize()

        assert len(summary.task_tree) == 1
        root = summary.task_tree[0]
        assert root.profile.task_name == "Pipeline"
        child_names = {c.profile.task_name for c in root.children}
        assert child_names == {"Sub1", "Sub2"}

    def test_tasks_by_device_still_works(self):
        stream = _stream_with_scope(
            (TaskStarted(scope_id="s1", task_name="T1", device_name="container"), "s1"),
            (TaskCompleted(duration_ms=100.0), "s1"),
            (TaskStarted(scope_id="s2", task_name="T2", device_name="local"), "s2"),
            (TaskCompleted(duration_ms=50.0), "s2"),
        )
        summary = stream.profile().summarize()
        by_device = summary.tasks_by_device
        assert "container" in by_device
        assert "local" in by_device
