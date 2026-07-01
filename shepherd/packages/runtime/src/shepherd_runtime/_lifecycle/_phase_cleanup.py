"""CleanupPhase: Cleanup contexts and discard sandboxes (idempotent).

Phase 7 of the lifecycle pipeline. This phase performs cleanup of all
contexts and sandboxes, handling errors gracefully.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._cleanup import cleanup_contexts, discard_sandboxes
from ._phase_base import PhaseBase
from ._phase_context import CleanupError

if TYPE_CHECKING:
    from ._emitter import EffectEmitter
    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class CleanupPhase(PhaseBase):
    """Phase 7: Cleanup all contexts and discard sandboxes.

    Reads: prepared_contexts, sandboxes, error, bindings, scope,
           cleaned_up_contexts, discarded_sandboxes
    Writes: cleaned_up_contexts, discarded_sandboxes, cleanup_errors
    Side effects: sandbox.discard(), context.cleanup(), scope.mark_binding_lifecycle()

    Cleanup always runs, even on error. Uses ctx.error to know if cleanup
    is happening due to failure.

    Idempotent Cleanup
    ------------------
    CleanupPhase tracks what has already been cleaned up (via cleaned_up_contexts
    and discarded_sandboxes in PhaseContext). This prevents double-cleanup when:
    1. PreparePhase.rollback() runs due to prepare failure
    2. CleanupPhase.execute() runs in __aexit__

    Both can safely call cleanup operations - the second call is a no-op.

    Error Handling
    --------------
    CleanupPhase NEVER raises exceptions. All errors are:
    1. Logged as warnings
    2. Recorded in ctx.cleanup_errors for inspection
    3. Reported in ContextCleanedUp effects

    This ensures cleanup completes for all contexts even if some fail.
    """

    def __init__(self, emitter: EffectEmitter) -> None:
        self._emitter = emitter

    @property
    def name(self) -> str:
        return "cleanup"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        # Use error from context (set by pipeline on failure)
        error = ctx.error
        errors: list[tuple[str, Exception]] = []

        # Discard sandboxes first (skip already discarded)
        ctx = discard_sandboxes(ctx, errors)

        # Cleanup contexts in reverse preparation order with effect emission
        ctx = await cleanup_contexts(
            ctx,
            error,
            errors,
            emit_effects=True,
            emitter=self._emitter,
            mark_out_of_lifecycle=False,  # We handle this separately below
        )

        # Unmark all bindings from lifecycle
        for binding in ctx.bindings:
            ctx.scope.mark_binding_lifecycle(binding.name, in_lifecycle=False)

        # Convert error tuples to CleanupError and record in context
        if errors:
            cleanup_errors = [CleanupError(name, exc) for name, exc in errors]
            ctx = ctx.with_cleanup_errors(cleanup_errors)
            logger.error(
                "Cleanup completed with %d errors: %s",
                len(cleanup_errors),
                [err.resource_name for err in cleanup_errors],
            )

        return ctx

    async def rollback(self, ctx: PhaseContext, error: Exception) -> PhaseContext:
        """CleanupPhase rollback is a no-op - cleanup itself doesn't need rollback."""
        return ctx


__all__ = ["CleanupPhase"]
