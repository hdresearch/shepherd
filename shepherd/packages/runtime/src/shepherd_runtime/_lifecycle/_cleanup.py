"""Shared cleanup utilities for sandbox and context teardown.

Used by:
- PreparePhase.rollback() - emergency cleanup on prepare failure
- CleanupPhase.execute() - normal cleanup during __aexit__

This module provides idempotent cleanup functions that track what has
already been cleaned up via PhaseContext's cleanup tracking.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._emitter import EffectEmitter
    from ._phase_context import PhaseContext

__all__ = ["cleanup_contexts", "discard_sandboxes"]

logger = logging.getLogger(__name__)


def discard_sandboxes(
    ctx: PhaseContext,
    errors: list[tuple[str, Exception]],
) -> PhaseContext:
    """Discard all sandboxes, collecting errors.

    Idempotent: skips already-discarded sandboxes (tracked via ctx.is_sandbox_discarded).

    Args:
        ctx: Phase context with sandboxes to discard
        errors: List to append (resource_name, exception) pairs to

    Returns:
        Updated PhaseContext with sandbox discard state tracked
    """
    for name, sandbox in ctx.sandboxes.items():
        if ctx.is_sandbox_discarded(name):
            logger.debug("Sandbox for '%s' already discarded, skipping", name)
            continue

        try:
            sandbox.discard()
            ctx = ctx.mark_sandbox_discarded(name)
            logger.debug("Discarded sandbox for '%s'", name)
        except Exception as e:  # noqa: BLE001
            errors.append((f"sandbox:{name}", e))
            logger.warning(
                "Sandbox discard failed for '%s': %s",
                name,
                e,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    return ctx


async def cleanup_contexts(
    ctx: PhaseContext,
    error: Exception | None,
    errors: list[tuple[str, Exception]],
    *,
    emit_effects: bool = False,
    emitter: EffectEmitter | None = None,
    mark_out_of_lifecycle: bool = False,
) -> PhaseContext:
    """Cleanup contexts in reverse preparation order.

    Idempotent: skips already-cleaned-up contexts (tracked via ctx.is_cleaned_up).

    Args:
        ctx: Phase context with prepared_contexts to clean up
        error: Error that triggered cleanup (or None for normal cleanup)
        errors: List to append (resource_name, exception) pairs to
        emit_effects: Whether to emit ContextCleanedUp effects
        emitter: Effect emitter (required if emit_effects=True)
        mark_out_of_lifecycle: Whether to also mark in_lifecycle=False on bindings

    Returns:
        Updated PhaseContext with cleanup state tracked
    """
    from shepherd_core.effects import ContextCleanedUp

    for name in reversed(list(ctx.prepared_contexts.keys())):
        context = ctx.prepared_contexts[name]
        already_cleaned = ctx.is_cleaned_up(name)

        if already_cleaned:
            logger.debug("Context '%s' already cleaned up, skipping", name)
        else:
            try:
                context.cleanup(error)
                ctx = ctx.mark_cleaned_up(name)
                logger.debug("Cleaned up context '%s'", name)
            except Exception as e:  # noqa: BLE001
                errors.append((f"context:{name}", e))
                logger.warning(
                    "Cleanup failed for '%s': %s",
                    name,
                    e,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )

        # Emit audit trail if requested
        if emit_effects and emitter is not None:
            emitter.emit(
                ContextCleanedUp(
                    context_id=context.context_id,
                    binding_name=name,
                    had_error=error is not None,
                    already_cleaned=already_cleaned,
                    task_name=ctx.task_name,
                    provider_id=ctx.effective_provider_id,
                )
            )

        # Mark binding lifecycle state
        ctx.scope.mark_binding_lifecycle(name, is_prepared=False)
        if mark_out_of_lifecycle:
            ctx.scope.mark_binding_lifecycle(name, in_lifecycle=False)

    return ctx
