"""Core types for combinators.

This module defines the foundational types used across all combinators:
- Type aliases for predicates (sync and async)
- Rejected: Result container for gated execution that failed predicate
- Budget: Resource limits for budgeted execution
- MergeStrategy: Strategies for merging parallel task effects
- eval_predicate: Helper to evaluate sync or async predicates

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
    design/syntax-api/DESIGN-primitives-layer.md - Foundation primitives
    design/effect-system/FOUNDATIONS-unified-theory.md - Theoretical foundations
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from shepherd_core.effects import Effect

    from shepherd_runtime.scope import Scope
    from shepherd_runtime.scope_types import EffectStreamLike

# =============================================================================
# Type Variables
# =============================================================================

InputT = TypeVar("InputT")  # Input type
OutputT = TypeVar("OutputT")  # Output type
T = TypeVar("T")  # Generic type

# Task is any async callable (input, scope) -> output
Task = Callable[[Any, "Scope"], Awaitable[Any]]


# =============================================================================
# Task Class Detection and Auto-Adaptation
# =============================================================================


def is_task_class(obj: Any) -> bool:
    """Check if obj is a @task-decorated class.

    Uses duck-typing to avoid circular imports. A @task class has:
    - _task_meta attribute (set by @task decorator)
    - arun classmethod (provided by TaskMixin)

    Args:
        obj: Any object to check

    Returns:
        True if obj is a @task class, False otherwise

    Example:
        @task
        class MyTask(BaseModel):
            topic: Input(str)
            result: Output(str)

        is_task_class(MyTask)  # True
        is_task_class(lambda x, s: x)  # False
    """
    return (
        isinstance(obj, type)
        and hasattr(obj, "_task_meta")
        and hasattr(obj, "arun")
        and callable(getattr(obj, "arun", None))
    )


def ensure_task_fn(task: Any) -> Task:
    """Ensure task is a combinator-compatible callable.

    If task is a @task class, it's automatically adapted to the combinator
    signature (inputs: dict, scope: Scope) -> Awaitable[T]. If it's already
    a callable, it's returned as-is.

    This enables combinators to accept either:
    - @task classes directly: retry(WriteCode, max_attempts=3)
    - Pre-adapted callables: retry(task_fn(WriteCode), max_attempts=3)
    - Raw async functions: retry(my_async_fn, max_attempts=3)

    Args:
        task: Either a @task class or a combinator-compatible callable

    Returns:
        A combinator-compatible async callable

    Example:
        # These are now equivalent:
        retry(WriteCode, max_attempts=3)
        retry(task_fn(WriteCode), max_attempts=3)

        # Raw callables pass through unchanged:
        async def my_task(inputs, scope):
            return inputs["x"] * 2
        ensure_task_fn(my_task)  # Returns my_task unchanged
    """
    if is_task_class(task):
        # Create an inline adapter to avoid importing from shepherd package
        task_class = task

        async def adapted_task(inputs: dict[str, Any], scope: Scope) -> Any:
            return await task_class.arun(scope=scope, **inputs)

        # Preserve identity for debugging
        adapted_task.__name__ = getattr(task_class, "__name__", "Task")
        adapted_task.__qualname__ = getattr(task_class, "__qualname__", adapted_task.__name__)
        return adapted_task

    # Already a callable - return as-is
    return task  # type: ignore[no-any-return]


# =============================================================================
# Type Aliases for Predicates
# =============================================================================

# Simple predicate on a value (sync or async)
Predicate = Callable[[Any], bool] | Callable[[Any], Awaitable[bool]]

# Predicate on an effect (sync only, used for filtering)
EffectPredicate = Callable[["Effect"], bool]

# Judge predicate that receives both result and effects (sync or async)
# Used by gate() to decide whether to merge or discard
JudgePredicate = Callable[[Any, "EffectStreamLike"], bool] | Callable[[Any, "EffectStreamLike"], Awaitable[bool]]


# =============================================================================
# Predicate Evaluation Helper
# =============================================================================


async def eval_predicate(predicate: Callable[..., Any], *args: Any) -> bool:
    """Evaluate a predicate that may be sync or async.

    This helper is used by combinators to support both synchronous and
    asynchronous predicates uniformly.

    Args:
        predicate: A callable that returns bool or Awaitable[bool]
        *args: Arguments to pass to the predicate

    Returns:
        The boolean result of the predicate

    Example:
        # Works with sync predicates
        result = await eval_predicate(lambda x: x > 0, 5)

        # Works with async predicates
        async def async_check(x):
            await asyncio.sleep(0.1)
            return x > 0
        result = await eval_predicate(async_check, 5)
    """
    result = predicate(*args)
    if inspect.isawaitable(result):
        return await result  # type: ignore[no-any-return]
    return result  # type: ignore[no-any-return]


# =============================================================================
# Rejected: Result Container for Gated Execution
# =============================================================================


@dataclass
class Rejected(Generic[OutputT]):
    """Result rejected by a gate predicate.

    When a gated task's predicate returns False, the effects are discarded
    and a Rejected instance is returned instead of the raw value.

    This allows callers to:
    - Distinguish rejected results from successful ones
    - Access the would-be result and effects for debugging
    - Provide fallback values via unwrap_or()

    Attributes:
        value: The result that was rejected
        effects: The effects that were discarded
        reason: Optional explanation for why the result was rejected

    Example:
        result = await gated_task(input, scope)
        if isinstance(result, Rejected):
            print(f"Rejected: {result.reason}")
            fallback = result.unwrap_or(default_value)
        else:
            # Normal processing
            process(result)
    """

    value: OutputT
    effects: EffectStreamLike
    reason: str | None = None

    def map(self, f: Callable[[OutputT], T]) -> Rejected[T]:
        """Transform the rejected value while preserving Rejected status.

        Useful for maintaining type consistency in pipelines.
        """
        return Rejected(value=f(self.value), effects=self.effects, reason=self.reason)

    def or_else(self, default: OutputT) -> OutputT:
        """Return default value (alias for unwrap_or)."""
        return default

    def unwrap_or(self, default: OutputT) -> OutputT:
        """Return default value, ignoring the rejected value."""
        return default

    def __repr__(self) -> str:
        reason_str = f", reason={self.reason!r}" if self.reason else ""
        return f"Rejected(value={self.value!r}{reason_str})"


# =============================================================================
# Budget: Resource Limits for Budgeted Execution
# =============================================================================


@dataclass
class Budget:
    """Resource limits for budgeted execution.

    Budget allows constraining task execution by various metrics:
    - Number of effects emitted
    - Number of files modified
    - Number of lines changed
    - Execution duration

    When any limit is exceeded, the task is rejected and its effects discarded.

    Attributes:
        max_effects: Maximum number of effects allowed
        max_files: Maximum number of files that can be modified
        max_lines: Maximum number of lines that can be changed
        max_duration_seconds: Maximum execution time in seconds

    Example:
        # Limit task to modifying at most 5 files in 30 seconds
        limited_task = budget(
            my_task,
            Budget(max_files=5, max_duration_seconds=30)
        )
    """

    max_effects: int | None = None
    max_files: int | None = None
    max_lines: int | None = None
    max_duration_seconds: float | None = None

    def check(self, effects: EffectStreamLike, duration: float = 0) -> tuple[bool, str | None]:
        """Check if the effects and duration are within budget.

        Args:
            effects: The stream of effects to check
            duration: Elapsed time in seconds

        Returns:
            Tuple of (within_budget, reason_if_exceeded)
            - (True, None) if all limits satisfied
            - (False, "reason") if any limit exceeded
        """
        # Check effect count
        if self.max_effects is not None and len(effects) > self.max_effects:
            return False, f"Effect limit exceeded: {len(effects)} > {self.max_effects}"

        # Check file count
        if self.max_files is not None:
            from shepherd_core.effects import FileCreate, FileDelete, FilePatch

            file_effects = (
                list(effects.query(FileCreate)) + list(effects.query(FilePatch)) + list(effects.query(FileDelete))
            )
            unique_files = {getattr(e.effect, "path", None) for e in file_effects}
            unique_files.discard(None)
            if len(unique_files) > self.max_files:
                return False, f"File limit exceeded: {len(unique_files)} > {self.max_files}"

        # Check line count (sum of lines in file patches)
        if self.max_lines is not None:
            from shepherd_core.effects import FilePatch

            total_lines = 0
            for layer in effects.query(FilePatch):
                patch = getattr(layer.effect, "patch", None)
                if patch and hasattr(patch, "patch"):
                    # Count lines in the diff
                    total_lines += patch.patch.count("\n")
            if total_lines > self.max_lines:
                return False, f"Line limit exceeded: {total_lines} > {self.max_lines}"

        # Check duration
        if self.max_duration_seconds is not None and duration > self.max_duration_seconds:
            return False, f"Duration limit exceeded: {duration:.2f}s > {self.max_duration_seconds}s"

        return True, None


# =============================================================================
# MergeStrategy: Strategies for Merging Parallel Task Effects
# =============================================================================


class MergeStrategy(ABC):
    """Abstract base for strategies that merge parallel task effects.

    When tasks run in parallel, their effects may conflict (e.g., both
    modifying the same file). MergeStrategy defines how to detect and
    handle such conflicts.

    Implementations:
        DisjointMerge: Fails if any context overlap (safe default)
        LastWriteWins: Later effects overwrite earlier (always succeeds)
    """

    @abstractmethod
    def can_merge(self, streams: list[EffectStreamLike]) -> tuple[bool, str | None]:
        """Check if streams can be merged without conflict.

        Args:
            streams: List of effect streams from parallel tasks

        Returns:
            Tuple of (can_merge, conflict_description)
            - (True, None) if streams can be merged
            - (False, "conflict details") if conflicts detected
        """
        ...

    @abstractmethod
    def merge(self, streams: list[EffectStreamLike]) -> EffectStreamLike:
        """Merge multiple streams into one.

        Args:
            streams: List of effect streams to merge

        Returns:
            Combined stream with all effects

        Note:
            Call can_merge() first if you need to check for conflicts.
            This method may produce unexpected results on conflicting streams.
        """
        ...


class DisjointMerge(MergeStrategy):
    """Merge strategy that fails on any context overlap.

    This is the safe default for parallel execution. It ensures that
    parallel tasks operate on completely disjoint resources.

    Conflict detection checks:
    - File paths: No two streams can modify the same file
    - Context IDs: No two streams can affect the same context

    Example:
        # These tasks can be merged (disjoint files)
        task_a modifies: ["src/a.py"]
        task_b modifies: ["src/b.py"]

        # These tasks CANNOT be merged (conflict)
        task_a modifies: ["src/shared.py"]
        task_b modifies: ["src/shared.py"]
    """

    def can_merge(self, streams: list[EffectStreamLike]) -> tuple[bool, str | None]:
        """Check for any resource overlap between streams."""
        from shepherd_core.effects import FileCreate, FileDelete, FilePatch

        # Collect all modified files per stream
        all_files: list[set[str]] = []
        for stream in streams:
            files: set[str] = set()
            for layer in stream:
                effect = layer.effect
                if isinstance(effect, (FileCreate, FilePatch, FileDelete)):
                    path = getattr(effect, "path", None)
                    if path:
                        files.add(path)
            all_files.append(files)

        # Check for overlaps
        conflicts: list[str] = []
        for i, files_i in enumerate(all_files):
            for j, files_j in enumerate(all_files):
                if i >= j:
                    continue
                overlap = files_i & files_j
                if overlap:
                    conflicts.extend(f"Stream {i} and {j} both modify: {f}" for f in overlap)

        if conflicts:
            return False, "; ".join(conflicts)
        return True, None

    def merge(self, streams: list[EffectStreamLike]) -> EffectStreamLike:
        """Merge streams by concatenating their layers."""
        from shepherd_runtime.scope_types import create_stream

        all_layers: list[Any] = []
        for stream in streams:
            all_layers.extend(stream.layers)

        # Sort by timestamp to maintain causal order
        all_layers.sort(key=lambda layer: layer.effect.timestamp)

        return create_stream(tuple(all_layers))


class LastWriteWins(MergeStrategy):
    """Merge strategy where later effects overwrite earlier ones.

    This strategy always succeeds - conflicts are resolved by taking
    the later effect. Use with caution as it may lose data.

    Useful when:
    - You know parallel tasks won't conflict in practice
    - Later results should genuinely supersede earlier ones
    - Performance is critical and conflict detection is expensive
    """

    def can_merge(self, streams: list[EffectStreamLike]) -> tuple[bool, str | None]:
        """Always returns True - last write wins handles all conflicts."""
        return True, None

    def merge(self, streams: list[EffectStreamLike]) -> EffectStreamLike:
        """Merge streams, sorting by timestamp (later effects win)."""
        from shepherd_runtime.scope_types import create_stream

        all_layers: list[Any] = []
        for stream in streams:
            all_layers.extend(stream.layers)

        # Sort by timestamp - later effects come last and "win"
        all_layers.sort(key=lambda layer: layer.effect.timestamp)

        return create_stream(tuple(all_layers))


def _set_combinator_name(fn: Callable[..., Any], name: str, *tasks: Any) -> None:
    """Set __name__ on a combinator wrapper for debugging."""
    parts = [getattr(t, "__name__", f"task{i}") for i, t in enumerate(tasks)]
    fn.__name__ = f"{name}({', '.join(parts)})"


__all__ = [
    "Budget",
    "DisjointMerge",
    "EffectPredicate",
    "JudgePredicate",
    "LastWriteWins",
    # Merge strategies
    "MergeStrategy",
    "Predicate",
    # Result types
    "Rejected",
    # Type aliases
    "Task",
    "ensure_task_fn",
    # Helpers
    "eval_predicate",
    # Task detection and adaptation
    "is_task_class",
]
