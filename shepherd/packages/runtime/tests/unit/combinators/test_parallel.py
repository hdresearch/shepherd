"""Tests for parallel combinators: parallel, race, parallel_all."""

import asyncio
import contextlib

import pytest
from shepherd_core.effects import Effect, FileCreate
from shepherd_runtime.combinators.parallel import (
    EffectConflictError,
    parallel,
    parallel_all,
    race,
)
from shepherd_runtime.combinators.types import LastWriteWins
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Fixtures: Mock Tasks
# =============================================================================


async def task_a(input: str, scope: Scope) -> str:
    """Task that modifies file a.py."""
    scope.emit(FileCreate(path="/a.py", content="content_a"))
    return f"result_a: {input}"


async def task_b(input: str, scope: Scope) -> str:
    """Task that modifies file b.py."""
    scope.emit(FileCreate(path="/b.py", content="content_b"))
    return f"result_b: {input}"


async def task_shared(input: str, scope: Scope) -> str:
    """Task that modifies shared.py (conflicts with another task_shared)."""
    scope.emit(FileCreate(path="/shared.py", content=f"content: {input}"))
    return f"result_shared: {input}"


async def slow_task(input: str, scope: Scope) -> str:
    """Task that takes 200ms."""
    scope.emit(Effect(effect_type="slow_start"))
    await asyncio.sleep(0.2)
    scope.emit(Effect(effect_type="slow_end"))
    return f"slow: {input}"


async def fast_task(input: str, scope: Scope) -> str:
    """Task that completes immediately."""
    scope.emit(Effect(effect_type="fast"))
    return f"fast: {input}"


async def failing_task(input: str, scope: Scope) -> str:
    """Task that always fails."""
    scope.emit(Effect(effect_type="before_fail"))
    raise ValueError("Intentional failure")


# =============================================================================
# Tests for parallel()
# =============================================================================


class TestParallel:
    """Tests for parallel combinator."""

    @pytest.mark.asyncio
    async def test_parallel_merges_both_on_success(self):
        """parallel() merges both forks when tasks succeed without conflict."""
        combined = parallel(task_a, task_b)

        with Scope() as scope:
            result_a, result_b = await combined("test", scope)

            assert "result_a" in result_a
            assert "result_b" in result_b
            # Both effects should be merged
            assert len(scope.effects) == 2
            paths = {layer.effect.path for layer in scope.effects}
            assert paths == {"/a.py", "/b.py"}

    @pytest.mark.asyncio
    async def test_parallel_detects_conflict(self):
        """parallel() raises EffectConflictError on resource overlap."""
        # Both tasks modify the same file
        combined = parallel(task_shared, task_shared)

        with Scope() as scope:
            with pytest.raises(EffectConflictError) as exc_info:
                await combined("test", scope)

            assert "shared.py" in str(exc_info.value)
            # Both forks should be discarded
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_parallel_discards_all_on_conflict(self):
        """parallel() discards all forks when conflict detected."""
        combined = parallel(task_shared, task_shared)

        with Scope() as scope:
            with contextlib.suppress(EffectConflictError):
                await combined("test", scope)

            # No effects should be merged
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_parallel_with_last_write_wins(self):
        """parallel() with LastWriteWins allows conflicts."""
        combined = parallel(task_shared, task_shared, merge_strategy=LastWriteWins())

        with Scope() as scope:
            result_a, result_b = await combined("test", scope)

            # Both results returned
            assert "result_shared" in result_a
            assert "result_shared" in result_b
            # Both effects merged (last write wins)
            assert len(scope.effects) == 2

    @pytest.mark.asyncio
    async def test_parallel_cleans_up_on_exception(self):
        """parallel() discards both forks when one fails."""
        combined = parallel(task_a, failing_task)

        with Scope() as scope:
            with pytest.raises(ValueError):
                await combined("test", scope)

            # Both forks should be discarded
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_parallel_preserves_task_names(self):
        """parallel() preserves task names for debugging."""
        combined = parallel(task_a, task_b)

        assert "task_a" in combined.__name__
        assert "task_b" in combined.__name__


# =============================================================================
# Tests for race()
# =============================================================================


class TestRace:
    """Tests for race combinator."""

    @pytest.mark.asyncio
    async def test_race_keeps_winner(self):
        """race() merges only the winner's effects."""
        combined = race(fast_task, slow_task)

        with Scope() as scope:
            result = await combined("test", scope)

            assert "fast" in result
            # Only fast_task's effect should be merged
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "fast"

    @pytest.mark.asyncio
    async def test_race_discards_loser(self):
        """race() discards the slower task's effects."""
        combined = race(fast_task, slow_task)

        with Scope() as scope:
            await combined("test", scope)

            # slow_task's effects should not appear
            effect_types = {layer.effect.effect_type for layer in scope.effects}
            assert "slow_start" not in effect_types
            assert "slow_end" not in effect_types

    @pytest.mark.asyncio
    async def test_race_handles_first_failure(self):
        """race() uses second task if first fails."""

        async def instant_fail(input: str, scope: Scope) -> str:
            raise ValueError("Instant failure")

        combined = race(instant_fail, fast_task)

        with Scope() as scope:
            result = await combined("test", scope)

            assert "fast" in result

    @pytest.mark.asyncio
    async def test_race_raises_if_both_fail(self):
        """race() raises if both tasks fail."""

        async def fail_a(input: str, scope: Scope) -> str:
            raise ValueError("Error A")

        async def fail_b(input: str, scope: Scope) -> str:
            raise RuntimeError("Error B")

        combined = race(fail_a, fail_b)

        with Scope() as scope, pytest.raises(ValueError, match="Error A"):
            await combined("test", scope)

    @pytest.mark.asyncio
    async def test_race_preserves_task_names(self):
        """race() preserves task names for debugging."""
        combined = race(fast_task, slow_task)

        assert "fast_task" in combined.__name__
        assert "slow_task" in combined.__name__


# =============================================================================
# Tests for parallel_all()
# =============================================================================


class TestParallelAll:
    """Tests for parallel_all combinator."""

    @pytest.mark.asyncio
    async def test_parallel_all_merges_all(self):
        """parallel_all() merges all forks when no conflict."""

        async def task_c(input: str, scope: Scope) -> str:
            scope.emit(FileCreate(path="/c.py", content=""))
            return "result_c"

        combined = parallel_all(task_a, task_b, task_c)

        with Scope() as scope:
            results = await combined("test", scope)

            assert len(results) == 3
            assert "result_a" in results[0]
            assert "result_b" in results[1]
            assert "result_c" in results[2]
            # All effects merged
            assert len(scope.effects) == 3

    @pytest.mark.asyncio
    async def test_parallel_all_detects_conflict(self):
        """parallel_all() raises on conflict."""
        combined = parallel_all(task_a, task_shared, task_shared)

        with Scope() as scope:
            with pytest.raises(EffectConflictError):
                await combined("test", scope)

            # All forks discarded
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_parallel_all_requires_tasks(self):
        """parallel_all() raises if no tasks provided."""
        with pytest.raises(ValueError, match="at least one task"):
            parallel_all()

    @pytest.mark.asyncio
    async def test_parallel_all_single_task(self):
        """parallel_all() works with single task."""
        combined = parallel_all(task_a)

        with Scope() as scope:
            results = await combined("test", scope)

            assert len(results) == 1
            assert "result_a" in results[0]

    @pytest.mark.asyncio
    async def test_parallel_all_preserves_task_names(self):
        """parallel_all() preserves task names for debugging."""
        combined = parallel_all(task_a, task_b)

        assert "task_a" in combined.__name__
        assert "task_b" in combined.__name__
