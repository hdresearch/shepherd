"""Gating combinators: conditional commit based on predicates.

This module provides combinators that run a task in a fork, then decide
whether to merge or discard based on the result and effects:

- gate: Run task, commit only if predicate passes
- budget: Reject if resource limits exceeded
- timeout: Reject if execution time exceeded

All gating combinators follow the same pattern:
    1. Fork scope for isolation
    2. Execute task in fork
    3. Evaluate condition (predicate, budget, timeout)
    4. If condition passes: merge fork to parent
    5. If condition fails: discard fork, return Rejected

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, TypeVar

from .types import Budget, JudgePredicate, Rejected, Task, _set_combinator_name, ensure_task_fn, eval_predicate

if TYPE_CHECKING:
    from shepherd_runtime.scope import Scope

# =============================================================================
# Type Variables
# =============================================================================

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


# =============================================================================
# gate: Conditional commit based on predicate
# =============================================================================


def gate(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    predicate: JudgePredicate,
) -> Task[InputT, OutputT | Rejected[OutputT]]:  # type: ignore[type-arg]
    """Run task, commit only if predicate passes.

    The predicate receives (result, effects) and returns True to merge
    or False to discard. This enables sophisticated approval logic based
    on both the output value and the side effects produced.

    Args:
        task: The task to gate. Can be a @task class or async callable.
        predicate: Function (result, effects) -> bool that determines
            whether to merge (True) or discard (False)

    Returns:
        A new task that returns either:
        - The original result (if predicate passed)
        - Rejected(value, effects) (if predicate failed)

    Example:
        # Only accept results that don't modify too many files
        def approve(result, effects):
            file_count = effects.count(FilePatch)
            return file_count <= 5

        # Works with @task classes directly
        safe_task = gate(RiskyTask, approve)

        # Or with task functions
        safe_task = gate(task_fn(RiskyTask), approve)

    Implementation:
        1. Fork scope for isolation
        2. Execute task in fork
        3. Evaluate predicate(result, effects)
        4. If True: merge fork to parent, return result
        5. If False: discard fork, return Rejected
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def gated_task(input: InputT, scope: Scope) -> OutputT | Rejected[OutputT]:
        # Fork for isolated execution
        child = scope.fork()

        try:
            # Execute task in fork
            result = await task(input, child)

            # Get effects for predicate
            effects = child.effects

            # Evaluate predicate (may be sync or async)
            approved = await eval_predicate(predicate, result, effects)

            if approved:
                # Merge effects to parent
                scope.merge(child)
                return result  # type: ignore[no-any-return]
            # Discard effects, return Rejected
            child.discard()
            return Rejected(value=result, effects=effects, reason="Predicate rejected")

        except Exception:
            # On exception, discard the fork and re-raise
            child.discard()
            raise

    _set_combinator_name(gated_task, "gate", task)
    return gated_task


# =============================================================================
# budget: Reject if resource limits exceeded
# =============================================================================


def budget(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    limits: Budget,
) -> Task[InputT, OutputT | Rejected[OutputT]]:  # type: ignore[type-arg]
    """Run task, reject if it exceeds resource budget.

    This combinator wraps a task with resource limit checking. The task
    runs in a fork, and if the effects exceed any budget limit, the
    fork is discarded and a Rejected result is returned.

    Args:
        task: The task to constrain. Can be a @task class or async callable.
        limits: Budget specifying max effects, files, lines, and/or duration

    Returns:
        A new task that returns either:
        - The original result (if within budget)
        - Rejected(value, effects, reason) (if budget exceeded)

    Example:
        # Limit task to 10 effects and 30 seconds
        constrained = budget(
            ExpensiveTask,  # @task class works directly
            Budget(max_effects=10, max_duration_seconds=30)
        )
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def budgeted_task(input: InputT, scope: Scope) -> OutputT | Rejected[OutputT]:
        # Fork for isolated execution
        child = scope.fork()
        start_time = time.monotonic()

        try:
            # Execute task in fork
            result = await task(input, child)

            # Check budget
            duration = time.monotonic() - start_time
            effects = child.effects
            within_budget, reason = limits.check(effects, duration)

            if within_budget:
                # Merge effects to parent
                scope.merge(child)
                return result  # type: ignore[no-any-return]
            # Discard effects, return Rejected
            child.discard()
            return Rejected(value=result, effects=effects, reason=reason)

        except Exception:
            # On exception, discard the fork and re-raise
            child.discard()
            raise

    _set_combinator_name(budgeted_task, "budget", task)
    return budgeted_task


# =============================================================================
# timeout: Reject if execution time exceeded
# =============================================================================


def timeout(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    seconds: float,
) -> Task[InputT, OutputT | Rejected[OutputT]]:  # type: ignore[type-arg]
    """Run task with timeout, reject if exceeded.

    This combinator adds a time limit to task execution. If the task
    doesn't complete within the specified time, it's cancelled, the
    fork is discarded, and a Rejected result is returned.

    Args:
        task: The task to time-limit. Can be a @task class or async callable.
        seconds: Maximum execution time in seconds

    Returns:
        A new task that returns either:
        - The original result (if completed in time)
        - Rejected(value=None, effects, reason) (if timed out)

    Example:
        # Give task 30 seconds max
        quick_task = timeout(SlowTask, 30.0)

    Note:
        On timeout, the Rejected.value is None since no result was produced.
        The effects captured before timeout are still available in Rejected.effects.
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def timed_task(input: InputT, scope: Scope) -> OutputT | Rejected[OutputT]:
        # Fork for isolated execution
        child = scope.fork()

        try:
            # Execute task with timeout
            result = await asyncio.wait_for(task(input, child), timeout=seconds)

            # Task completed in time - merge effects
            scope.merge(child)
            return result  # type: ignore[no-any-return]

        except asyncio.TimeoutError:
            # Timeout - discard fork and return Rejected
            effects = child.effects
            child.discard()
            return Rejected(
                value=None,  # type: ignore[arg-type]
                effects=effects,
                reason=f"Timeout after {seconds}s",
            )

        except Exception:
            # On other exception, discard the fork and re-raise
            child.discard()
            raise

    _set_combinator_name(timed_task, "timeout", task)
    return timed_task


__all__ = [
    "budget",
    "gate",
    "timeout",
]
