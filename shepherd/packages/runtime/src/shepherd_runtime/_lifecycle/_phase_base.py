"""Phase protocol and base class for lifecycle phases.

This module defines the Phase protocol that all lifecycle phases implement,
and PhaseBase which provides default implementations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


@runtime_checkable
class Phase(Protocol):
    """Protocol for lifecycle phase handlers.

    Each phase:
    1. Receives PhaseContext from previous phase
    2. Performs its work (may have side effects on scope)
    3. Returns new PhaseContext with updated fields
    4. Optionally implements rollback for failure recovery

    Phases are composed into a pipeline by LifecyclePipeline.

    Async Design
    ------------
    All phases are async, even those that are logically pure (like
    ConfigurePhase). This uniformity:
    - Simplifies the pipeline implementation
    - Allows phases to be swapped without API changes
    - Has negligible overhead for pure phases

    Phase Dependencies
    ------------------
    Each phase documents which PhaseContext fields it reads and writes:

    | Phase     | Reads                              | Writes                            |
    |-----------|------------------------------------| ----------------------------------|
    | Configure | bindings, provider.capabilities, output_format  | composed_binding                  |
    | Prepare   | bindings, scope                    | prepared_contexts, sandboxes      |
    | Execute   | prompt, composed_binding, scope    | result                            |
    | Artifact  | artifact_markers, result           | artifact_outputs, artifact_effects|
    | Extract   | prepared_contexts, sandboxes, result| extracted_effects, context_effects|
    | Apply     | extracted_effects, scope           | context_outputs                   |
    | Cleanup   | prepared_contexts, sandboxes, error| (cleanup state tracking)          |
    """

    @property
    def name(self) -> str:
        """Phase name for logging and effect attribution.

        Convention: lowercase, e.g., "configure", "prepare", "execute"
        """
        ...

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        """Execute the phase.

        Args:
            ctx: Context from previous phase

        Returns:
            New context with this phase's outputs

        Raises:
            Any exception - pipeline will trigger rollback
        """
        ...

    async def rollback(self, ctx: PhaseContext, error: Exception) -> PhaseContext:
        """Rollback phase on pipeline failure.

        Called in reverse order when a later phase fails.
        Should undo any side effects from execute().

        Args:
            ctx: Context at time of failure (includes error field)
            error: The exception that caused the failure

        Returns:
            PhaseContext (possibly updated with cleanup state). Always returns
            a context for consistent pipeline handling - return ctx unchanged
            if no state changes occurred.

        Contract:
            - Must be idempotent (may be called multiple times)
            - Must not raise (log warnings instead)
            - Should track cleanup state via ctx.mark_cleaned_up() etc.
            - Default implementation returns ctx unchanged for pure phases
        """
        ...


class PhaseBase:
    """Base class providing default rollback implementation and error handling utilities.

    Subclasses inherit:
    - Default no-op rollback for pure phases
    - _handle_error_with_rollback() for phases that need mid-execution cleanup
    """

    async def rollback(self, ctx: PhaseContext, error: Exception) -> PhaseContext:
        """Default: no-op rollback for phases without side effects.

        Returns ctx unchanged since pure phases have nothing to roll back.
        """
        return ctx

    async def _handle_error_with_rollback(
        self,
        ctx: PhaseContext,
        error: Exception,
        wrap_error: Callable[[Exception], Exception] | None = None,
    ) -> None:
        """Handle an error by performing rollback and re-raising.

        This method provides a standardized pattern for phases that need to
        clean up partial state when an error occurs during execution. It:

        1. Calls self.rollback() with the provided context (which should contain
           partial state that needs cleanup)
        2. Optionally wraps the exception
        3. Re-raises the (possibly wrapped) exception

        This is particularly useful for phases like PreparePhase that may
        partially complete work before an error occurs and need to clean up
        before the pipeline's cross-phase rollback runs.

        Usage Pattern:
            The phase's execute() method should:
            1. Update ctx with partial state as work progresses
            2. Catch exceptions and call this method with the updated ctx

        Args:
            ctx: The phase context WITH partial state already set. This context
                 is passed to rollback() so it knows what to clean up.
            error: The exception that occurred
            wrap_error: Optional callable to wrap the exception before re-raising.
                       Receives the original exception and returns the new one.

        Raises:
            Always raises - either the original exception or the wrapped version.

        Example:
            async def execute(self, ctx: PhaseContext) -> PhaseContext:
                prepared = {}
                for binding in ctx.bindings:
                    try:
                        prepared[binding.name] = binding.context.prepare()
                        ctx = ctx.with_prepared(prepared, {})
                    except Exception as e:
                        # ctx already has partial state from previous iterations
                        await self._handle_error_with_rollback(
                            ctx,
                            e,
                            wrap_error=lambda err: PreparationError(str(err), cause=err),
                        )
                return ctx
        """
        logger.debug(
            "Phase '%s' handling error: %s - triggering rollback",
            getattr(self, "name", "unknown"),
            error,
        )

        # Rollback using the context with partial state
        await self.rollback(ctx, error)

        # Wrap error if requested, then raise
        if wrap_error is not None:
            raise wrap_error(error) from error
        raise error


__all__ = ["Phase", "PhaseBase"]
