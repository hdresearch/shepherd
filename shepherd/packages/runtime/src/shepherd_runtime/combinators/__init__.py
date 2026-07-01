"""Runtime-owned combinators for higher-order task composition.

Combinators are built from the four primitives (fork, merge, discard,
materialize) and provide reusable patterns for task composition.

Gating (conditional commit based on predicates):
    gate            Run task, commit only if predicate passes
    budget          Reject if resource limits exceeded
    timeout         Reject if execution time exceeded

Retry (automatic retry and recovery):
    retry           Retry task until success or max attempts
    fallback        Try tasks in order until one succeeds
    recover         Provide default value on error

Parallel (concurrent execution with conflict detection):
    parallel        Run two tasks in parallel
    race            First to complete wins
    parallel_all    Run N tasks in parallel

Composition (sequential and branching):
    sequence        Chain tasks (output -> input)
    sequence_all    Chain N tasks in sequence
    branch          Choose task based on predicate
    loop            Repeat task until condition

Effects (transform effect streams):
    filter_effects  Only merge effects matching predicate
    map_effects     Transform effects before merging
    tap             Observe result and effects
    scope_tap       Observe child scope

Types:
    Rejected        Result container for rejected gated execution
    Budget          Resource limits for budgeted execution
    MergeStrategy   Strategy for merging parallel task effects
    DisjointMerge   Fails on any context overlap (safe default)
    LastWriteWins   Later effects overwrite earlier

Errors:
    EffectConflictError  Raised when parallel tasks conflict

All combinators return new Task instances. They compose freely.

Example:
    from shepherd_runtime.combinators import gate, retry, parallel

    # Gate a task to only commit if result is valid
    validated = gate(my_task, lambda r, e: r.is_valid)

    # Retry with exponential backoff
    reliable = retry(flaky_task, max_attempts=5, delay_seconds=1, backoff=2)

    # Run independent tasks in parallel
    combined = parallel(task_a, task_b)

See Also:
    design/syntax-api/DESIGN-combinators-library.md - Full specification
    design/syntax-api/DESIGN-primitives-layer.md - Foundation primitives
"""

from __future__ import annotations

# Composition
from .composition import (
    branch,
    loop,
    sequence,
    sequence_all,
)

# Effects
from .effects import (
    filter_effects,
    map_effects,
    scope_tap,
    tap,
)

# Gating
from .gating import (
    budget,
    gate,
    timeout,
)

# Parallel
from .parallel import (
    EffectConflictError,
    parallel,
    parallel_all,
    race,
)

# Retry
from .retry import (
    fallback,
    recover,
    retry,
)

# Speculation
from .speculation import (
    SpeculativeResult,
    speculate,
)

# Types
from .types import (
    Budget,
    DisjointMerge,
    EffectPredicate,
    JudgePredicate,
    LastWriteWins,
    MergeStrategy,
    Predicate,
    Rejected,
    Task,
    ensure_task_fn,
    eval_predicate,
    is_task_class,
)

__all__ = [
    "Budget",
    "DisjointMerge",
    # Errors
    "EffectConflictError",
    "EffectPredicate",
    "JudgePredicate",
    "LastWriteWins",
    # Merge strategies
    "MergeStrategy",
    "Predicate",
    # Result types
    "Rejected",
    # Speculation
    "SpeculativeResult",
    # Type aliases
    "Task",
    "branch",
    "budget",
    "ensure_task_fn",
    # Helpers
    "eval_predicate",
    "fallback",
    # Effects
    "filter_effects",
    # Gating
    "gate",
    # Task detection and adaptation
    "is_task_class",
    "loop",
    "map_effects",
    # Parallel
    "parallel",
    "parallel_all",
    "race",
    "recover",
    # Retry
    "retry",
    "scope_tap",
    # Composition
    "sequence",
    "sequence_all",
    "speculate",
    "tap",
    "timeout",
]
