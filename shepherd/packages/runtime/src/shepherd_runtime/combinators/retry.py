"""Retry combinators: automatic retry and recovery patterns.

This module provides combinators for handling failures gracefully:

- retry: Retry task until success or max attempts reached
- fallback: Try tasks in order until one succeeds
- recover: Provide default value on error

Each attempt runs in a fresh fork. Failed attempts are discarded,
ensuring no partial effects contaminate the parent scope.

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, TypeVar

from .types import Predicate, Task, _set_combinator_name, ensure_task_fn, eval_predicate

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shepherd_runtime.scope import Scope

# =============================================================================
# Type Variables
# =============================================================================

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


# =============================================================================
# retry: Retry task until success or max attempts
# =============================================================================


def retry(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    *,
    max_attempts: int = 3,
    until: Predicate | None = None,
    delay_seconds: float = 0,
    backoff: float = 1.0,
) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Retry task until success or max attempts reached.

    Each attempt runs in a fresh fork. Failed attempts are discarded.
    Only the successful attempt's effects are merged.

    Args:
        task: The task to retry. Can be a @task class or async callable.
        max_attempts: Maximum number of attempts (default 3)
        until: Optional predicate - retry until this returns True.
            If None, retry until no exception is raised.
        delay_seconds: Initial delay between attempts (default 0)
        backoff: Multiplier for delay after each attempt (default 1.0)

    Returns:
        A new task that retries on failure

    Raises:
        Exception: The last exception if all attempts fail

    Example:
        # Retry up to 5 times with exponential backoff
        reliable_task = retry(
            FlakyTask,  # @task class works directly
            max_attempts=5,
            delay_seconds=1.0,
            backoff=2.0  # 1s, 2s, 4s, 8s delays
        )

        # Retry until result is valid
        validated_task = retry(
            GenerateTask,
            until=lambda result: result.is_valid,
            max_attempts=3
        )
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def retrying_task(input: InputT, scope: Scope) -> OutputT:
        last_error: Exception | None = None
        current_delay = delay_seconds

        for attempt in range(max_attempts):
            # Fork for this attempt
            child = scope.fork()

            try:
                # Execute task in fork
                result = await task(input, child)

                # Check until predicate if provided
                if until is not None:
                    satisfied = await eval_predicate(until, result)
                    if not satisfied:
                        # Predicate not satisfied - discard and retry
                        child.discard()
                        if attempt < max_attempts - 1 and current_delay > 0:
                            await asyncio.sleep(current_delay)
                            current_delay *= backoff
                            continue
                        if attempt < max_attempts - 1:
                            continue
                        # Last attempt - merge anyway (best effort)
                        scope.merge(child)
                        return result  # type: ignore[no-any-return]

                # Success - merge effects and return
                scope.merge(child)
                return result  # type: ignore[no-any-return]

            except Exception as e:  # noqa: BLE001
                # Failure - discard fork
                child.discard()
                last_error = e

                # If not last attempt, wait and retry
                if attempt < max_attempts - 1 and current_delay > 0:
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

        # All attempts failed - raise last error
        assert last_error is not None  # Loop guarantees at least one attempt
        raise last_error

    _set_combinator_name(retrying_task, "retry", task)
    return retrying_task


# =============================================================================
# fallback: Try tasks in order until one succeeds
# =============================================================================


def fallback(*tasks: Task[InputT, OutputT]) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Try tasks in order until one succeeds.

    Each task runs in a fork. First success is merged, others discarded.
    If all tasks fail, the last exception is raised.

    Args:
        *tasks: Tasks to try in order. Can be @task classes or async callables.

    Returns:
        A new task that tries alternatives on failure

    Raises:
        Exception: The last exception if all tasks fail
        ValueError: If no tasks provided

    Example:
        # Try primary, then backup approaches
        resilient_task = fallback(
            FastButFlakyTask,  # @task classes work directly
            SlowButReliableTask,
            CachedFallbackTask
        )
    """
    if not tasks:
        raise ValueError("fallback() requires at least one task")

    # Auto-adapt all @task classes to callables
    adapted_tasks = [ensure_task_fn(t) for t in tasks]

    async def fallback_task(input: InputT, scope: Scope) -> OutputT:
        last_error: Exception | None = None

        for _i, task in enumerate(adapted_tasks):
            # Fork for this attempt
            child = scope.fork()

            try:
                # Execute task in fork
                result = await task(input, child)

                # Success - merge effects and return
                scope.merge(child)
                return result  # type: ignore[no-any-return]

            except Exception as e:  # noqa: BLE001
                # Failure - discard fork and try next
                child.discard()
                last_error = e

        # All tasks failed - raise last error
        assert last_error is not None  # Loop guarantees at least one task was tried
        raise last_error

    _set_combinator_name(fallback_task, "fallback", *tasks)
    return fallback_task


# =============================================================================
# recover: Provide default value on error
# =============================================================================


def recover(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    on_error: Callable[[Exception], OutputT | Awaitable[OutputT]],
) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Run task, call recovery handler on error.

    Unlike retry, this provides a default value on failure instead of
    retrying. The recovery handler receives the exception and can
    return an appropriate fallback value.

    Args:
        task: The task to execute. Can be a @task class or async callable.
        on_error: Handler (exception) -> fallback_value

    Returns:
        A new task that never raises, returning fallback on error

    Example:
        # Return empty result on failure
        safe_search = recover(
            SearchTask,  # @task class works directly
            on_error=lambda e: SearchResults(items=[], error=str(e))
        )

        # Log and return default
        async def handle_error(e):
            await log_error(e)
            return default_value

        safe_task = recover(RiskyTask, handle_error)
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def recovering_task(input: InputT, scope: Scope) -> OutputT:
        # Fork for isolated execution
        child = scope.fork()

        try:
            # Execute task in fork
            result = await task(input, child)

            # Success - merge effects and return
            scope.merge(child)
            return result  # type: ignore[no-any-return]

        except Exception as e:  # noqa: BLE001
            # Failure - discard fork and call recovery handler
            child.discard()

            # Call recovery handler (may be sync or async)
            recovery_result = on_error(e)
            if inspect.isawaitable(recovery_result):
                return await recovery_result
            return recovery_result

    _set_combinator_name(recovering_task, "recover", task)
    return recovering_task


__all__ = [
    "fallback",
    "recover",
    "retry",
]
