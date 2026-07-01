"""ExtractPhase: Extract effects from contexts (pure).

Phase 5 of the lifecycle pipeline. This phase extracts effects from all
prepared contexts using their sandbox state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._phase_base import PhaseBase

if TYPE_CHECKING:
    from shepherd_core.effects import Effect

    from ._emitter import EffectEmitter
    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class ExtractPhase(PhaseBase):
    """Phase 5: Extract effects from all contexts.

    Reads: prepared_contexts, sandboxes, result, scope, task_name, provider
    Writes: extracted_effects, context_effects

    Extract reads sandbox state but doesn't modify it.
    """

    def __init__(self, emitter: EffectEmitter) -> None:
        self._emitter = emitter

    @property
    def name(self) -> str:
        return "extract"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        # Skip if cache hit (no effects to extract)
        if ctx.cache_hit:
            logger.debug("Skipping extract phase - cache hit")
            return ctx.with_extracted_effects(all_effects=(), per_context={})

        all_effects: list[Effect] = []
        context_effects: dict[str, list[Effect]] = {}

        for name, context in ctx.prepared_contexts.items():
            sandbox = ctx.sandboxes.get(name)

            # Extract effects using v2 API
            effects = list(context.extract_effects(sandbox, ctx.result))  # type: ignore[arg-type]

            # Attribute and emit
            attributed: list[Effect] = []
            for effect in effects:
                attributed_effect = effect.with_attribution(
                    task_name=ctx.task_name,
                    provider_id=ctx.effective_provider_id,
                    context_id=context.context_id,
                    binding_name=name,
                )
                attributed.append(attributed_effect)
                self._emitter.emit(attributed_effect)

            context_effects[name] = attributed
            all_effects.extend(attributed)

            logger.debug(
                "Extracted %d effects from '%s'",
                len(effects),
                name,
            )

        return ctx.with_extracted_effects(
            all_effects=tuple(all_effects),
            per_context={k: tuple(v) for k, v in context_effects.items()},
        )


__all__ = ["ExtractPhase"]
