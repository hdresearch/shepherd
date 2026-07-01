"""Composition combinators: sequential and branching execution.

This module provides combinators for composing tasks:

- sequence: Chain two tasks (output -> input)
- sequence_all: Chain N tasks in sequence
- branch: Choose task based on predicate
- loop: Repeat task until condition

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from .types import Predicate, Task, _set_combinator_name, ensure_task_fn, eval_predicate

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
# sequence: Chain two tasks
# =============================================================================


def sequence(
    task1: Task[InputT, O1],  # type: ignore[type-arg]
    task2: Task[O1, O2],  # type: ignore[type-arg]
) -> Task[InputT, O2]:  # type: ignore[type-arg]
    """Run tasks in sequence, piping output to input.

    First task's output becomes second task's input.
    Effects from both tasks are accumulated in order.

    Args:
        task1: First task (InputT -> O1). Can be a @task class or async callable.
        task2: Second task (O1 -> O2). Can be a @task class or async callable.

    Returns:
        Combined task (InputT -> O2)

    Example:
        # Chain preprocessing and main processing
        pipeline = sequence(
            PreprocessTask,  # @task classes work directly
            ProcessTask
        )
        result = await pipeline(raw_data, scope)
    """
    # Auto-adapt @task classes to callables
    task1 = ensure_task_fn(task1)
    task2 = ensure_task_fn(task2)

    async def sequenced_task(input: InputT, scope: Scope) -> O2:
        # Run first task
        intermediate = await task1(input, scope)
        # Run second task with first's output
        return await task2(intermediate, scope)  # type: ignore[no-any-return]

    _set_combinator_name(sequenced_task, "sequence", task1, task2)
    return sequenced_task


# =============================================================================
# sequence_all: Chain N tasks
# =============================================================================


def sequence_all(*tasks: Task[Any, Any]) -> Task[Any, Any]:  # type: ignore[type-arg]
    """Chain N tasks in sequence.

    Each task's output becomes the next task's input.
    Effects from all tasks are accumulated in order.

    Args:
        *tasks: Tasks to chain. Can be @task classes or async callables.

    Returns:
        Combined task that runs all in sequence

    Raises:
        ValueError: If no tasks provided

    Example:
        # Multi-stage pipeline
        pipeline = sequence_all(
            ParseInputTask,  # @task classes work directly
            ValidateTask,
            TransformTask,
            FormatOutputTask
        )
        result = await pipeline(raw_input, scope)
    """
    if not tasks:
        raise ValueError("sequence_all() requires at least one task")

    # Auto-adapt all @task classes to callables
    adapted_tasks = [ensure_task_fn(t) for t in tasks]

    async def sequenced_all_task(input: Any, scope: Scope) -> Any:
        result = input
        for task in adapted_tasks:
            result = await task(result, scope)
        return result

    _set_combinator_name(sequenced_all_task, "sequence_all", *tasks)
    return sequenced_all_task


# =============================================================================
# branch: Choose task based on predicate
# =============================================================================


def branch(
    predicate: Predicate,
    if_true: Task[InputT, OutputT],  # type: ignore[type-arg]
    if_false: Task[InputT, OutputT],  # type: ignore[type-arg]
) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Choose task based on predicate.

    The predicate receives the input and returns True or False.
    Based on the result, either if_true or if_false is executed.

    Args:
        predicate: Function (input) -> bool
        if_true: Task to run when predicate returns True. Can be @task class.
        if_false: Task to run when predicate returns False. Can be @task class.

    Returns:
        A task that conditionally executes one branch

    Example:
        # Use different strategies based on input size
        adaptive_task = branch(
            lambda data: len(data) > 1000,
            if_true=BatchProcessTask,  # @task classes work directly
            if_false=SimpleProcessTask
        )
    """
    # Auto-adapt @task classes to callables
    if_true = ensure_task_fn(if_true)
    if_false = ensure_task_fn(if_false)

    async def branching_task(input: InputT, scope: Scope) -> OutputT:
        # Evaluate predicate
        condition = await eval_predicate(predicate, input)

        # Execute appropriate branch
        if condition:
            return await if_true(input, scope)  # type: ignore[no-any-return]
        return await if_false(input, scope)  # type: ignore[no-any-return]

    _set_combinator_name(branching_task, "branch", if_true, if_false)
    return branching_task


# =============================================================================
# loop: Repeat task until condition
# =============================================================================


def loop(
    task: Task[InputT, InputT],  # type: ignore[type-arg]
    *,
    until: Predicate,
    max_iterations: int = 10,
) -> Task[InputT, InputT]:  # type: ignore[type-arg]
    """Run task repeatedly until predicate passes.

    Output of each iteration becomes input to the next.
    Effects accumulate across all iterations.

    Args:
        task: Task to repeat (must have same input/output type). Can be @task class.
        until: Predicate (result) -> bool that returns True when done
        max_iterations: Safety limit to prevent infinite loops (default 10)

    Returns:
        A task that loops until condition met

    Raises:
        RuntimeError: If max_iterations exceeded without satisfying predicate

    Example:
        # Iteratively refine a result
        refined = loop(
            RefineStepTask,  # @task class works directly
            until=lambda result: result.quality >= 0.95,
            max_iterations=5
        )

        # Retry until valid
        validated = loop(
            GenerateAndCheckTask,
            until=lambda r: r.is_valid,
            max_iterations=3
        )
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def looping_task(input: InputT, scope: Scope) -> InputT:
        result = input

        for _iteration in range(max_iterations):
            # Execute task
            result = await task(result, scope)

            # Check termination condition
            done = await eval_predicate(until, result)
            if done:
                return result

        # Max iterations reached without satisfying condition
        raise RuntimeError(f"loop() exceeded {max_iterations} iterations without satisfying termination condition")

    _set_combinator_name(looping_task, "loop", task)
    return looping_task


__all__ = [
    "branch",
    "loop",
    "sequence",
    "sequence_all",
]
