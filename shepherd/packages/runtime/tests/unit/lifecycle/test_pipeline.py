"""Tests for LifecyclePipeline.

Covers:
- Basic execution (run_until, run_remaining)
- Phase validation
- Rollback on failure (automatic and manual)
- Double-rollback prevention
- Effect emission (started, completed, failed)
- Timing recording
- State management (update_context, current_context)
- Recovery after failure (can still run cleanup)
"""

from unittest.mock import MagicMock

import pytest
from shepherd_runtime._lifecycle import LifecyclePipeline, PhaseBase, PhaseContext

# =============================================================================
# Test Fixtures
# =============================================================================


class MockPhase(PhaseBase):
    """Configurable mock phase for testing."""

    def __init__(
        self,
        name: str,
        *,
        fail: bool = False,
        fail_on_rollback: bool = False,
        track_calls: list | None = None,
    ):
        self._name = name
        self._fail = fail
        self._fail_on_rollback = fail_on_rollback
        self._track_calls = track_calls if track_calls is not None else []

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        self._track_calls.append(("execute", self._name))
        if self._fail:
            raise RuntimeError(f"Phase {self._name} failed")
        return ctx

    async def rollback(self, ctx: PhaseContext, error: Exception) -> PhaseContext:
        self._track_calls.append(("rollback", self._name))
        if self._fail_on_rollback:
            raise RuntimeError(f"Rollback {self._name} failed")
        return ctx.mark_cleaned_up(f"mock-{self._name}")


@pytest.fixture
def mock_scope() -> MagicMock:
    """Create a mock scope."""
    scope = MagicMock()
    scope.emit = MagicMock()
    return scope


@pytest.fixture
def mock_provider() -> MagicMock:
    """Create a mock provider."""
    provider = MagicMock()
    provider.provider_id = "test-provider"
    return provider


@pytest.fixture
def mock_emitter() -> MagicMock:
    """Create a mock effect emitter."""
    emitter = MagicMock()
    emitter.emit = MagicMock()
    emitter.emit_phase_started = MagicMock()
    emitter.emit_phase_completed = MagicMock()
    emitter.emit_phase_failed = MagicMock()
    return emitter


@pytest.fixture
def basic_context(mock_scope: MagicMock, mock_provider: MagicMock) -> PhaseContext:
    """Create a basic PhaseContext for testing."""
    return PhaseContext(
        scope=mock_scope,
        provider=mock_provider,
        task_name="test-task",
        prompt="Test prompt",
    )


# =============================================================================
# Tests: Basic Execution
# =============================================================================


class TestRunUntil:
    """Tests for run_until() method."""

    @pytest.mark.asyncio
    async def test_executes_phases_in_order(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """run_until should execute phases in order."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", track_calls=calls),
            MockPhase("execute", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="execute")

        assert calls == [
            ("execute", "configure"),
            ("execute", "prepare"),
            ("execute", "execute"),
        ]

    @pytest.mark.asyncio
    async def test_stops_at_specified_phase(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """run_until should stop after the specified phase."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", track_calls=calls),
            MockPhase("execute", track_calls=calls),
            MockPhase("cleanup", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="prepare")

        assert calls == [
            ("execute", "configure"),
            ("execute", "prepare"),
        ]
        assert pipeline.completed_phase_names == ["configure", "prepare"]

    @pytest.mark.asyncio
    async def test_continues_from_previous_position(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """run_until should continue from where it left off."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", track_calls=calls),
            MockPhase("execute", track_calls=calls),
            MockPhase("cleanup", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        # First call - run through prepare
        ctx = await pipeline.run_until(basic_context, stop_after="prepare")

        # Second call - continue to execute
        await pipeline.run_until(ctx, stop_after="execute")

        assert calls == [
            ("execute", "configure"),
            ("execute", "prepare"),
            ("execute", "execute"),
        ]
        assert pipeline.completed_phase_names == ["configure", "prepare", "execute"]

    @pytest.mark.asyncio
    async def test_raises_on_invalid_phase_name(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """run_until should raise ValueError for unknown phase name."""
        phases = [MockPhase("configure"), MockPhase("prepare")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        with pytest.raises(ValueError) as exc_info:
            await pipeline.run_until(basic_context, stop_after="nonexistent")

        assert "nonexistent" in str(exc_info.value)
        assert "configure" in str(exc_info.value)
        assert "prepare" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_returns_updated_context(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """run_until should return updated context."""
        phases = [MockPhase("configure")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        result = await pipeline.run_until(basic_context, stop_after="configure")

        assert result is not None
        assert "configure" in result.phase_timings


class TestRunRemaining:
    """Tests for run_remaining() method."""

    @pytest.mark.asyncio
    async def test_runs_all_remaining_phases(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """run_remaining should complete all phases."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", track_calls=calls),
            MockPhase("cleanup", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        # Run first phase
        ctx = await pipeline.run_until(basic_context, stop_after="configure")

        # Run remaining
        await pipeline.run_remaining()

        assert calls == [
            ("execute", "configure"),
            ("execute", "prepare"),
            ("execute", "cleanup"),
        ]

    @pytest.mark.asyncio
    async def test_raises_without_context(self, mock_emitter: MagicMock) -> None:
        """run_remaining should raise if no context set."""
        phases = [MockPhase("configure")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        with pytest.raises(RuntimeError) as exc_info:
            await pipeline.run_remaining()

        assert "No context available" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handles_empty_pipeline(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """run_remaining should handle empty pipeline."""
        pipeline = LifecyclePipeline(phases=[], emitter=mock_emitter)
        pipeline._current_ctx = basic_context

        result = await pipeline.run_remaining()

        assert result is basic_context


# =============================================================================
# Tests: Rollback on Failure
# =============================================================================


class TestRollbackOnFailure:
    """Tests for automatic rollback when phases fail."""

    @pytest.mark.asyncio
    async def test_rollback_on_phase_failure(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Failure should trigger rollback of completed phases."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", track_calls=calls),
            MockPhase("execute", fail=True, track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="execute")

        # Verify rollback happened in reverse order
        assert calls == [
            ("execute", "configure"),
            ("execute", "prepare"),
            ("execute", "execute"),  # Failed
            ("rollback", "prepare"),
            ("rollback", "configure"),
        ]

    @pytest.mark.asyncio
    async def test_context_has_error_after_failure(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Context should have error set after failure."""
        phases = [
            MockPhase("configure"),
            MockPhase("execute", fail=True),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="execute")

        assert pipeline.current_context is not None
        assert pipeline.current_context.has_error

    @pytest.mark.asyncio
    async def test_rollback_continues_on_rollback_failure(
        self, basic_context: PhaseContext, mock_emitter: MagicMock
    ) -> None:
        """Rollback should continue even if a phase rollback fails."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", fail_on_rollback=True, track_calls=calls),
            MockPhase("execute", fail=True, track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="execute")

        # Both rollbacks should be attempted
        assert ("rollback", "prepare") in calls
        assert ("rollback", "configure") in calls


class TestRollbackAll:
    """Tests for rollback_all() method."""

    @pytest.mark.asyncio
    async def test_rollback_all_for_external_error(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """rollback_all should rollback completed phases."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        # Run phases successfully
        await pipeline.run_until(basic_context, stop_after="prepare")

        # External error triggers rollback
        error = RuntimeError("External error")
        await pipeline.rollback_all(error)

        assert ("rollback", "prepare") in calls
        assert ("rollback", "configure") in calls

    @pytest.mark.asyncio
    async def test_rollback_all_returns_none_without_context(self, mock_emitter: MagicMock) -> None:
        """rollback_all should return None if no context."""
        pipeline = LifecyclePipeline(phases=[], emitter=mock_emitter)

        result = await pipeline.rollback_all(RuntimeError("test"))

        assert result is None

    @pytest.mark.asyncio
    async def test_rollback_all_sets_error_in_context(
        self, basic_context: PhaseContext, mock_emitter: MagicMock
    ) -> None:
        """rollback_all should set error in context."""
        phases = [MockPhase("configure")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)
        await pipeline.run_until(basic_context, stop_after="configure")

        error = RuntimeError("External error")
        await pipeline.rollback_all(error)

        assert pipeline.current_context.error is error


# =============================================================================
# Tests: Double-Rollback Prevention
# =============================================================================


class TestDoubleRollbackPrevention:
    """Tests for preventing double-rollback."""

    @pytest.mark.asyncio
    async def test_rollback_all_is_noop_after_automatic_rollback(
        self, basic_context: PhaseContext, mock_emitter: MagicMock
    ) -> None:
        """rollback_all should be no-op if rollback already happened."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("execute", fail=True, track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        # run_until triggers automatic rollback
        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="execute")

        rollback_count_before = calls.count(("rollback", "configure"))

        # rollback_all should be no-op
        await pipeline.rollback_all(RuntimeError("another error"))

        rollback_count_after = calls.count(("rollback", "configure"))
        assert rollback_count_after == rollback_count_before

    @pytest.mark.asyncio
    async def test_rollback_all_is_noop_on_second_call(
        self, basic_context: PhaseContext, mock_emitter: MagicMock
    ) -> None:
        """Calling rollback_all twice should only rollback once."""
        calls: list = []
        phases = [MockPhase("configure", track_calls=calls)]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="configure")

        # First rollback
        await pipeline.rollback_all(RuntimeError("error1"))
        rollback_count = calls.count(("rollback", "configure"))

        # Second rollback should be no-op
        await pipeline.rollback_all(RuntimeError("error2"))

        assert calls.count(("rollback", "configure")) == rollback_count

    @pytest.mark.asyncio
    async def test_is_rollback_completed_property(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """is_rollback_completed should track rollback state."""
        phases = [MockPhase("configure")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="configure")

        assert not pipeline.is_rollback_completed

        await pipeline.rollback_all(RuntimeError("test"))

        assert pipeline.is_rollback_completed


# =============================================================================
# Tests: Effect Emission
# =============================================================================


class TestEffectEmission:
    """Tests for effect emission during phase execution."""

    @pytest.mark.asyncio
    async def test_emits_phase_started(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Should emit LifecyclePhaseStarted before each phase."""
        phases = [MockPhase("configure"), MockPhase("prepare")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="prepare")

        assert mock_emitter.emit_phase_started.call_count == 2
        calls = mock_emitter.emit_phase_started.call_args_list
        assert calls[0][1]["phase"] == "configure"
        assert calls[1][1]["phase"] == "prepare"

    @pytest.mark.asyncio
    async def test_emits_phase_completed_on_success(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Should emit LifecyclePhaseCompleted on success."""
        phases = [MockPhase("configure")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="configure")

        mock_emitter.emit_phase_completed.assert_called_once()
        call_kwargs = mock_emitter.emit_phase_completed.call_args[1]
        assert call_kwargs["phase"] == "configure"
        assert call_kwargs["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_emits_phase_failed_on_failure(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Should emit LifecyclePhaseFailed on failure."""
        phases = [MockPhase("execute", fail=True)]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="execute")

        mock_emitter.emit_phase_failed.assert_called_once()
        call_kwargs = mock_emitter.emit_phase_failed.call_args[1]
        assert call_kwargs["phase"] == "execute"
        assert call_kwargs["error_type"] == "RuntimeError"
        assert "execute failed" in call_kwargs["error_message"]


# =============================================================================
# Tests: Timing
# =============================================================================


class TestTiming:
    """Tests for phase timing recording."""

    @pytest.mark.asyncio
    async def test_records_phase_timing(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Phase timing should be recorded in context."""
        phases = [MockPhase("configure"), MockPhase("prepare")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        result = await pipeline.run_until(basic_context, stop_after="prepare")

        assert "configure" in result.phase_timings
        assert "prepare" in result.phase_timings
        assert result.phase_timings["configure"] >= 0
        assert result.phase_timings["prepare"] >= 0


# =============================================================================
# Tests: State Management
# =============================================================================


class TestStateManagement:
    """Tests for pipeline state management."""

    @pytest.mark.asyncio
    async def test_update_context(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """update_context should update internal state."""
        pipeline = LifecyclePipeline(phases=[], emitter=mock_emitter)

        pipeline.update_context(basic_context)

        assert pipeline.current_context is basic_context

    @pytest.mark.asyncio
    async def test_current_context_tracks_latest(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """current_context should reflect latest context."""
        phases = [MockPhase("configure")]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        assert pipeline.current_context is None

        result = await pipeline.run_until(basic_context, stop_after="configure")

        assert pipeline.current_context is result

    @pytest.mark.asyncio
    async def test_completed_phase_names(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """completed_phase_names should track completed phases."""
        phases = [
            MockPhase("configure"),
            MockPhase("prepare"),
            MockPhase("execute"),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        assert pipeline.completed_phase_names == []

        await pipeline.run_until(basic_context, stop_after="prepare")

        assert pipeline.completed_phase_names == ["configure", "prepare"]


# =============================================================================
# Tests: Recovery After Failure
# =============================================================================


class TestRecoveryAfterFailure:
    """Tests for running cleanup after failure."""

    @pytest.mark.asyncio
    async def test_can_run_cleanup_after_failure(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Should be able to run cleanup phase after failure and rollback."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("prepare", track_calls=calls),
            MockPhase("execute", fail=True, track_calls=calls),
            MockPhase("cleanup", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        # Run until execute (which fails)
        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="execute")

        # Now run cleanup - should work because _advance_to_cleanup was called
        await pipeline.run_until(pipeline.current_context, stop_after="cleanup")

        assert ("execute", "cleanup") in calls

    @pytest.mark.asyncio
    async def test_advance_to_cleanup_skips_failed_phases(
        self, basic_context: PhaseContext, mock_emitter: MagicMock
    ) -> None:
        """After failure, should skip to cleanup without re-running failed phase."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("execute", fail=True, track_calls=calls),
            MockPhase("apply", track_calls=calls),
            MockPhase("cleanup", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        # Fail at execute
        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="apply")

        execute_count_before = calls.count(("execute", "execute"))

        # Run cleanup - should NOT re-run execute or apply
        await pipeline.run_until(pipeline.current_context, stop_after="cleanup")

        # Execute should not have been called again
        assert calls.count(("execute", "execute")) == execute_count_before
        # Apply should not have been called
        assert ("execute", "apply") not in calls
        # Cleanup should have been called
        assert ("execute", "cleanup") in calls


# =============================================================================
# Tests: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_empty_pipeline(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Empty pipeline should handle run_until gracefully."""
        pipeline = LifecyclePipeline(phases=[], emitter=mock_emitter)

        with pytest.raises(ValueError):
            await pipeline.run_until(basic_context, stop_after="anything")

    @pytest.mark.asyncio
    async def test_single_phase_pipeline(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Single phase pipeline should work."""
        calls: list = []
        phases = [MockPhase("only", track_calls=calls)]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="only")

        assert calls == [("execute", "only")]

    @pytest.mark.asyncio
    async def test_no_cleanup_phase(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Pipeline without cleanup should handle failure gracefully."""
        calls: list = []
        phases = [
            MockPhase("configure", track_calls=calls),
            MockPhase("execute", fail=True, track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        with pytest.raises(RuntimeError):
            await pipeline.run_until(basic_context, stop_after="execute")

        # Rollback should have happened
        assert ("rollback", "configure") in calls

        # Trying to run cleanup should fail with ValueError (no such phase)
        with pytest.raises(ValueError):
            await pipeline.run_until(pipeline.current_context, stop_after="cleanup")

    @pytest.mark.asyncio
    async def test_phase_at_end_of_pipeline(self, basic_context: PhaseContext, mock_emitter: MagicMock) -> None:
        """Running to the last phase should work."""
        calls: list = []
        phases = [
            MockPhase("first", track_calls=calls),
            MockPhase("last", track_calls=calls),
        ]
        pipeline = LifecyclePipeline(phases=phases, emitter=mock_emitter)

        await pipeline.run_until(basic_context, stop_after="last")

        assert calls == [("execute", "first"), ("execute", "last")]
