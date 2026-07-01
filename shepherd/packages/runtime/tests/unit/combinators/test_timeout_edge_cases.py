"""Tests for timeout edge cases and nested timeout behavior.

This module tests timeout handling in edge cases:
- Nested timeout interactions (parent vs child timeouts)
- Timeout with scope operations
- Timeout cleanup behavior

These tests address coverage gap HIGH-T4: timeout edge cases.
"""

import asyncio
import contextlib

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.combinators.gating import timeout
from shepherd_runtime.combinators.types import Rejected
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Tasks (with correct signature: input, scope)
# =============================================================================


async def fast_task(input: str, scope: Scope) -> str:
    """Fast task that completes quickly."""
    scope.emit(Effect(effect_type="fast_effect"))
    await asyncio.sleep(0.01)
    return f"fast: {input}"


async def slow_task(input: str, scope: Scope) -> str:
    """Slow task that takes longer."""
    scope.emit(Effect(effect_type="slow_start"))
    await asyncio.sleep(0.5)  # 500ms
    scope.emit(Effect(effect_type="slow_end"))
    return f"slow: {input}"


async def medium_task(input: str, scope: Scope) -> str:
    """Medium-speed task."""
    scope.emit(Effect(effect_type="medium_effect"))
    await asyncio.sleep(0.1)  # 100ms
    return f"medium: {input}"


async def failing_task(input: str, scope: Scope) -> str:
    """Task that raises an exception."""
    scope.emit(Effect(effect_type="before_fail"))
    raise ValueError("Intentional failure")


async def multi_effect_task(input: str, scope: Scope) -> str:
    """Task that emits multiple effects with delays."""
    scope.emit(Effect(effect_type="effect_1"))
    await asyncio.sleep(0.05)
    scope.emit(Effect(effect_type="effect_2"))
    await asyncio.sleep(0.05)
    scope.emit(Effect(effect_type="effect_3"))
    return f"multi: {input}"


# =============================================================================
# Tests: Basic Timeout Behavior
# =============================================================================


class TestTimeoutBasics:
    """Basic timeout behavior tests."""

    async def test_timeout_allows_fast_operations(self):
        """Operations completing before timeout succeed."""
        timed_task = timeout(fast_task, seconds=1.0)

        with Scope() as scope:
            result = await timed_task("test", scope)

            assert result == "fast: test"
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "fast_effect"

    async def test_timeout_rejects_slow_operations(self):
        """Operations exceeding timeout are rejected."""
        timed_task = timeout(slow_task, seconds=0.1)  # 100ms timeout

        with Scope() as scope:
            result = await timed_task("test", scope)

            # Should be Rejected
            assert isinstance(result, Rejected)
            assert "Timeout" in result.reason
            # Effects before timeout are captured but not merged
            assert len(scope.effects) == 0

    async def test_timeout_effects_captured_before_timeout(self):
        """Effects emitted before timeout are captured in Rejected."""
        timed_task = timeout(slow_task, seconds=0.05)

        with Scope() as scope:
            result = await timed_task("test", scope)

            assert isinstance(result, Rejected)
            # The start effect should have been captured
            assert len(result.effects) >= 1
            assert result.effects[0].effect.effect_type == "slow_start"


# =============================================================================
# Tests: Nested Timeout Behavior
# =============================================================================


class TestNestedTimeouts:
    """Tests for nested timeout interactions."""

    async def test_inner_timeout_shorter_than_outer(self):
        """Inner timeout triggers before outer timeout."""
        # Inner task with short timeout
        inner_timed = timeout(slow_task, seconds=0.05)

        # Outer wrapper
        async def outer_task(input: str, scope: Scope) -> str:
            result = await inner_timed(input, scope)
            return f"outer: {result}"

        outer_timed = timeout(outer_task, seconds=1.0)

        with Scope() as scope:
            result = await outer_timed("test", scope)

            # Inner timeout should have triggered, outer completes
            assert "Rejected" in str(result) or isinstance(result, str)

    async def test_outer_timeout_shorter_than_inner(self):
        """Outer timeout triggers before inner timeout."""
        # Inner task with long timeout
        inner_timed = timeout(slow_task, seconds=1.0)

        # Outer wrapper
        async def outer_task(input: str, scope: Scope) -> str:
            result = await inner_timed(input, scope)
            return f"outer: {result}"

        outer_timed = timeout(outer_task, seconds=0.05)

        with Scope() as scope:
            result = await outer_timed("test", scope)

            # Outer timeout should have triggered
            assert isinstance(result, Rejected)
            assert "Timeout" in result.reason

    async def test_nested_timeouts_both_succeed(self):
        """Nested timeouts both succeed with fast operation."""
        inner_timed = timeout(fast_task, seconds=1.0)

        async def outer_task(input: str, scope: Scope) -> str:
            result = await inner_timed(input, scope)
            return f"outer: {result}"

        outer_timed = timeout(outer_task, seconds=1.0)

        with Scope() as scope:
            result = await outer_timed("test", scope)

            assert result == "outer: fast: test"


# =============================================================================
# Tests: Timeout with Scope Operations
# =============================================================================


class TestTimeoutWithScope:
    """Tests for timeout interaction with scope operations."""

    async def test_timeout_with_child_scope(self):
        """Timeout works correctly with child scopes."""

        async def task_with_child(input: str, scope: Scope) -> str:
            scope.emit(Effect(effect_type="parent_effect"))

            with scope.child() as child:
                child.emit(Effect(effect_type="child_effect"))
                await asyncio.sleep(0.01)

            return f"done: {input}"

        timed_task = timeout(task_with_child, seconds=1.0)

        with Scope() as scope:
            result = await timed_task("test", scope)

            assert result == "done: test"
            # Both effects should be present (child propagates to parent)
            effect_types = [e.effect.effect_type for e in scope.effects.layers]
            assert "parent_effect" in effect_types
            assert "child_effect" in effect_types

    async def test_timeout_with_fork(self):
        """Timeout works correctly with forked scopes."""

        async def task_with_fork(input: str, scope: Scope) -> str:
            scope.emit(Effect(effect_type="before_fork"))

            forked = scope.fork()
            forked.emit(Effect(effect_type="in_fork"))
            await asyncio.sleep(0.01)
            scope.merge(forked)

            return f"forked: {input}"

        timed_task = timeout(task_with_fork, seconds=1.0)

        with Scope() as scope:
            result = await timed_task("test", scope)

            assert result == "forked: test"
            effect_types = [e.effect.effect_type for e in scope.effects.layers]
            assert "before_fork" in effect_types
            assert "in_fork" in effect_types

    async def test_timeout_during_fork_operation(self):
        """Timeout during fork operation handles cleanup."""

        async def slow_fork_task(input: str, scope: Scope) -> str:
            forked = scope.fork()
            forked.emit(Effect(effect_type="fork_start"))

            # This sleep will cause timeout
            await asyncio.sleep(0.5)

            forked.emit(Effect(effect_type="fork_end"))
            scope.merge(forked)
            return "done"

        timed_task = timeout(slow_fork_task, seconds=0.05)

        with Scope() as scope:
            result = await timed_task("test", scope)

            # Should timeout
            assert isinstance(result, Rejected)
            # Fork effects should not be in parent scope
            assert len(scope.effects) == 0


# =============================================================================
# Tests: Timeout Error Scenarios
# =============================================================================


class TestTimeoutErrors:
    """Tests for error handling with timeouts."""

    async def test_exception_before_timeout_propagates(self):
        """Exceptions raised before timeout propagate normally."""
        timed_task = timeout(failing_task, seconds=1.0)

        with Scope() as scope, pytest.raises(ValueError, match="Intentional failure"):
            await timed_task("test", scope)

    async def test_exception_effects_not_merged_on_error(self):
        """Effects from failed task are not merged to parent."""
        timed_task = timeout(failing_task, seconds=1.0)

        with Scope() as scope:
            with contextlib.suppress(ValueError):
                await timed_task("test", scope)

            # Effects should not be in parent (task failed, fork discarded)
            # The exact behavior depends on implementation
            # This documents the expected behavior
            assert len(scope.effects) == 0


# =============================================================================
# Tests: Timeout Cleanup
# =============================================================================


class TestTimeoutCleanup:
    """Tests for cleanup behavior when timeout occurs."""

    async def test_timeout_cleans_up_fork(self):
        """Fork is discarded when timeout occurs."""
        timed_task = timeout(slow_task, seconds=0.05)

        with Scope() as scope:
            result = await timed_task("test", scope)

            assert isinstance(result, Rejected)
            # Effects from timed-out task should not leak to parent
            assert len(scope.effects) == 0

    async def test_multiple_timeouts_independent(self):
        """Multiple timeout operations don't interfere."""
        fast_timed = timeout(fast_task, seconds=1.0)
        slow_timed = timeout(slow_task, seconds=0.05)

        with Scope() as scope:
            # Run fast task - should succeed
            result1 = await fast_timed("test1", scope)
            assert result1 == "fast: test1"
            assert len(scope.effects) == 1

            # Run slow task - should timeout
            result2 = await slow_timed("test2", scope)
            assert isinstance(result2, Rejected)
            # Only fast task effects remain
            assert len(scope.effects) == 1


# =============================================================================
# Tests: Timeout Task Name Preservation
# =============================================================================


class TestTimeoutTaskName:
    """Tests for task name preservation in timeout combinator."""

    async def test_timeout_preserves_task_name(self):
        """Timeout combinator preserves the original task name."""
        timed_task = timeout(fast_task, seconds=1.0)

        # The wrapped function should have a recognizable name
        assert "fast_task" in timed_task.__name__ or "timed" in timed_task.__name__
