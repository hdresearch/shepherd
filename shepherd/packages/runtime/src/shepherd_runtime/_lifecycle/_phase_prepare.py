"""PreparePhase: Prepare contexts and create sandboxes (has rollback).

Phase 2 of the lifecycle pipeline. This phase prepares execution contexts
and creates sandboxes for isolated execution.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shepherd_core.types import PreparationError

from ._cleanup import cleanup_contexts, discard_sandboxes
from ._phase_base import PhaseBase

if TYPE_CHECKING:
    from shepherd_core.context.kernel import ExecutionContext

    from ..context import Sandbox
    from ..sandbox_registry import SandboxRegistry
    from ._emitter import EffectEmitter
    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class PreparePhase(PhaseBase):
    """Phase 2: Prepare contexts and create sandboxes.

    Reads: bindings, scope
    Writes: prepared_contexts, sandboxes
    Side effects: scope.update_context(), scope.mark_binding_lifecycle(), sandbox.setup()
    Effects emitted: ContextPrepared for each successfully prepared context

    On failure, rollback cleans up already-prepared contexts and sandboxes.
    """

    def __init__(
        self,
        sandbox_registry: SandboxRegistry,
        emitter: EffectEmitter | None = None,
    ) -> None:
        self._sandbox_registry = sandbox_registry
        self._emitter = emitter

    @property
    def name(self) -> str:
        return "prepare"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        prepared_contexts: dict[str, ExecutionContext] = {}
        sandboxes: dict[str, Sandbox] = {}

        for binding in ctx.bindings:
            binding_id = binding.context.context_id
            try:
                # Prepare context (may have side effects)
                prepared = binding.context.prepare()

                # Store in our dict for reliable rollback
                prepared_contexts[binding.name] = prepared

                # Update scope with prepared context
                ctx.scope.update_context(binding.name, prepared)
                ctx.scope.mark_binding_lifecycle(binding.name, is_prepared=True)

                # Emit ContextPrepared effect
                if self._emitter is not None:
                    self._emitter.emit_context_prepared(
                        context_id=prepared.context_id,
                        binding_name=binding.name,
                        task_name=ctx.task_name,
                        provider_id=ctx.effective_provider_id,
                    )

                # Create sandbox if factory is registered
                sandbox = self._sandbox_registry.create_for(prepared)
                if sandbox is not None:
                    sandbox.setup(prepared)
                    sandboxes[binding.name] = sandbox

                logger.debug("Prepared context '%s'", binding.name)

            except Exception as e:  # noqa: BLE001
                # Store what we've prepared so far in ctx for rollback
                ctx = ctx.with_prepared(prepared_contexts, sandboxes)

                # Use base class helper for standardized error handling:
                # 1. Calls self.rollback() with ctx containing partial state
                # 2. Wraps error in PreparationError
                # 3. Re-raises (never returns)
                await self._handle_error_with_rollback(
                    ctx,
                    e,
                    wrap_error=lambda err, binding_id=binding_id: PreparationError(  # type: ignore[misc]
                        binding_id,
                        str(err),
                        cause=err,
                    ),
                )

        return ctx.with_prepared(prepared_contexts, sandboxes)

    async def rollback(self, ctx: PhaseContext, error: Exception) -> PhaseContext:
        """Cleanup already-prepared contexts on failure.

        Uses prepared_contexts from PhaseContext (not scope) to ensure
        we clean up exactly what we prepared, even if scope was modified.

        Returns:
            Updated PhaseContext with cleanup state tracked (cleaned_up_contexts,
            discarded_sandboxes). This ensures CleanupPhase won't double-cleanup.
        """
        errors: list[tuple[str, Exception]] = []

        # Discard sandboxes first
        ctx = discard_sandboxes(ctx, errors)

        # Cleanup contexts in reverse order (no effect emission during rollback)
        ctx = await cleanup_contexts(ctx, error, errors)

        # Log any errors (PreparePhase doesn't aggregate them into CleanupError)
        for resource, exc in errors:
            logger.warning("Rollback error for %s: %s", resource, exc)

        return ctx


__all__ = ["PreparePhase"]
