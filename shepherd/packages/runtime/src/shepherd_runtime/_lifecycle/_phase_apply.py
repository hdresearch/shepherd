"""ApplyPhase: Apply effects to derive new state (pure).

Phase 6 of the lifecycle pipeline. This phase applies extracted effects
to contexts to derive new immutable state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shepherd_core.effects import ContextCaptured

from ._phase_base import PhaseBase

if TYPE_CHECKING:
    from shepherd_core.context.kernel import ExecutionContext

    from ._emitter import EffectEmitter
    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class ApplyPhase(PhaseBase):
    """Phase 6: Apply effects to derive new context state.

    Reads: prepared_contexts, context_effects, scope
    Writes: context_outputs
    Side effects: scope.update_context() (if auto_update=True)

    Apply derives new immutable context state from effects.

    Effect Application Strategy
    ---------------------------
    Each context only receives its OWN effects (from context_effects dict),
    not all extracted effects. This is more efficient and semantically correct:
    - WorkspaceRef only applies FilePatch, FileCreate effects it emitted
    - SessionState only applies SessionUpdated effects it emitted
    """

    def __init__(self, emitter: EffectEmitter, auto_update: bool = True) -> None:
        self._emitter = emitter
        self._auto_update = auto_update

    @property
    def name(self) -> str:
        return "apply"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        # Skip if cache hit (no effects to apply)
        if ctx.cache_hit:
            logger.debug("Skipping apply phase - cache hit")
            return ctx.with_context_outputs({})

        context_outputs: dict[str, ExecutionContext] = {}

        for name, current in ctx.prepared_contexts.items():
            old_context_id = current.context_id

            # Get effects specific to THIS context only
            effects_for_context = ctx.context_effects.get(name, ())

            # Apply only this context's effects to derive new state
            new_context = current
            for effect in effects_for_context:
                new_context = new_context.apply_effect(effect)

            context_outputs[name] = new_context

            # Emit capture effect for audit
            self._emitter.emit(
                ContextCaptured(
                    context_id=old_context_id,
                    binding_name=name,
                    old_context_id=old_context_id,
                    new_context_id=new_context.context_id,
                    effect_count=len(effects_for_context),
                    task_name=ctx.task_name,
                    provider_id=ctx.effective_provider_id,
                )
            )

            # Update scope binding
            if self._auto_update:
                ctx.scope.update_context(name, new_context)

            logger.debug(
                "Applied %d effects to '%s'",
                len(effects_for_context),
                name,
            )

        return ctx.with_context_outputs(context_outputs)


__all__ = ["ApplyPhase"]
