"""Checkpoint management for Scope.

This module extracts checkpoint/restore logic from scope.py, providing:
- CheckpointManager: Creates and restores checkpoints
- Helper functions for validation, truncation, and effect replay

The CheckpointManager class encapsulates the checkpoint workflow while
keeping minimal coupling to the Scope implementation via the ScopeAccessor
protocol.

Design: Checkpoints use stream truncation rather than fork redirection.
When restored, the stream is truncated to the checkpoint position and
context state is recomputed by replaying effects from initial state.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from shepherd_runtime.checkpoint import Checkpoint

    from .substrate import ContextBinding, ImmutableScope

logger = logging.getLogger(__name__)

__all__ = ["CheckpointManager"]


class ScopeAccessor(Protocol):
    """Protocol for accessing scope state during checkpoint operations.

    This protocol breaks the circular dependency between CheckpointManager
    and ScopeProxy. ScopeProxy implements this protocol.
    """

    def checkpoint_snapshot(self) -> ImmutableScope:
        """Return the current immutable scope state."""
        ...

    def replace_checkpoint_snapshot(self, scope: ImmutableScope) -> None:
        """Replace the current immutable scope state."""
        ...

    @property
    def checkpoint_materialized_index(self) -> int:
        """Index up to which effects have been materialized."""
        ...


class CheckpointManager:
    """Manages checkpoint creation and restoration for a scope.

    This class provides a high-level interface for creating named
    savepoints and restoring scope state. It handles:
    - Recording stream position and binding count
    - Validating checkpoints before restore
    - Truncating the stream
    - Rebuilding binding state via effect replay

    Usage:
        manager = CheckpointManager(scope)
        cp = manager.create("before_risky_operation")
        # ... do work ...
        manager.restore(cp)  # Roll back
    """

    def __init__(self, scope_accessor: ScopeAccessor) -> None:
        """Initialize with a scope accessor.

        Args:
            scope_accessor: Object implementing ScopeAccessor protocol
                (typically ScopeProxy)
        """
        self._accessor = scope_accessor

    def create(self, name: str) -> Checkpoint:
        """Create a named checkpoint for potential rollback.

        Records the current stream position and binding count. Calling
        restore(checkpoint) will truncate the stream back to this position,
        remove any bindings added after the checkpoint, and recompute
        context state by replaying the remaining effects.

        Args:
            name: Human-readable name for debugging

        Returns:
            Checkpoint that can be passed to restore()
        """
        from shepherd_runtime.checkpoint import Checkpoint

        scope = self._accessor.checkpoint_snapshot()
        position = len(scope._stream)
        binding_count = len(scope._bindings)

        # Create checkpoint with fingerprint for validation
        cp = Checkpoint(
            name=name,
            _scope=self._accessor,  # type: ignore[arg-type]
            _position=position,
            _binding_count=binding_count,
        )
        # Set fingerprint after creation (dataclass frozen issue workaround)
        object.__setattr__(cp, "_fingerprint", cp._compute_fingerprint())

        return cp

    def restore(
        self,
        checkpoint: Checkpoint,
        *,
        keep_bindings: list[str] | None = None,
        exclude_effect_types: list[str] | None = None,
        strict: bool = False,
    ) -> None:
        """Restore to checkpoint, optionally preserving specific bindings or effects.

        Truncates the stream back to the checkpoint position, removes any
        bindings added after the checkpoint (except those in keep_bindings),
        and recomputes context states by replaying effects (except those
        matching exclude_effect_types).

        Args:
            checkpoint: A checkpoint created by this scope
            keep_bindings: List of binding names to preserve even if they were
                added after the checkpoint. Their current state is kept intact.
            exclude_effect_types: List of effect type names to skip during replay.
                Effects of these types won't be applied when recomputing state.
                Use effect.effect_type values (e.g., "tool_call_started").
            strict: If True, fail on any validation inconsistency (including
                fingerprint mismatch, binding count decrease). If False (default),
                only critical issues cause failure and warnings are logged.

        Raises:
            ValueError: If checkpoint belongs to different scope
            ValueError: If checkpoint was already restored
            ValueError: If checkpoint is stale (invalidated by previous restore)
            ValueError: If keep_bindings contains a binding that doesn't exist
            CheckpointValidationError: If strict=True and validation fails
            ContainmentError: If effects after checkpoint were materialized
        """
        from shepherd_core.errors import ContainmentError

        from shepherd_runtime.checkpoint import CheckpointValidationError

        from .substrate import ContextBinding

        # Basic validation - scope ownership
        if checkpoint._scope is not self._accessor:  # type: ignore[comparison-overlap]
            raise ValueError(f"Checkpoint '{checkpoint.name}' belongs to different scope")

        # Run comprehensive validation
        is_valid, issues = checkpoint.validate(strict=strict)

        if not is_valid:
            # In strict mode, issues become CheckpointValidationError
            # In non-strict mode, only critical failures reach here
            if strict:
                raise CheckpointValidationError(
                    checkpoint.name,
                    "validation failed",
                    "; ".join(issues),
                )
            # Handle specific critical failures with appropriate error types
            if checkpoint._restored:
                raise ValueError(f"Checkpoint '{checkpoint.name}' already restored")
            if checkpoint.is_stale:
                raise ValueError(
                    f"Checkpoint '{checkpoint.name}' is stale: position {checkpoint._position} "
                    f"exceeds current stream length {len(self._accessor.checkpoint_snapshot()._stream)}. "
                    f"This checkpoint was invalidated by a previous restore."
                )
            # Generic validation failure
            raise CheckpointValidationError(
                checkpoint.name,
                "validation failed",
                "; ".join(issues),
            )

        # Log warnings if any (non-strict mode)
        if issues:
            for issue in issues:
                logger.warning(
                    "Checkpoint '%s' validation warning: %s",
                    checkpoint.name,
                    issue,
                )

        # Check materialization barrier using watermark
        if checkpoint._position < self._accessor.checkpoint_materialized_index:
            raise ContainmentError(
                f"Cannot restore checkpoint '{checkpoint.name}' - "
                f"effects after position {checkpoint._position} have been materialized "
                f"(materialized through index {self._accessor.checkpoint_materialized_index - 1})"
            )

        # Normalize filter parameters
        keep_bindings_set = set(keep_bindings) if keep_bindings else set()
        exclude_effect_types_set = set(exclude_effect_types) if exclude_effect_types else set()

        # Validate keep_bindings - all must exist in current scope
        scope = self._accessor.checkpoint_snapshot()
        if keep_bindings_set:
            current_binding_names = {b.name for b in scope._bindings}
            unknown_bindings = keep_bindings_set - current_binding_names
            if unknown_bindings:
                raise ValueError(
                    f"keep_bindings contains unknown binding(s): {sorted(unknown_bindings)}. "
                    f"Available bindings: {sorted(current_binding_names)}"
                )

        logger.debug(
            "Restoring checkpoint '%s': truncating stream from %d to %d, "
            "resetting bindings from %d to %d (keeping: %s, excluding effects: %s)",
            checkpoint.name,
            len(scope._stream),
            checkpoint._position,
            len(scope._bindings),
            checkpoint._binding_count,
            sorted(keep_bindings_set) if keep_bindings_set else "none",
            sorted(exclude_effect_types_set) if exclude_effect_types_set else "none",
        )

        # Step 1: Truncate stream
        truncated_stream = scope._stream.truncate_to(checkpoint._position)

        # Step 2: Build new bindings list
        # - Bindings from checkpoint time are reset to initial state
        # - Bindings in keep_bindings are preserved with current state
        checkpoint_bindings = scope._bindings[: checkpoint._binding_count]
        new_after_bindings = scope._bindings[checkpoint._binding_count :]

        # Build result: reset checkpoint bindings (unless kept), plus kept new bindings
        result_bindings: list[ContextBinding] = []

        for b in checkpoint_bindings:
            if b.name in keep_bindings_set:
                # Preserve current state for kept bindings
                result_bindings.append(b)
            else:
                # Reset to initial state
                result_bindings.append(
                    ContextBinding(
                        name=b.name,
                        context=b.initial_context,  # type: ignore[arg-type]
                        initial_context=b.initial_context,
                    )
                )

        # Add bindings created after checkpoint that should be kept
        for b in new_after_bindings:
            if b.name in keep_bindings_set:
                result_bindings.append(b)

        # Step 3: Update scope with truncated stream and new bindings
        # Note: replace() triggers __post_init__ which rebuilds indices
        self._accessor.replace_checkpoint_snapshot(
            replace(
                scope,
                _bindings=tuple(result_bindings),
                _stream=truncated_stream,
            )
        )

        # Step 4: Replay effects to re-derive binding state (with filtering)
        # This uses ImmutableScope.apply_effect() which routes effects to bindings
        # by binding_name (set by lifecycle) and returns a new scope with updated
        # binding contexts.
        #
        # Note: If apply_effect() raises, we crash intentionally. apply_effect() is
        # pure and should never fail - an exception indicates a bug in the context.
        for layer in truncated_stream:
            effect = layer.effect

            # Skip excluded effect types during replay
            if effect.effect_type in exclude_effect_types_set:
                logger.debug(
                    "Skipping excluded effect type '%s' during replay",
                    effect.effect_type,
                )
                continue

            # Skip effects targeting kept bindings (they already have current state)
            effect_binding = getattr(effect, "binding_name", None)
            if effect_binding and effect_binding in keep_bindings_set:
                logger.debug(
                    "Skipping effect for kept binding '%s' during replay",
                    effect_binding,
                )
                continue

            self._accessor.replace_checkpoint_snapshot(self._accessor.checkpoint_snapshot().apply_effect(effect))

        logger.debug(
            "Checkpoint '%s' restored successfully",
            checkpoint.name,
        )

        # Mark checkpoint as restored
        checkpoint._restored = True
