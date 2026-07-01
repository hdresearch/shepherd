"""ConfigurePhase: Compose bindings from contexts (pure).

Phase 1 of the lifecycle pipeline. This phase configures all contexts and
composes their bindings into a single ProviderBinding.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from shepherd_core.errors import SessionCWDMismatchError
from shepherd_core.types import ProviderBinding

from ._phase_base import PhaseBase

if TYPE_CHECKING:
    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class ConfigurePhase(PhaseBase):
    """Phase 1: Configure all contexts and compose bindings.

    Reads: bindings, provider.capabilities, output_format
    Writes: composed_binding

    This phase is pure (no side effects), so rollback is a no-op.
    """

    @property
    def name(self) -> str:
        return "configure"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        binding_list: list[ProviderBinding] = []

        capabilities = ctx.provider.capabilities if ctx.provider else None
        for binding in ctx.bindings:
            pb = binding.context.configure(capabilities)
            binding_list.append(pb)
            logger.debug(
                "Configured context '%s': %s capabilities",
                binding.name,
                len(pb.capabilities) if pb.capabilities else 0,
            )

        # Compose into single binding
        composed = ProviderBinding.compose(*binding_list)

        # Add output format if provided
        if ctx.output_format and composed:
            composed = composed.model_copy(update={"output_format": ctx.output_format})

        # Validate session CWD compatibility (before any side effects)
        # This catches the case where a task resumes a session but has a different
        # CWD than where the session was created (which causes CLI fork_session to fail)
        if composed and composed.session_id:
            self._validate_session_cwd(ctx, composed)

        # Validate against provider (before any side effects)
        if composed and ctx.provider is not None:
            ctx.provider.validate_binding(composed)

        return ctx.with_composed_binding(composed)

    def _validate_session_cwd(self, ctx: PhaseContext, composed: ProviderBinding) -> None:
        """Validate CWD compatibility for session resumption.

        When resuming a session (session_id is set), the CLI requires the same CWD
        as when the session was created. This method checks for mismatches and
        raises SessionCWDMismatchError with a helpful message.

        Uses duck-typing to check for SessionState-like contexts (those with
        host_cwd and session_id attributes) to avoid circular imports.
        """
        # Find any context that looks like SessionState (duck typing)
        # SessionState has: session_id, host_cwd attributes
        for binding in ctx.bindings:
            context = binding.context
            # Duck-type check for SessionState-like context
            session_id = getattr(context, "session_id", None)
            host_cwd = getattr(context, "host_cwd", None)

            if session_id and host_cwd:
                # This context has session info - check CWD compatibility
                binding_cwd = composed.cwd or str(Path.cwd())

                if binding_cwd != host_cwd:
                    raise SessionCWDMismatchError(
                        session_cwd=host_cwd,
                        binding_cwd=binding_cwd,
                    )


__all__ = ["ConfigurePhase"]
