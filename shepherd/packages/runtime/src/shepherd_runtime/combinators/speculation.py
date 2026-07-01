"""Speculation combinators: manual commit/abandon control.

This module provides combinators for speculative execution with manual
control over effect propagation:

- speculate: Run task and return result with commit/abandon methods

Unlike gate() which auto-commits/discards based on a predicate, speculate()
returns a SpeculativeResult that the caller can inspect and decide on.

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from .types import Task, _set_combinator_name, ensure_task_fn

if TYPE_CHECKING:
    from collections.abc import Callable

    from shepherd_runtime.scope import Scope
    from shepherd_runtime.scope_types import EffectStreamLike

# =============================================================================
# Type Variables
# =============================================================================

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
T = TypeVar("T")


# =============================================================================
# SpeculativeResult: Container for speculative execution result
# =============================================================================


@dataclass
class SpeculativeResult(Generic[OutputT]):
    """Result of speculative execution with manual commit/abandon control.

    This class holds the result of a speculatively executed task along with
    its captured effects. The caller can inspect the result and effects,
    then decide whether to commit (merge effects to parent) or abandon
    (discard effects).

    Attributes:
        output: The task's return value
        effects: The effects that would be applied on commit

    Example:
        result = await speculate(fix_bug)(bug_info, scope)

        # Inspect result
        print(f"Fix: {result.output}")
        print(f"Would create {len(result.effects)} effects")

        # Decide
        if user_approves(result.output):
            result.commit()
        else:
            result.abandon()

    Context manager pattern (auto-abandons if not committed):
        with await speculate(task)(input, scope) as result:
            if approved:
                result.commit()
            # Auto-abandons if not committed
    """

    output: OutputT
    effects: EffectStreamLike
    _scope: Scope = field(repr=False)
    _parent: Scope = field(repr=False)
    _decided: bool = field(default=False, repr=False)

    def commit(self) -> OutputT:
        """Merge effects to parent scope and return output.

        After calling commit(), the effects captured during speculative
        execution are merged into the parent scope's stream.

        Returns:
            The output value (same as self.output)

        Raises:
            ValueError: If already committed or abandoned

        Example:
            result = await speculate(task)(input, scope)
            if looks_good(result.output):
                final_output = result.commit()
        """
        if self._decided:
            raise ValueError("SpeculativeResult already committed or abandoned")
        self._decided = True
        self._parent.merge(self._scope)
        return self.output

    def abandon(self) -> None:
        """Discard effects without merging to parent (idempotent).

        After calling abandon(), the effects are discarded and will not
        appear in the parent scope. This is safe to call multiple times.

        Example:
            result = await speculate(task)(input, scope)
            if not approved:
                result.abandon()
        """
        if self._decided:
            return  # Idempotent
        self._decided = True
        if not self._scope.is_discarded:
            self._scope.discard()

    @property
    def is_decided(self) -> bool:
        """Whether commit() or abandon() has been called."""
        return self._decided

    def map(self, f: Callable[[OutputT], T]) -> SpeculativeResult[T]:
        """Transform output value while preserving speculative context.

        Creates a new SpeculativeResult with transformed output. The
        effects and scope state are shared with the original.

        Args:
            f: Function to transform the output

        Returns:
            New SpeculativeResult with transformed output

        Example:
            result = await speculate(parse_json)(data, scope)
            string_result = result.map(lambda obj: obj['name'])
            if validate(string_result.output):
                string_result.commit()
        """
        return SpeculativeResult(
            output=f(self.output),
            effects=self.effects,
            _scope=self._scope,
            _parent=self._parent,
            _decided=self._decided,
        )

    def __enter__(self) -> SpeculativeResult[OutputT]:
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        """Exit context manager, auto-abandoning if not decided."""
        if not self._decided:
            self.abandon()

    def __del__(self):  # type: ignore[no-untyped-def]
        """Warn if garbage collected without explicit decision."""
        if not self._decided and not self._scope.is_discarded:
            warnings.warn(
                "SpeculativeResult was garbage collected without commit/abandon. "
                "Use context manager or explicitly finalize.",
                ResourceWarning,
                stacklevel=2,
            )
            self.abandon()


# =============================================================================
# speculate: Run task speculatively
# =============================================================================


def speculate(task: Task[InputT, OutputT]) -> Task[InputT, SpeculativeResult[OutputT]]:  # type: ignore[type-arg]
    """Run task speculatively, returning result with manual commit/abandon.

    Unlike gate() which automatically commits or discards based on a
    predicate, speculate() gives the caller full control. The returned
    SpeculativeResult contains the task's output and captured effects,
    allowing inspection before deciding.

    Args:
        task: The task to run speculatively. Can be a @task class or async callable.

    Returns:
        A new task that returns SpeculativeResult instead of raw output

    Example - Manual decision:
        result = await speculate(FixBugTask)(bug_info, scope)  # @task class

        # Can inspect output and effects
        print(f"Proposed fix: {result.output}")
        print(f"Would modify {len(result.effects)} things")

        # Manual decision
        if user_approves(result.output):
            result.commit()  # Effects merged to parent
        else:
            result.abandon()  # Effects discarded

    Example - Context manager (auto-abandon):
        with await speculate(MyTask)(input, scope) as result:
            if result.output.is_valid:
                result.commit()
            # Auto-abandons on exit if not committed

    Example - Chaining:
        # speculate() composes with other combinators
        speculative_with_timeout = speculate(timeout(SlowTask, 30))

    Implementation:
        1. Fork scope for isolated execution
        2. Execute task in fork
        3. Return SpeculativeResult wrapping output and fork
        4. On exception: discard fork and re-raise
    """
    # Auto-adapt @task classes to callable
    task = ensure_task_fn(task)

    async def speculating_task(input: InputT, scope: Scope) -> SpeculativeResult[OutputT]:
        child = scope.fork()
        try:
            result = await task(input, child)
            return SpeculativeResult(
                output=result,
                effects=child.effects,
                _scope=child,
                _parent=scope,
            )
        except Exception:
            child.discard()
            raise

    _set_combinator_name(speculating_task, "speculate", task)
    return speculating_task


__all__ = [
    "SpeculativeResult",
    "speculate",
]
