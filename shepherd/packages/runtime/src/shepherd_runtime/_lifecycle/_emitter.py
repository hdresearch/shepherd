"""EffectEmitter: Encapsulates effect emission with scope/formatter routing.

This internal module extracts the effect emission logic from ExecutionLifecycle
to improve testability and add configurable exception handling around formatter calls.

Usage:
    emitter = EffectEmitter(scope, formatter)
    emitter.emit(TaskStarted(...))
    emitter.finalize()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from shepherd_core.effects import (
    ContextCaptured,
    ContextCleanedUp,
    ContextPrepared,
    Effect,
    LifecyclePhaseCompleted,
    LifecyclePhaseFailed,
    LifecyclePhaseStarted,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
)

if TYPE_CHECKING:
    from shepherd_runtime.scope import Scope

logger = logging.getLogger(__name__)


class EffectEmitter:
    """Routes effects to scope AND formatter with configurable strictness.

    This class encapsulates the dual-routing of effects:
    1. Always emit to scope (for stream recording and effect routing)
    2. Optionally route to formatter (for verbose output)

    By default, formatter exceptions are re-raised to surface formatter bugs.
    Callers can disable strictness to treat formatter failures as non-fatal.

    Attributes:
        _scope: The scope to emit effects to
        _formatter: Optional formatter for verbose output
    """

    def __init__(
        self,
        scope: Scope,
        formatter: Any | None = None,
        *,
        strict_formatter: bool = True,
    ) -> None:
        """Initialize the emitter.

        Args:
            scope: The scope to emit effects to (required)
            formatter: Optional formatter with on_effect/finalize methods
            strict_formatter: If True, re-raise formatter exceptions
        """
        self._scope = scope
        self._formatter = formatter
        self._strict_formatter = strict_formatter

    @property
    def scope(self) -> Scope:
        """The scope effects are emitted to."""
        return self._scope

    @property
    def formatter(self) -> Any | None:
        """The formatter for verbose output (if any)."""
        return self._formatter

    @property
    def strict_formatter(self) -> bool:
        """Whether formatter exceptions are re-raised."""
        return self._strict_formatter

    def emit(self, effect: Effect) -> None:
        """Emit an effect to scope and optionally to formatter.

        Always emits to scope. If a formatter is configured, routes the
        effect to formatter.on_effect() with exception handling.

        Args:
            effect: The effect to emit
        """
        # Always emit to scope
        self._scope.emit(effect)

        # Route to formatter with exception handling
        if self._formatter is not None:
            try:
                self._formatter.on_effect(effect)
            except Exception as e:
                if self._strict_formatter:
                    logger.exception(
                        "Formatter error on %s: %s",
                        type(effect).__name__,
                        e,
                    )
                    raise
                logger.warning(
                    "Formatter error on %s: %s",
                    type(effect).__name__,
                    e,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )

    def finalize(self) -> None:
        """Finalize the formatter (if any).

        Called at the end of the execution lifecycle to allow the formatter
        to perform any cleanup or final output.

        Exception handling prevents formatter.finalize() errors from
        disrupting lifecycle cleanup.
        """
        if self._formatter is not None:
            try:
                self._formatter.finalize()
            except Exception as e:
                if self._strict_formatter:
                    logger.exception("Formatter error on finalize: %s", e)
                    raise
                logger.warning(
                    "Formatter error on finalize: %s",
                    e,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )

    # --- Convenience Methods for Common Effects ---

    def emit_phase_started(
        self,
        task_name: str | None,
        provider_id: str | None,
        phase: str,
        context_count: int,
    ) -> None:
        """Emit a LifecyclePhaseStarted effect."""
        self.emit(
            LifecyclePhaseStarted(
                task_name=task_name,
                provider_id=provider_id,
                phase=phase,
                context_count=context_count,
            )
        )

    def emit_phase_completed(
        self,
        task_name: str | None,
        provider_id: str | None,
        phase: str,
        duration_ms: float,
    ) -> None:
        """Emit a LifecyclePhaseCompleted effect."""
        self.emit(
            LifecyclePhaseCompleted(
                task_name=task_name,
                provider_id=provider_id,
                phase=phase,
                duration_ms=duration_ms,
            )
        )

    def emit_phase_failed(
        self,
        task_name: str | None,
        provider_id: str | None,
        phase: str,
        duration_ms: float,
        error_type: str,
        error_message: str,
    ) -> None:
        """Emit a LifecyclePhaseFailed effect."""
        self.emit(
            LifecyclePhaseFailed(
                task_name=task_name,
                provider_id=provider_id,
                phase=phase,
                duration_ms=duration_ms,
                error_type=error_type,
                error_message=error_message,
            )
        )

    def emit_task_started(
        self,
        task_name: str | None,
        provider_id: str | None,
        inputs: dict[str, Any],
        device_name: str | None = None,
        stage_name: str | None = None,
    ) -> None:
        """Emit a TaskStarted effect."""
        self.emit(
            TaskStarted(
                task_name=task_name,
                provider_id=provider_id,
                inputs=inputs,
                device_name=device_name,
                stage_name=stage_name,
            )
        )

    def emit_task_completed(
        self,
        task_name: str | None,
        provider_id: str | None,
        outputs: dict[str, Any],
        duration_ms: float,
        device_name: str | None = None,
        stage_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a TaskCompleted effect."""
        self.emit(
            TaskCompleted(
                task_name=task_name,
                provider_id=provider_id,
                outputs=outputs,
                duration_ms=duration_ms,
                device_name=device_name,
                stage_name=stage_name,
                metadata=metadata or {},
            )
        )

    def emit_task_failed(
        self,
        task_name: str | None,
        provider_id: str | None,
        error: str,
        error_type: str,
        device_name: str | None = None,
        stage_name: str | None = None,
    ) -> None:
        """Emit a TaskFailed effect."""
        self.emit(
            TaskFailed(
                task_name=task_name,
                provider_id=provider_id,
                error=error,
                error_type=error_type,
                device_name=device_name,
                stage_name=stage_name,
            )
        )

    def emit_context_prepared(
        self,
        context_id: str,
        binding_name: str,
        task_name: str | None,
        provider_id: str | None,
    ) -> None:
        """Emit a ContextPrepared effect."""
        self.emit(
            ContextPrepared(
                context_id=context_id,
                binding_name=binding_name,
                task_name=task_name,
                provider_id=provider_id,
            )
        )

    def emit_context_captured(
        self,
        context_id: str,
        binding_name: str,
        old_context_id: str,
        new_context_id: str,
        effect_count: int,
        task_name: str | None,
        provider_id: str | None,
    ) -> None:
        """Emit a ContextCaptured effect."""
        self.emit(
            ContextCaptured(
                context_id=context_id,
                binding_name=binding_name,
                old_context_id=old_context_id,
                new_context_id=new_context_id,
                effect_count=effect_count,
                task_name=task_name,
                provider_id=provider_id,
            )
        )

    def emit_context_cleaned_up(
        self,
        context_id: str,
        binding_name: str,
        had_error: bool,
        task_name: str | None,
        provider_id: str | None,
    ) -> None:
        """Emit a ContextCleanedUp effect."""
        self.emit(
            ContextCleanedUp(
                context_id=context_id,
                binding_name=binding_name,
                had_error=had_error,
                task_name=task_name,
                provider_id=provider_id,
            )
        )


__all__ = ["EffectEmitter"]
