"""Type definitions for scope module.

This module provides scope-related type definitions that are used across
the scope package but don't belong in any specific module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaterializationSummary:
    """Summary of a materialization operation.

    Returned by scope.materialize() to indicate what was processed.
    Provides counts for tracking and debugging.

    Attributes:
        effects_processed: Total effects in the pending batch
        effects_materialized: Effects that actually escaped (had materializers)
        total_paths_affected: Total filesystem paths affected across all effects
        rollback_errors: Errors encountered during rollback (if any).
            Each entry is a tuple of (effect_type_name, error_message).
            Empty tuple if no rollback was needed or rollback succeeded.
    """

    effects_processed: int
    effects_materialized: int
    total_paths_affected: int
    rollback_errors: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        """True if any effects were actually materialized.

        Allows usage like:
            summary = scope.materialize()
            if summary:
                print("Changes were applied")
        """
        return self.effects_materialized > 0

    @property
    def rollback_failed(self) -> bool:
        """True if rollback encountered errors.

        Use this to check if the system is in a partially-rolled-back state:
            summary = scope.materialize()
            if summary.rollback_failed:
                logger.error("Partial rollback: %s", summary.rollback_errors)
        """
        return len(self.rollback_errors) > 0


__all__ = ["MaterializationSummary"]
