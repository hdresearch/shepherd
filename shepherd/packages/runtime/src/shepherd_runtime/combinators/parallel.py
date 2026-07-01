"""Parallel combinators: concurrent execution with conflict detection.

This module provides combinators for running tasks in parallel:

- parallel: Run two tasks in parallel, merge both if no conflicts
- race: Run two tasks in parallel, keep first to complete
- parallel_all: Run N tasks in parallel, merge all if no conflicts

IMPORTANT: The framework DETECTS conflicts but does NOT RESOLVE them.
Users must design parallel tasks for disjoint resources.

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, TypeVar

from .types import DisjointMerge, MergeStrategy, Task, _set_combinator_name, ensure_task_fn

if TYPE_CHECKING:
    from shepherd_runtime.scope import Scope

# =============================================================================
# Type Variables
# =============================================================================

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
O1 = TypeVar("O1")
O2 = TypeVar("O2")


# =============================================================================
# EffectConflictError
# =============================================================================


class EffectConflictError(Exception):
    """Raised when parallel tasks produce conflicting effects.

    This error indicates that parallel tasks attempted to modify the
    same resource (e.g., same file path) and the merge strategy
    detected an unresolvable conflict.

    Attributes:
        conflicts: List of conflict descriptions
    """

    def __init__(self, conflicts: list[str], message: str | None = None):
        self.conflicts = conflicts
        if message is None:
            message = f"Parallel tasks produced conflicting effects: {'; '.join(conflicts)}"
        super().__init__(message)


# =============================================================================
# parallel: Run two tasks in parallel
# =============================================================================


def parallel(
    task1: Task[InputT, O1],  # type: ignore[type-arg]
    task2: Task[InputT, O2],  # type: ignore[type-arg]
    *,
    merge_strategy: MergeStrategy | None = None,
) -> Task[InputT, tuple[O1, O2]]:  # type: ignore[type-arg]
    """Run two tasks in parallel, merge both if no conflicts.

    Both tasks receive the same input and run concurrently in separate
    forks. If both succeed and their effects don't conflict, both forks
    are merged to the parent. Otherwise, both are discarded.

    IMPORTANT: Framework DETECTS conflicts but does NOT RESOLVE them.
    Users must design parallel tasks for disjoint resources.

    Args:
        task1: First task to run. Can be a @task class or async callable.
        task2: Second task to run. Can be a @task class or async callable.
        merge_strategy: Strategy for detecting/handling conflicts.
            Defaults to DisjointMerge (fails on any overlap).

    Returns:
        A new task that returns (result1, result2)

    Raises:
        EffectConflictError: If tasks produce conflicting effects
        Exception: If either task raises an exception

    Example:
        # Run two independent tasks in parallel
        combined = parallel(
            ProcessSectionATask,  # @task classes work directly
            ProcessSectionBTask
        )
        result_a, result_b = await combined(document, scope)
    """
    # Auto-adapt @task classes to callables
    task1 = ensure_task_fn(task1)
    task2 = ensure_task_fn(task2)
    strategy = merge_strategy or DisjointMerge()

    async def parallel_task(input: InputT, scope: Scope) -> tuple[O1, O2]:
        # Fork for each task
        child1 = scope.fork()
        child2 = scope.fork()

        try:
            # Run both tasks concurrently
            result1, result2 = await asyncio.gather(task1(input, child1), task2(input, child2), return_exceptions=False)

            # Check for conflicts
            can_merge, conflict_reason = strategy.can_merge([child1.effects, child2.effects])

            if not can_merge:
                # Conflict detected - discard both forks
                child1.discard()
                child2.discard()
                raise EffectConflictError(conflicts=[conflict_reason] if conflict_reason else ["Unknown conflict"])

            # No conflict - merge both forks
            scope.merge(child1)
            scope.merge(child2)
            return (result1, result2)

        except EffectConflictError:
            raise
        except Exception:
            # On any exception, clean up both forks
            child1.discard()
            child2.discard()
            raise

    _set_combinator_name(parallel_task, "parallel", task1, task2)
    return parallel_task


# =============================================================================
# race: First to complete wins
# =============================================================================


def race(
    task1: Task[InputT, O1],  # type: ignore[type-arg]
    task2: Task[InputT, O2],  # type: ignore[type-arg]
) -> Task[InputT, O1 | O2]:  # type: ignore[type-arg]
    """Run two tasks in parallel, keep first to complete.

    Both tasks start concurrently. The first to complete successfully
    has its effects merged. The other is cancelled and its fork discarded.

    Args:
        task1: First task. Can be a @task class or async callable.
        task2: Second task. Can be a @task class or async callable.

    Returns:
        A new task that returns the winner's result

    Raises:
        Exception: If both tasks fail

    Example:
        # Try two approaches, use whichever finishes first
        fast_result = race(
            ApproachATask,  # @task classes work directly
            ApproachBTask
        )
    """
    # Auto-adapt @task classes to callables
    task1 = ensure_task_fn(task1)
    task2 = ensure_task_fn(task2)

    async def racing_task(input: InputT, scope: Scope) -> O1 | O2:
        # Fork for each task
        child1 = scope.fork()
        child2 = scope.fork()

        # Create tasks
        coro1 = task1(input, child1)
        coro2 = task2(input, child2)

        # Wrap in asyncio tasks so we can cancel
        async_task1 = asyncio.create_task(coro1)  # type: ignore[arg-type, var-annotated]
        async_task2 = asyncio.create_task(coro2)  # type: ignore[arg-type, var-annotated]

        pending = {async_task1, async_task2}
        winner = None
        winner_child = None
        loser_child = None
        result = None

        try:
            # Wait for first to complete
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

            # Get the winner
            for completed_task in done:
                if completed_task.exception() is None:
                    winner = completed_task
                    result = completed_task.result()
                    break

            if winner is None and pending:
                # First completed task had an exception
                # Wait for the other one
                done, pending = await asyncio.wait(pending)
                for completed_task in done:
                    if completed_task.exception() is None:
                        winner = completed_task
                        result = completed_task.result()
                        break

            if winner is None:
                # Both failed - raise the first exception
                async_task1.result()  # Will raise

            # Determine winner and loser
            if winner is async_task1:
                winner_child = child1
                loser_child = child2
            else:
                winner_child = child2
                loser_child = child1

            # Cancel pending tasks
            for p in pending:
                p.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await p

            # Merge winner, discard loser
            scope.merge(winner_child)
            loser_child.discard()

            return result  # type: ignore

        except Exception:
            # Clean up on failure
            for t in [async_task1, async_task2]:
                if not t.done():
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t
            child1.discard()
            child2.discard()
            raise

    _set_combinator_name(racing_task, "race", task1, task2)
    return racing_task


# =============================================================================
# parallel_all: Run N tasks in parallel
# =============================================================================


def parallel_all(
    *tasks: Task[InputT, Any],  # type: ignore[type-arg]
    merge_strategy: MergeStrategy | None = None,
) -> Task[InputT, tuple[Any, ...]]:  # type: ignore[type-arg]
    """Run N tasks in parallel, merge all if no conflicts.

    All tasks receive the same input and run concurrently in separate
    forks. If all succeed and their effects don't conflict, all forks
    are merged. Otherwise, all are discarded.

    Args:
        *tasks: Tasks to run in parallel. Can be @task classes or async callables.
        merge_strategy: Strategy for detecting/handling conflicts.
            Defaults to DisjointMerge.

    Returns:
        A new task that returns tuple of all results

    Raises:
        EffectConflictError: If tasks produce conflicting effects
        ValueError: If no tasks provided
        Exception: If any task raises an exception

    Example:
        # Process multiple sections in parallel
        combined = parallel_all(
            ProcessSection1Task,  # @task classes work directly
            ProcessSection2Task,
            ProcessSection3Task
        )
        r1, r2, r3 = await combined(document, scope)
    """
    if not tasks:
        raise ValueError("parallel_all() requires at least one task")

    # Auto-adapt all @task classes to callables
    adapted_tasks = [ensure_task_fn(t) for t in tasks]
    strategy = merge_strategy or DisjointMerge()

    async def parallel_all_task(input: InputT, scope: Scope) -> tuple[Any, ...]:
        # Fork for each task
        children = [scope.fork() for _ in adapted_tasks]

        try:
            # Run all tasks concurrently
            results = await asyncio.gather(
                *[task(input, child) for task, child in zip(adapted_tasks, children, strict=True)],
                return_exceptions=False,
            )

            # Check for conflicts
            streams = [child.effects for child in children]
            can_merge, conflict_reason = strategy.can_merge(streams)

            if not can_merge:
                # Conflict detected - discard all forks
                for child in children:
                    child.discard()
                raise EffectConflictError(conflicts=[conflict_reason] if conflict_reason else ["Unknown conflict"])

            # No conflict - merge all forks
            for child in children:
                scope.merge(child)

            return tuple(results)

        except EffectConflictError:
            raise
        except Exception:
            # On any exception, clean up all forks
            for child in children:
                child.discard()
            raise

    _set_combinator_name(parallel_all_task, "parallel_all", *tasks)
    return parallel_all_task


__all__ = [
    "EffectConflictError",
    "parallel",
    "parallel_all",
    "race",
]
