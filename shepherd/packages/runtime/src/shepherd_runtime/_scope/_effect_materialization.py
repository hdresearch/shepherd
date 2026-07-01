"""Effect-based materialization for Scope.

This module extracts effect-based materialization logic from scope.py.
It handles dispatching effects to registered materializers via MaterializerRegistry.

This is distinct from context-based materialization (in _materialization.py) which
uses ctx.materialization_intent() and get_materializer(). The two systems serve
different purposes:

- Effect-based (this module): Dispatch effects by type to materializers
- Context-based (_materialization.py): Commit pending changes from contexts

Usage:
    manager = EffectMaterializationManager(scope)
    summary = manager.materialize(registry)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from shepherd_runtime.effect_materialization import MaterializerRegistry

    from .substrate import EffectLayer, MaterializationSummary

logger = logging.getLogger(__name__)

__all__ = ["EffectMaterializationManager"]


class EffectMaterializationAccessor(Protocol):
    """Protocol for accessing scope state during effect materialization.

    This protocol breaks the circular dependency between EffectMaterializationManager
    and ScopeProxy. ScopeProxy implements this protocol.
    """

    @property
    def effect_materialization_is_root(self) -> bool:
        """Whether effect materialization is allowed from this scope."""
        ...

    @property
    def effect_materialization_is_discarded(self) -> bool:
        """Whether the scope has been discarded."""
        ...

    def effect_materialization_layers(self) -> list[EffectLayer]:
        """Return effect layers visible to effect-based materialization."""
        ...

    @property
    def effect_materialization_watermark(self) -> int:
        """Index of the first pending effect layer."""
        ...

    def advance_effect_materialization_watermark(self, up_to: int) -> None:
        """Record that layers through ``up_to`` have escaped containment."""
        ...

    def default_effect_materializer_registry(self) -> MaterializerRegistry:
        """Return the default registry used by ``scope.materialize()``."""
        ...


class EffectMaterializationManager:
    """Manages effect-based materialization for a scope.

    This class dispatches pending effects to registered materializers,
    handling rollback on failure. It tracks which effects have been
    materialized via the host's semantic watermark operations.

    Effect-based materialization differs from context-based materialization:
    - Effect-based: Each effect type has a registered materializer
    - Context-based: Each context type has a materializer that handles intents

    Usage:
        manager = EffectMaterializationManager(scope)
        summary = manager.materialize()  # Uses default registry
        summary = manager.materialize(custom_registry)  # Uses custom registry
    """

    def __init__(self, scope_accessor: EffectMaterializationAccessor) -> None:
        """Initialize with a scope accessor.

        Args:
            scope_accessor: Object implementing EffectMaterializationAccessor protocol
                (typically ScopeProxy)
        """
        self._scope = scope_accessor

    def materialize(
        self,
        registry: MaterializerRegistry | None = None,
    ) -> MaterializationSummary:
        """Apply pending effects to the real world (escape containment).

        Dispatches effects to registered materializers based on effect type.
        On failure, attempts to rollback previously materialized effects.

        Args:
            registry: MaterializerRegistry for dispatch. If None, uses
                the host's default effect materializer registry.

        Returns:
            MaterializationSummary with counts of processed/materialized effects

        Raises:
            RuntimeError: If called from non-root scope
            ContainmentError: If scope has been discarded
            MaterializationError: If materialization fails
        """
        from shepherd_core.errors import ContainmentError, MaterializationError

        from .substrate import MaterializationSummary

        # Invariant: root scope only
        if not self._scope.effect_materialization_is_root:
            raise RuntimeError("Can only materialize at root scope. Use scope.materialize() on the outermost scope.")

        # Invariant: not discarded
        if self._scope.effect_materialization_is_discarded:
            raise ContainmentError("Cannot materialize discarded scope")

        # Get registry with builtins if not provided
        if registry is None:
            registry = self._scope.default_effect_materializer_registry()

        # Get pending effects
        all_layers = self._scope.effect_materialization_layers()
        pending_layers = all_layers[self._scope.effect_materialization_watermark :]

        results: list[tuple[Any, Any]] = []  # (effect, MaterializationResult)
        completed: list[Any] = []  # For rollback tracking (reversible only)
        total_paths = 0
        rollback_errors: tuple[tuple[str, str], ...] = ()

        for layer in pending_layers:
            effect = layer.effect

            try:
                result = registry.materialize(effect)

                if not result.success:
                    # Expected failure - materializer returned success=False
                    logger.debug(
                        "Materialization failed for %s: %s, attempting rollback",
                        type(effect).__name__,
                        result.error,
                    )
                    try:
                        rollback_errors = self._rollback(completed, registry)
                        if rollback_errors:
                            logger.warning(
                                "Rollback encountered %d error(s)",
                                len(rollback_errors),
                            )
                    except Exception as rollback_exc:
                        # Rollback itself threw - capture both errors
                        logger.exception("Rollback itself failed: %s", rollback_exc)
                        raise MaterializationError(
                            f"Materialization failed for {type(effect).__name__} "
                            f"and rollback also failed: {rollback_exc}",
                            original_error=rollback_exc,
                        ) from rollback_exc

                    # Raise MaterializationError for expected failures
                    raise MaterializationError(
                        f"Materialization failed for {type(effect).__name__}: {result.error}",
                        rollback_errors=rollback_errors,
                    )

                results.append((effect, result))
                total_paths += len(result.paths_affected)

                # Track for potential rollback (only if reversible)
                if registry.can_reverse(effect):
                    completed.append(effect)

            except MaterializationError:
                # Already processed - re-raise
                raise
            except Exception as e:
                # Unexpected error from materializer or registry
                logger.debug(
                    "Unexpected error during materialization of %s: %s, attempting rollback",
                    type(effect).__name__,
                    e,
                )
                try:
                    rollback_errors = self._rollback(completed, registry)
                    if rollback_errors:
                        logger.warning(
                            "Rollback encountered %d error(s) after unexpected failure",
                            len(rollback_errors),
                        )
                except Exception as rollback_exc:
                    # Rollback itself threw - chain both errors
                    logger.exception("Rollback itself failed: %s", rollback_exc)
                    raise MaterializationError(
                        "Materialization failed and rollback also failed",
                        original_error=e,
                        rollback_errors=((type(effect).__name__, str(rollback_exc)),),
                    ) from e

                # Re-raise original with rollback info if there were partial failures
                if rollback_errors:
                    raise MaterializationError(
                        f"Materialization failed for {type(effect).__name__}: {e}",
                        original_error=e,
                        rollback_errors=rollback_errors,
                    ) from e
                # No rollback errors - just re-raise original
                raise

        # Update watermark
        self._scope.advance_effect_materialization_watermark(len(all_layers))

        return MaterializationSummary(
            effects_processed=len(pending_layers),
            effects_materialized=sum(1 for _, r in results if r.paths_affected),
            total_paths_affected=total_paths,
            rollback_errors=rollback_errors,  # Always empty on success path
        )

    def _rollback(
        self,
        effects: list[Any],
        registry: MaterializerRegistry,
    ) -> tuple[tuple[str, str], ...]:
        """Rollback completed materializations in LIFO order.

        Called when a materialization fails to undo previously completed
        materializations. Only effects where can_reverse() returned True
        are included in the list.

        Args:
            effects: List of effects to rollback (in completion order)
            registry: MaterializerRegistry containing the materializers

        Returns:
            Tuple of (effect_type_name, error_message) for any failed rollbacks.
            Empty tuple if all rollbacks succeeded.
        """
        if not effects:
            logger.debug("No effects to rollback")
            return ()

        logger.debug("Rolling back %d materialized effect(s) in LIFO order", len(effects))
        errors: list[tuple[str, str]] = []

        for effect in reversed(effects):
            effect_name = type(effect).__name__
            try:
                logger.debug("Attempting rollback of %s", effect_name)
                registry.reverse(effect)
                logger.debug("Successfully rolled back %s", effect_name)
            except Exception as e:
                error_msg = str(e)
                logger.exception(
                    "Rollback failed for %s: %s",
                    effect_name,
                    error_msg,
                )
                errors.append((effect_name, error_msg))

        if errors:
            logger.warning(
                "Rollback completed with %d error(s) out of %d effect(s)",
                len(errors),
                len(effects),
            )
        else:
            logger.debug("All %d effect(s) rolled back successfully", len(effects))

        return tuple(errors)
