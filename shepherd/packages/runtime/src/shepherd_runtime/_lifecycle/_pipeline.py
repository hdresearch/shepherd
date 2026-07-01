"""LifecyclePipeline: Stateful orchestrator for lifecycle phases.

This internal module defines the LifecyclePipeline that composes Phase handlers
into a unified execution flow with cross-phase rollback support.

Usage:
    pipeline = LifecyclePipeline(
        phases=[ConfigurePhase(), PreparePhase(registry), ...],
        emitter=emitter,
    )

    # Run phases incrementally
    ctx = await pipeline.run_until(initial_ctx, stop_after="prepare")
    ctx = await pipeline.run_until(ctx, stop_after="apply")

    # Or run all remaining
    ctx = await pipeline.run_remaining()

    # On external failure, rollback all
    await pipeline.rollback_all(error)
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._emitter import EffectEmitter
    from ._phase_context import PhaseContext
    from ._phases import Phase

logger = logging.getLogger(__name__)


@dataclass
class LifecyclePipeline:
    """Stateful pipeline that executes phases with timing and cross-phase rollback.

    The pipeline:
    1. Executes phases in order via run_until() or run_remaining()
    2. Tracks completed phases across multiple calls
    3. Emits phase start/complete/failed effects
    4. Records timing for each phase
    5. On failure, rolls back ALL completed phases (cross-phase rollback)

    Stateful Design
    ---------------
    Unlike a simple "run all phases" approach, this pipeline maintains state
    across multiple calls. This enables the facade pattern where:
    - __aenter__ runs configure + prepare
    - execute() runs execute + artifact + extract + apply
    - __aexit__ runs cleanup

    All completed phases are tracked, so if execute() fails, the rollback
    includes PreparePhase (which was completed in __aenter__).

    Double-Rollback Prevention
    --------------------------
    The pipeline prevents double-rollback through two mechanisms:

    1. **Pipeline-level**: `_rollback_completed` flag tracks whether rollback
       has already run. If `rollback_all()` is called after `run_until()`
       already performed rollback (due to exception), it's a no-op.

    2. **Phase-level**: `PhaseContext.cleaned_up_contexts` and
       `discarded_sandboxes` track which resources have been cleaned up.
       Even if rollback runs twice, phases skip already-cleaned resources.

    This belt-and-suspenders approach ensures robustness even if the facade
    or user code calls rollback in unexpected patterns.

    Cleanup Phase
    -------------
    Standard usage expects a CleanupPhase as the final phase. After a failure
    and rollback, the pipeline advances to the cleanup phase so that
    `run_until("cleanup")` can still execute it.

    Pipelines without a cleanup phase are supported but cleanup must be
    handled externally if needed.

    Usage:
        pipeline = LifecyclePipeline(
            phases=[
                ConfigurePhase(),
                PreparePhase(registry),
                ExecutePhase(),
                ArtifactPhase(emitter),
                ExtractPhase(emitter),
                ApplyPhase(emitter),
                CleanupPhase(emitter),
            ],
            emitter=emitter,
        )

        # Run phases incrementally
        ctx = await pipeline.run_until(initial_ctx, stop_after="prepare")
        # ... later ...
        ctx = await pipeline.run_until(ctx, stop_after="apply")

        # On external failure
        await pipeline.rollback_all(error)

        # Run cleanup
        ctx = await pipeline.run_until(ctx, stop_after="cleanup")
    """

    phases: list[Phase]
    emitter: EffectEmitter

    # State tracking (mutable - this is a stateful object)
    _completed_phases: list[Phase] = field(default_factory=list)
    _current_ctx: PhaseContext | None = field(default=None)
    _phase_index: int = field(default=0)
    _rollback_completed: bool = field(default=False)

    def _get_phase_index(self, phase_name: str) -> int | None:
        """Get the index of a phase by name, or None if not found."""
        for i, phase in enumerate(self.phases):
            if phase.name == phase_name:
                return i
        return None

    def _validate_phase_name(self, phase_name: str) -> None:
        """Validate that a phase name exists in the pipeline.

        Raises:
            ValueError: If phase_name is not found in the pipeline.
        """
        if self._get_phase_index(phase_name) is None:
            available = [p.name for p in self.phases]
            raise ValueError(f"Phase '{phase_name}' not found in pipeline. Available phases: {available}")

    async def run_until(
        self,
        ctx: PhaseContext,
        stop_after: str,
    ) -> PhaseContext:
        """Run phases until (and including) the named phase.

        Executes phases sequentially from the current position until the
        specified phase completes. Can be called multiple times to run
        phases incrementally.

        Args:
            ctx: Current phase context (or initial context on first call)
            stop_after: Phase name to stop after (e.g., "prepare", "apply")

        Returns:
            Updated context after running phases

        Raises:
            ValueError: If stop_after doesn't match any phase name
            Any exception from phases (after rollback completes)

        Example:
            ctx = await pipeline.run_until(ctx, stop_after="prepare")
            # Later...
            ctx = await pipeline.run_until(ctx, stop_after="apply")
        """
        self._validate_phase_name(stop_after)
        self._current_ctx = ctx

        try:
            while self._phase_index < len(self.phases):
                phase = self.phases[self._phase_index]

                # Execute phase with timing and effects
                self._current_ctx = await self._execute_phase(phase, self._current_ctx)

                # Track completion
                self._completed_phases.append(phase)
                self._phase_index += 1

                # Stop if we've reached the target phase
                if phase.name == stop_after:
                    break

            return self._current_ctx

        except Exception as e:
            # Set error in context for cleanup phase to see
            self._current_ctx = self._current_ctx.with_error(e)

            # Rollback all completed phases
            await self._rollback(self._completed_phases, self._current_ctx, e)

            # Advance to cleanup phase so __aexit__ can run it
            self._advance_to_cleanup()

            # Re-raise the original exception
            raise

    async def run_remaining(self) -> PhaseContext:
        """Run all remaining phases.

        Convenience method to execute all phases from the current position
        to the end of the pipeline.

        Returns:
            Final phase context

        Raises:
            RuntimeError: If no context set (must call run_until first)
        """
        if self._current_ctx is None:
            raise RuntimeError("No context available - call run_until() first to set initial context")

        if not self.phases:
            return self._current_ctx

        # Run until the last phase
        return await self.run_until(
            self._current_ctx,
            stop_after=self.phases[-1].name,
        )

    async def rollback_all(self, error: Exception) -> PhaseContext | None:
        """Rollback all completed phases (for external failure handling).

        Called by the facade when an error occurs outside the pipeline
        (e.g., in user code between pipeline calls).

        Safe to call multiple times - subsequent calls are no-ops if rollback
        already completed (either from run_until() or a previous rollback_all()).

        Args:
            error: The exception that caused the failure

        Returns:
            Updated context after rollback, or None if nothing to rollback
        """
        if self._current_ctx is None:
            return None

        if self._rollback_completed:
            logger.debug("Rollback already completed, skipping")
            return self._current_ctx

        self._current_ctx = self._current_ctx.with_error(error)
        await self._rollback(self._completed_phases, self._current_ctx, error)
        return self._current_ctx

    def update_context(self, ctx: PhaseContext) -> None:
        """Update the current context.

        Used by the facade to inject values (like prompt) before running
        subsequent phases.

        Args:
            ctx: The new context to use
        """
        self._current_ctx = ctx

    @property
    def current_context(self) -> PhaseContext | None:
        """Get the current phase context."""
        return self._current_ctx

    @property
    def completed_phase_names(self) -> list[str]:
        """Get names of completed phases (for debugging/logging)."""
        return [p.name for p in self._completed_phases]

    @property
    def is_rollback_completed(self) -> bool:
        """Whether rollback has already been performed."""
        return self._rollback_completed

    def _advance_to_cleanup(self) -> None:
        """Advance phase_index to cleanup phase after rollback.

        This ensures that after a failure and rollback, the facade can
        still call run_until("cleanup") to execute the cleanup phase.

        No-op if cleanup phase doesn't exist or is before current position.
        """
        cleanup_index = self._get_phase_index("cleanup")
        if cleanup_index is not None and cleanup_index > self._phase_index:
            logger.debug(
                "Advancing from phase index %d to cleanup at %d",
                self._phase_index,
                cleanup_index,
            )
            self._phase_index = cleanup_index

    async def _execute_phase(
        self,
        phase: Phase,
        ctx: PhaseContext,
    ) -> PhaseContext:
        """Execute a single phase with timing and effects.

        Emits LifecyclePhaseStarted before execution and either
        LifecyclePhaseCompleted or LifecyclePhaseFailed after.

        Args:
            phase: The phase to execute
            ctx: Current phase context

        Returns:
            Updated context with phase timing recorded

        Raises:
            Any exception from phase.execute()
        """
        # Emit phase started
        self.emitter.emit_phase_started(
            task_name=ctx.task_name,
            provider_id=ctx.effective_provider_id,
            phase=phase.name,
            context_count=len(ctx.bindings),
        )

        start = time.perf_counter()
        success = False

        try:
            result_ctx = await phase.execute(ctx)
            success = True

            # Record timing in context
            duration_ms = (time.perf_counter() - start) * 1000
            return result_ctx.with_phase_timing(phase.name, duration_ms)

        finally:
            duration_ms = (time.perf_counter() - start) * 1000

            if success:
                self.emitter.emit_phase_completed(
                    task_name=ctx.task_name,
                    provider_id=ctx.effective_provider_id,
                    phase=phase.name,
                    duration_ms=duration_ms,
                )
            else:
                # Get error info from exception context
                exc_info = sys.exc_info()
                error = exc_info[1] or Exception("Unknown error")
                error_message = str(error)[:500]  # Truncate for effect

                self.emitter.emit_phase_failed(
                    task_name=ctx.task_name,
                    provider_id=ctx.effective_provider_id,
                    phase=phase.name,
                    duration_ms=duration_ms,
                    error_type=type(error).__name__,
                    error_message=error_message,
                )

            logger.debug(
                "Phase '%s' %s in %.2fms",
                phase.name,
                "completed" if success else "failed",
                duration_ms,
            )

    async def _rollback(
        self,
        completed_phases: list[Phase],
        ctx: PhaseContext,
        error: Exception,
    ) -> None:
        """Rollback completed phases in reverse order.

        Updates ctx with cleanup state as rollback proceeds.
        Sets _rollback_completed flag when done to prevent double-rollback.

        Args:
            completed_phases: List of phases that completed successfully
            ctx: Current phase context (should have error set)
            error: The exception that caused the failure

        Note:
            Never raises - all rollback errors are logged as warnings.
        """
        if self._rollback_completed:
            logger.debug("Rollback already completed, skipping")
            return

        # Determine which phase failed (for logging)
        failed_phase_name = self.phases[self._phase_index].name if self._phase_index < len(self.phases) else "unknown"

        logger.warning(
            "Pipeline failed at phase '%s': %s - rolling back %d completed phases",
            failed_phase_name,
            error,
            len(completed_phases),
        )

        # Rollback in reverse order
        for phase in reversed(completed_phases):
            try:
                logger.debug("Rolling back phase '%s'", phase.name)
                # Rollback returns updated ctx with cleanup state tracked
                ctx = await phase.rollback(ctx, error)
                self._current_ctx = ctx
            except Exception as e:  # noqa: BLE001
                # Never raise from rollback - log and continue
                logger.warning(
                    "Rollback failed for phase '%s': %s",
                    phase.name,
                    e,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )

        self._rollback_completed = True


__all__ = ["LifecyclePipeline"]
