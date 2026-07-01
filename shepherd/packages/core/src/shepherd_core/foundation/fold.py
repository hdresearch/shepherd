"""Layer 0: The fold - the core invariant that defines Shepherd's semantics.

THE CORE INVARIANT:
    state(t) = fold(apply, effects[0:t], initial)

This single equation defines Shepherd's semantics:
- State is DERIVED, never stored separately
- Effects are the single source of truth
- Time travel is free (recompute from any point)
- Replay is deterministic (same effects = same state)

This is a CATAMORPHISM - the fundamental pattern for consuming
structured data.

See Also:
    design/syntax-api/DESIGN-primitives-layer.md - Full specification
    design/effect-system/FOUNDATIONS-unified-theory.md - Theoretical foundations
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

S = TypeVar("S")  # State type
E = TypeVar("E")  # Effect type


def fold(
    apply: Callable[[S, E], S],
    effects: Iterable[E],
    initial: S,
) -> S:
    """Compute state by folding apply over effects.

    THE CORE INVARIANT:
        state(t) = fold(apply, effects[0:t], initial)

    Args:
        apply: Function that applies one effect to state
        effects: Ordered sequence of effects
        initial: Starting state

    Returns:
        Final state after all effects applied

    Example:
        # Compute workspace state from effects
        def apply_to_workspace(ws: WorkspaceRef, effect: Effect) -> WorkspaceRef:
            match effect:
                case WorkspacePatchCaptured(patch=patch):
                    return ws.with_patch(patch)
                case _:
                    return ws

        current_state = fold(apply_to_workspace, stream.effects, initial_workspace)
    """
    state = initial
    for effect in effects:
        state = apply(state, effect)
    return state


def fold_with_index(
    apply: Callable[[S, E, int], S],
    effects: Iterable[E],
    initial: S,
) -> S:
    """Fold with effect index available.

    Useful when the apply function needs positional information.

    Args:
        apply: Function (state, effect, index) -> new_state
        effects: Ordered sequence of effects
        initial: Starting state

    Returns:
        Final state after all effects applied

    Example:
        def apply_with_index(state, effect, idx):
            print(f"Applying effect {idx}: {effect}")
            return state.apply(effect)

        final = fold_with_index(apply_with_index, effects, initial)
    """
    state = initial
    for i, effect in enumerate(effects):
        state = apply(state, effect, i)
    return state


def scan(
    apply: Callable[[S, E], S],
    effects: Iterable[E],
    initial: S,
) -> Iterator[S]:
    """Like fold, but yields intermediate states.

    Useful for time-travel debugging: see state at each point.

    Args:
        apply: Function that applies one effect to state
        effects: Ordered sequence of effects
        initial: Starting state

    Yields:
        State after each effect (including initial)

    Example:
        for i, state in enumerate(scan(apply, effects, initial)):
            print(f"State at t={i}: {state}")
    """
    yield initial
    state = initial
    for effect in effects:
        state = apply(state, effect)
        yield state


def fold_until(
    apply: Callable[[S, E], S],
    effects: Iterable[E],
    initial: S,
    predicate: Callable[[S], bool],
) -> tuple[S, int]:
    """Fold until predicate is satisfied.

    Useful for finding the first state that matches a condition,
    or implementing early termination in state computation.

    Args:
        apply: Function that applies one effect to state
        effects: Ordered sequence of effects
        initial: Starting state
        predicate: Stop when this returns True

    Returns:
        Tuple of (final_state, effects_processed)

    Example:
        # Find state when error occurred
        def has_error(state):
            return state.has_error

        error_state, n = fold_until(apply, effects, initial, has_error)
        print(f"Error occurred after {n} effects")
    """
    state = initial
    count = 0
    for effect in effects:
        state = apply(state, effect)
        count += 1
        if predicate(state):
            break
    return state, count


__all__ = ["fold", "fold_until", "fold_with_index", "scan"]
