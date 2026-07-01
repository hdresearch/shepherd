"""Materialization workflow management for Scope.

This module extracts materialization logic from scope.py, providing:
- Helper functions for ordering bindings by reversibility
- Rollback management for failed materializations
- Core commit logic that can be used by ScopeProxy

The MaterializationManager class encapsulates the commit workflow while
keeping minimal coupling to the Scope implementation.
"""

from __future__ import annotations

import logging
import time as time_module
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from shepherd_core.effects import Effect

    from .substrate import ContextBinding, EffectLayer

logger = logging.getLogger(__name__)

__all__ = [
    "MaterializationManager",
    "order_bindings_by_reversibility",
    "rollback_completed_materializations",
]


# NOTE: MaterializationResult is defined in materialization.py (public API).
# This module uses that class via the Materializer protocol.
# The workflow tracks results using dict[str, Any] for flexibility.


class ScopeAccessor(Protocol):
    """Protocol for accessing scope state during materialization.

    This protocol breaks the circular dependency between MaterializationManager
    and ScopeProxy. ScopeProxy implements this protocol.
    """

    @property
    def materialization_parent(self) -> Any:
        """Parent scope proxy, or None if root."""
        ...

    def materialization_snapshot(self) -> Any:
        """Return the immutable scope state."""
        ...

    def replace_materialization_snapshot(self, scope: Any) -> None:
        """Replace the immutable scope state."""
        ...

    def emit(self, effect: Effect) -> None:
        """Emit an effect to the stream."""
        ...

    def ordered_materialization_bindings(self) -> list[ContextBinding]:
        """Get bindings ordered by reversibility level."""
        ...

    def mark_escaped(self) -> None:
        """Record that effects have escaped containment via commit()."""
        ...


def order_bindings_by_reversibility(
    bindings: tuple[ContextBinding, ...],
) -> list[ContextBinding]:
    """Order bindings by reversibility level for safe materialization.

    Returns contexts ordered: AUTO first, then COMPENSABLE, then NONE.
    Only includes Materializable contexts with pending changes.

    This ordering ensures that if a non-reversible context fails,
    we can rollback the reversible contexts that succeeded.

    Args:
        bindings: Tuple of context bindings to order

    Returns:
        List of bindings ordered by reversibility, filtered to only
        materializable contexts with pending changes.
    """
    from shepherd_core.types import ReversibilityLevel

    from shepherd_runtime.materialization import is_materializable

    order_map = {
        ReversibilityLevel.AUTO: 0,
        ReversibilityLevel.COMPENSABLE: 1,
        ReversibilityLevel.NONE: 2,
    }

    materializable: list[tuple[ContextBinding, ReversibilityLevel]] = []
    for binding in bindings:
        ctx = binding.context
        if is_materializable(ctx) and ctx.has_pending_changes:  # type: ignore[attr-defined]
            materializable.append((binding, ctx.reversibility))

    materializable.sort(key=lambda x: order_map.get(x[1], 2))
    return [binding for binding, _ in materializable]


def rollback_completed_materializations(
    completed: list[tuple[ContextBinding, Any, Any]],
) -> None:
    """Rollback completed materializations in reverse order.

    Called when a later materialization fails. Rollback failures
    are logged but do not raise (we're already in error recovery).

    Args:
        completed: List of (binding, intent, result) tuples to rollback
    """
    from shepherd_runtime.materialization import get_materializer

    for binding, intent, result in reversed(completed):
        materializer = get_materializer(type(binding.context).__name__)
        if materializer is None or not materializer.can_rollback():
            logger.warning("Cannot rollback %s", binding.name)
            continue
        try:
            materializer.rollback(intent, result)
            logger.info("Rolled back %s", binding.name)
        except Exception as e:
            logger.exception("Rollback failed for %s: %s", binding.name, e)


def get_already_materialized(layers: list[EffectLayer]) -> set[str]:
    """Get set of binding names that were already successfully materialized.

    Scans the effect stream for ContextMaterialized effects.

    Args:
        layers: List of effect layers to scan

    Returns:
        Set of binding names that have successful ContextMaterialized effects
    """
    from shepherd_core.effects import ContextMaterialized

    already_materialized: set[str] = set()
    for layer in layers:
        if isinstance(layer.effect, ContextMaterialized) and layer.effect.success:
            already_materialized.add(layer.effect.binding_name)
    return already_materialized


def materialize_binding(
    binding: ContextBinding,
    message: str | None,
    emit: Callable[[Effect], None],
) -> tuple[dict[str, Any], Any, Any]:
    """Materialize a single binding.

    Args:
        binding: The binding to materialize
        message: Optional commit message
        emit: Callback to emit effects

    Returns:
        Tuple of (result_dict, intent, materializer_result)

    Raises:
        RuntimeError: If no materializer is registered or materialization fails
    """
    from shepherd_core.effects import ContextMaterialized

    from shepherd_runtime.materialization import get_materializer, run_materialization_admission_hooks

    ctx = binding.context
    start_time = time_module.time()

    # Get intent
    intent = ctx.materialization_intent()  # type: ignore[attr-defined]
    if message and hasattr(intent, "with_commit_message"):
        intent = intent.with_commit_message(message)
    changes_applied = _intent_change_count(intent)

    try:
        run_materialization_admission_hooks(intent)
    except Exception as exc:
        duration_ms = (time_module.time() - start_time) * 1000
        emit(
            ContextMaterialized(
                binding_name=binding.name,
                context_type=type(ctx).__name__,
                changes_applied=changes_applied,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )
        )
        raise RuntimeError(f"Materialization admission failed for {binding.name}: {exc}") from exc

    # Get materializer
    materializer = get_materializer(type(ctx).__name__)
    if materializer is None:
        # Emit failure effect
        duration_ms = (time_module.time() - start_time) * 1000
        emit(
            ContextMaterialized(
                binding_name=binding.name,
                context_type=type(ctx).__name__,
                success=False,
                error=f"No materializer registered for {type(ctx).__name__}",
                duration_ms=duration_ms,
            )
        )
        raise RuntimeError(
            f"No materializer registered for {type(ctx).__name__}. "
            f"Call register_materializer('{type(ctx).__name__}', materializer) "
            f"in your module's __init__.py."
        )

    # Execute materialization (I/O happens here)
    result = materializer.materialize(intent)
    duration_ms = (time_module.time() - start_time) * 1000

    if not result.success:
        # Emit failure effect
        emit(
            ContextMaterialized(
                binding_name=binding.name,
                context_type=type(ctx).__name__,
                changes_applied=changes_applied,
                paths_affected=result.paths_affected,
                success=False,
                error=result.error,
                duration_ms=duration_ms,
            )
        )
        raise RuntimeError(f"Materialization failed for {binding.name}: {result.error}")

    # Emit success effect
    committed = result.metadata.get("committed") == "true"
    emit(
        ContextMaterialized(
            binding_name=binding.name,
            context_type=type(ctx).__name__,
            changes_applied=changes_applied,
            paths_affected=result.paths_affected,
            success=True,
            committed=committed,
            duration_ms=duration_ms,
        )
    )

    result_dict = {
        "name": binding.name,
        "context_id": ctx.context_id,
        "paths_affected": list(result.paths_affected),
        "metadata": result.metadata,
    }

    return result_dict, intent, result


def _intent_change_count(intent: Any) -> int:
    if hasattr(intent, "patches"):
        return len(intent.patches) if intent.patches else 0
    if hasattr(intent, "changesets"):
        return len(intent.changesets) if intent.changesets else 0
    return 0


class MaterializationManager:
    """Manages the materialization workflow for a scope.

    This class provides a high-level interface for committing pending
    changes across all bound contexts. It handles:
    - Ordering contexts by reversibility
    - Executing materializations
    - Rolling back on failure
    - Emitting appropriate effects

    Usage:
        manager = MaterializationManager(scope)
        result = manager.commit(message="Fix bug")
    """

    def __init__(self, scope_accessor: ScopeAccessor) -> None:
        """Initialize with a scope accessor.

        Args:
            scope_accessor: Object implementing ScopeAccessor protocol
                (typically ScopeProxy)
        """
        self._scope = scope_accessor

    def commit(self, message: str | None = None) -> dict[str, Any]:
        """Materialize all contexts with pending changes.

        Args:
            message: Optional commit message

        Returns:
            Dict with summary of what was materialized

        Raises:
            RuntimeError: If not at root scope or materialization fails
        """
        if self._scope.materialization_parent is not None:
            raise RuntimeError("Can only commit at root scope. Use scope.commit() on the outermost scope.")

        results: list[dict[str, Any]] = []
        total_paths = 0
        completed: list[tuple[ContextBinding, Any, Any]] = []

        for binding in self._scope.ordered_materialization_bindings():
            try:
                result_dict, intent, result = materialize_binding(binding, message, self._scope.emit)

                # Track for potential rollback
                completed.append((binding, intent, result))

                # Update context state (pure)
                new_ctx = binding.context.with_materialized(result)  # type: ignore[attr-defined]
                snapshot = self._scope.materialization_snapshot()
                self._scope.replace_materialization_snapshot(snapshot.with_updated_context(binding.name, new_ctx))

                results.append(result_dict)
                total_paths += len(result.paths_affected)

            except RuntimeError:
                rollback_completed_materializations(completed)
                raise

        if results:
            self._scope.mark_escaped()

        return {
            "contexts": results,
            "total_paths_affected": total_paths,
        }

    def commit_remaining(
        self,
        message: str | None = None,
    ) -> dict[str, Any]:
        """Continue an interrupted commit, skipping already-materialized contexts.

        Args:
            message: Optional commit message

        Returns:
            Dict with summary including skipped contexts

        Raises:
            RuntimeError: If not at root scope or materialization fails
        """
        if self._scope.materialization_parent is not None:
            raise RuntimeError("Can only commit at root scope. Use scope.commit_remaining() on the outermost scope.")

        # Find contexts that were already successfully materialized
        already_materialized = get_already_materialized(list(self._scope.materialization_snapshot()._stream._layers))

        results: list[dict[str, Any]] = []
        total_paths = 0
        completed: list[tuple[ContextBinding, Any, Any]] = []
        skipped: list[str] = []

        for binding in self._scope.ordered_materialization_bindings():
            # Skip already-materialized contexts
            if binding.name in already_materialized:
                logger.info(
                    "Skipping %s: already materialized in previous session",
                    binding.name,
                )
                skipped.append(binding.name)
                continue

            try:
                result_dict, intent, result = materialize_binding(binding, message, self._scope.emit)

                # Track for potential rollback
                completed.append((binding, intent, result))

                # Update context state (pure)
                new_ctx = binding.context.with_materialized(result)  # type: ignore[attr-defined]
                snapshot = self._scope.materialization_snapshot()
                self._scope.replace_materialization_snapshot(snapshot.with_updated_context(binding.name, new_ctx))

                results.append(result_dict)
                total_paths += len(result.paths_affected)

            except RuntimeError:
                rollback_completed_materializations(completed)
                raise

        if results:
            self._scope.mark_escaped()

        return {
            "contexts": results,
            "total_paths_affected": total_paths,
            "skipped": skipped,
        }

    def preview(self) -> dict[str, Any]:
        """Preview what commit() would do without executing.

        Returns:
            Dict mapping binding name to intent info
        """
        from shepherd_runtime.materialization import is_materializable

        result: dict[str, Any] = {}

        for binding in self._scope.materialization_snapshot()._bindings:
            ctx = binding.context

            if not is_materializable(ctx):
                continue

            if not ctx.has_pending_changes:
                continue

            intent = ctx.materialization_intent()

            result[binding.name] = {
                "context_type": type(ctx).__name__,
                "context_id": ctx.context_id,
                "intent": intent,
                "has_pending_changes": True,
            }

        return result
