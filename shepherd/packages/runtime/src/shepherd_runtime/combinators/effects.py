"""Effect transformation combinators.

This module provides combinators for transforming effect streams:

- filter_effects: Only merge effects matching predicate
- map_effects: Transform effects before merging
- tap: Observe result and effects without modifying
- scope_tap: Observe child scope for debugging

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, TypeVar

from .types import EffectPredicate, Task, _set_combinator_name, ensure_task_fn

if TYPE_CHECKING:
    from collections.abc import Callable

    from shepherd_core.effects import Effect

    from shepherd_runtime.scope import Scope
    from shepherd_runtime.scope_types import EffectStreamLike

# =============================================================================
# Type Variables
# =============================================================================

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


# =============================================================================
# filter_effects: Only merge matching effects
# =============================================================================


def filter_effects(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    predicate: EffectPredicate,
) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Run task, only merge effects matching predicate.

    This combinator allows selective effect propagation. Effects that
    don't match the predicate are silently discarded.

    Args:
        task: The task to wrap. Can be a @task class or async callable.
        predicate: Function (effect) -> bool that returns True to keep

    Returns:
        A new task that filters effects before merging

    Example:
        # Only keep file effects, drop logging effects
        file_only = filter_effects(
            NoisyTask,  # @task classes work directly
            lambda e: isinstance(e, (FileCreate, FilePatch))
        )

        # Only keep effects for a specific context
        context_filtered = filter_effects(
            MyTask,
            lambda e: e.context_id == "my_context"
        )
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def filtering_task(input: InputT, scope: Scope) -> OutputT:
        # Fork for isolated execution
        child = scope.fork()

        try:
            # Execute task in fork
            result = await task(input, child)

            # Filter and merge effects
            for layer in child.effects:
                effect = layer.effect
                if predicate(effect):
                    scope.emit(effect)

            # Discard the fork (we've manually copied effects)
            child.discard()
            return result  # type: ignore[no-any-return]

        except Exception:
            child.discard()
            raise

    _set_combinator_name(filtering_task, "filter_effects", task)
    return filtering_task


# =============================================================================
# map_effects: Transform effects before merging
# =============================================================================


def map_effects(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    transform: Callable[[Effect], Effect],
) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Run task, transform effects before merging.

    This combinator allows modifying effects before they propagate to
    the parent scope. Common uses include:
    - Adding attribution (task_name, context_id)
    - Normalizing paths
    - Masking sensitive data

    Args:
        task: The task to wrap. Can be a @task class or async callable.
        transform: Function (effect) -> effect that transforms each effect

    Returns:
        A new task that transforms effects before merging

    Example:
        # Add attribution to all effects
        attributed = map_effects(
            MyTask,  # @task classes work directly
            lambda e: e.with_attribution(task_name="my_task")
        )

        # Normalize file paths
        normalized = map_effects(
            FileTask,
            lambda e: normalize_path(e) if hasattr(e, 'path') else e
        )
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def mapping_task(input: InputT, scope: Scope) -> OutputT:
        # Fork for isolated execution
        child = scope.fork()

        try:
            # Execute task in fork
            result = await task(input, child)

            # Transform and merge effects
            for layer in child.effects:
                effect = layer.effect
                transformed = transform(effect)
                scope.emit(transformed)

            # Discard the fork (we've manually copied effects)
            child.discard()
            return result  # type: ignore[no-any-return]

        except Exception:
            child.discard()
            raise

    _set_combinator_name(mapping_task, "map_effects", task)
    return mapping_task


# =============================================================================
# tap: Observe result and effects
# =============================================================================


def tap(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    observer: Callable[[OutputT, EffectStreamLike], Any],
) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Run task, call observer with result and effects.

    Observer is for side effects (logging, metrics) and does not affect
    the result or effects. The observer is called after the task completes
    but before effects are merged.

    Args:
        task: The task to observe. Can be a @task class or async callable.
        observer: Function (result, effects) -> None for side effects

    Returns:
        A new task that calls observer before returning

    Example:
        # Log results
        logged = tap(
            MyTask,  # @task classes work directly
            lambda result, effects: logger.info(f"Got {result}, {len(effects)} effects")
        )

        # Collect metrics
        monitored = tap(
            ProcessTask,
            lambda r, e: metrics.record(task_name, len(e), e.count(FilePatch))
        )
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def tapping_task(input: InputT, scope: Scope) -> OutputT:
        # Fork for isolated execution
        child = scope.fork()

        try:
            # Execute task in fork
            result = await task(input, child)

            # Call observer (may be sync or async)
            effects = child.effects
            observer_result = observer(result, effects)
            if inspect.isawaitable(observer_result):
                await observer_result

            # Merge effects to parent
            scope.merge(child)
            return result  # type: ignore[no-any-return]

        except Exception:
            child.discard()
            raise

    _set_combinator_name(tapping_task, "tap", task)
    return tapping_task


# =============================================================================
# scope_tap: Observe child scope
# =============================================================================


def scope_tap(
    task: Task[InputT, OutputT],  # type: ignore[type-arg]
    observer: Callable[[Scope], Any],
) -> Task[InputT, OutputT]:  # type: ignore[type-arg]
    """Run task, call observer with the child scope.

    This is a lower-level tap that gives access to the full scope,
    useful for debugging or advanced introspection. The observer is
    called after the task completes but before effects are merged.

    Args:
        task: The task to observe. Can be a @task class or async callable.
        observer: Function (scope) -> None for side effects

    Returns:
        A new task that calls observer before returning

    Example:
        # Debug scope state
        debugged = scope_tap(
            MyTask,  # @task classes work directly
            lambda s: print(f"Scope {s.id}: {len(s.effects)} effects")
        )

        # Inspect bindings
        inspected = scope_tap(
            OtherTask,
            lambda s: log_bindings(s)
        )
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def scope_tapping_task(input: InputT, scope: Scope) -> OutputT:
        # Fork for isolated execution
        child = scope.fork()

        try:
            # Execute task in fork
            result = await task(input, child)

            # Call observer (may be sync or async)
            observer_result = observer(child)
            if inspect.isawaitable(observer_result):
                await observer_result

            # Merge effects to parent
            scope.merge(child)
            return result  # type: ignore[no-any-return]

        except Exception:
            child.discard()
            raise

    _set_combinator_name(scope_tapping_task, "scope_tap", task)
    return scope_tapping_task


__all__ = [
    "filter_effects",
    "map_effects",
    "scope_tap",
    "tap",
]
