"""Persistence and caching integration for Scope.

This module extracts persistence and cache management from scope.py, following
the composition pattern. It encapsulates:
- PersistenceManager initialization and lifecycle
- CacheStore initialization and access
- Configuration management

The ScopePersistence class is owned by ScopeProxy and provides lazy
initialization of persistence infrastructure.

Note: The resume() classmethod stays in ScopeProxy because it needs to
construct a new ScopeProxy instance. ScopePersistence handles the underlying
persistence state management.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from shepherd_core.context.kernel import ExecutionContext

    from shepherd_runtime.cache import CacheStore
    from shepherd_runtime.persistence import PersistenceConfig, PersistenceManager

    from .substrate import EffectLayer

logger = logging.getLogger(__name__)

__all__ = ["ScopePersistence"]


class ScopePersistence:
    """Handles persistence and caching integration for Scope.

    Manages lazy initialization of PersistenceManager and CacheStore.
    This class encapsulates the persistence state and provides methods
    for interacting with the persistence layer.

    Usage:
        # In ScopeProxy.__init__:
        self._persistence_manager = ScopePersistence(project_path)

        # Initialize if enabled:
        if persistence_enabled:
            self._persistence_manager.initialize()

        # Append layers during emit():
        self._persistence_manager.append_layer(layer)

        # Access cache:
        cache = self._persistence_manager.get_cache_store()
    """

    def __init__(self, project_path: Path | None) -> None:
        """Initialize persistence manager.

        Args:
            project_path: Path to the project directory. Required for persistence.
                If None, persistence is disabled.
        """
        self._project_path = project_path
        self._manager: PersistenceManager | None = None
        self._cache_store: CacheStore | None = None
        self._initialized = False

    @property
    def project_path(self) -> Path | None:
        """The project path for this persistence instance."""
        return self._project_path

    @property
    def manager(self) -> PersistenceManager | None:
        """The underlying persistence manager, or None if not initialized."""
        return self._manager

    @manager.setter
    def manager(self, value: PersistenceManager | None) -> None:
        """Set the persistence manager (used by resume())."""
        self._manager = value
        self._initialized = value is not None

    @property
    def is_initialized(self) -> bool:
        """Whether persistence has been initialized."""
        return self._initialized

    def initialize(self) -> None:
        """Initialize persistence manager if project_path is set.

        Creates project directory structure and starts a new stream.
        Safe to call multiple times (no-op if already initialized or disabled).
        """
        if self._initialized or self._project_path is None:
            return

        from shepherd_runtime.persistence import PersistenceConfig, PersistenceManager, ProjectId

        config = PersistenceConfig()
        project_id = ProjectId.from_path(self._project_path)
        self._manager = PersistenceManager(config.base_dir, project_id)
        self._manager.initialize()
        self._manager.start_stream()
        self._initialized = True
        logger.debug("Persistence initialized for %s", self._project_path)

    def append_layer(self, layer: EffectLayer) -> None:
        """Persist a layer to disk.

        No-op if persistence is not enabled or not initialized.

        Args:
            layer: The effect layer to persist
        """
        if self._manager is not None:
            self._manager.append_layer(layer)

    def close_stream(self) -> None:
        """Close the current persistence stream.

        Called during scope exit to finalize the stream.
        """
        if self._manager is not None:
            self._manager.close_stream()
            logger.debug("Persistence stream closed")

    def get_cache_store(self, parent_cache_getter: Any = None) -> CacheStore | None:
        """Get the cache store, initializing if needed.

        Args:
            parent_cache_getter: Optional callable to get parent scope's cache store.
                If provided and this is a child scope, delegates to parent.

        Returns:
            CacheStore if caching is enabled and initialized, None otherwise.
        """
        # Already initialized
        if self._cache_store is not None:
            return self._cache_store

        # Can't initialize without project_path
        if self._project_path is None:
            return None

        # Initialize cache store
        from shepherd_runtime.cache import CacheStore
        from shepherd_runtime.persistence import ProjectId

        config = self._get_cache_config()
        if not config.cache_enabled:
            return None

        project_id = ProjectId.from_path(self._project_path)
        cache_dir = config.base_dir / "projects" / project_id.hash / "cache"

        self._cache_store = CacheStore(cache_dir)
        self._cache_store.initialize()

        logger.debug("Cache store initialized at %s", cache_dir)
        return self._cache_store

    def get_cache_config(self) -> PersistenceConfig:
        """Get the cache configuration through the public persistence seam."""
        return self._get_cache_config()

    def _get_cache_config(self) -> PersistenceConfig:
        """Get the cache configuration.

        Returns default config if not configured.
        """
        from shepherd_runtime.persistence import PersistenceConfig

        # In the future, this could read from scope-level config
        # For now, return default config
        return PersistenceConfig()

    @property
    def cache_store(self) -> CacheStore | None:
        """Direct access to cache store without lazy initialization."""
        return self._cache_store


def apply_resumed_effects(
    resumed_layers: list[Any] | None,
    binding_name: str,
    context: ExecutionContext,
) -> ExecutionContext:
    """Apply matching effects from resumed stream to a context.

    Called during bind() when resuming from persistence. Effects are matched
    by binding_name (stable routing) OR context_id (semantic routing).

    Args:
        resumed_layers: List of EffectLayers from the resumed stream
        binding_name: Name the context is being bound under
        context: The initial context instance

    Returns:
        Context with all matching effects applied
    """
    if not resumed_layers:
        return context

    context_id = context.context_id
    applied_count = 0

    for layer in resumed_layers:
        effect = layer.effect

        # Get routing attributes from effect
        effect_binding_name = getattr(effect, "binding_name", None)
        effect_context_id = getattr(effect, "context_id", None)

        # Skip effects that don't target any binding
        if effect_binding_name is None and effect_context_id is None:
            continue

        # Match by binding_name (stable) OR context_id (semantic)
        if effect_binding_name == binding_name or effect_context_id == context_id:
            try:
                new_context = context.apply_effect(effect)
                if new_context is not context:
                    context = new_context
                    applied_count += 1
            except (TypeError, ValueError, AttributeError, KeyError) as e:
                # Expected effect application errors - log and continue
                # TypeError: effect type incompatible with context
                # ValueError: invalid effect data
                # AttributeError: effect missing expected attributes
                # KeyError: missing data in dict-like structures
                logger.warning(
                    "Failed to apply effect %s to binding '%s': %s",
                    type(effect).__name__,
                    binding_name,
                    e,
                )
            except Exception as e:
                # Unexpected error - log with full context and re-raise
                logger.exception(
                    "Unexpected error applying effect %s to binding '%s': %s",
                    type(effect).__name__,
                    binding_name,
                    e,
                )
                raise

    if applied_count > 0:
        logger.debug(
            "Applied %d resumed effects to binding '%s'",
            applied_count,
            binding_name,
        )

    return context
