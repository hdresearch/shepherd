"""Sandbox tracking for container execution.

This module extracts sandbox management from scope.py, following
the composition pattern established by ScopePersistence. It encapsulates:
- Sandbox registration and lookup
- Hierarchy-aware sandbox search
- Sandbox cleanup on scope exit

The SandboxTracker class is owned by ScopeProxy and provides
tracking of container sandboxes for overlay layering.

See Also:
    PLAN-workspace-patch-layering.md (Change 8)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from .substrate import Stream

logger = logging.getLogger(__name__)

__all__ = ["SandboxTracker"]


class SandboxTracker:
    """Tracks container sandboxes for overlay layering.

    Manages sandbox registration and lookup within a scope hierarchy.
    Each scope has its own tracker that can delegate to a parent tracker
    for hierarchy-aware lookups.

    This class was extracted from ScopeProxy to reduce its responsibilities
    and improve testability.

    Usage:
        # In ScopeProxy.__init__:
        self._sandbox_tracker = SandboxTracker(
            parent_tracker=parent._sandbox_tracker if parent else None,
            stream_accessor=lambda: self._scope._stream,
        )

        # Register a sandbox:
        self._sandbox_tracker.register(sandbox)

        # Look up a sandbox:
        sandbox = self._sandbox_tracker.get(sandbox_id)

        # Find latest sandbox for a context:
        sandbox = self._sandbox_tracker.get_latest_for_context("workspace")

        # Cleanup on scope exit:
        self._sandbox_tracker.cleanup()
    """

    def __init__(
        self,
        parent_tracker: SandboxTracker | None = None,
        stream_accessor: Callable[[], Stream] | None = None,
        scope_id: str | None = None,
        *,
        propagate_on_register: bool = True,
    ) -> None:
        """Initialize the sandbox tracker.

        Args:
            parent_tracker: Parent scope's tracker for hierarchy lookups.
            stream_accessor: Callable that returns the effect stream.
                Used by get_latest_for_context() to search effects.
            scope_id: Scope ID for debug logging.
            propagate_on_register: If True, register() propagates sandbox
                entries to ancestor trackers.  Set to False for fork trackers
                where the parent link is only for read lookups — sandbox
                registrations should stay local until an explicit merge().
        """
        self._sandboxes: dict[str, Any] = {}
        self._parent = parent_tracker
        self._get_stream = stream_accessor
        self._scope_id = scope_id
        self._propagate_on_register = propagate_on_register

    def register(self, sandbox: Any) -> None:
        """Register a container sandbox for tracking.

        Called by ContainerDevice after creating a sandbox. Enables subsequent
        tasks to find parent sandboxes for overlay layering.

        Args:
            sandbox: ContainerSandbox instance with sandbox_id attribute.
        """
        sandbox_id = getattr(sandbox, "sandbox_id", None)
        if sandbox_id is None:
            logger.warning("Cannot register sandbox without sandbox_id")
            return
        self._sandboxes[sandbox_id] = sandbox
        logger.debug(
            "Registered sandbox %s in scope %s",
            sandbox_id,
            self._scope_id or "unknown",
        )

        # Propagate to ancestor scopes so sibling tasks can find each other's
        # sandboxes and Device() exit cleanup can locate them.
        # Skipped for fork trackers where the link is read-only — sandbox
        # registrations stay local until merge() calls merge_from().
        if self._propagate_on_register:
            ancestor = self._parent
            while ancestor is not None:
                ancestor._sandboxes[sandbox_id] = sandbox
                ancestor = ancestor._parent

    @property
    def parent_tracker(self) -> SandboxTracker | None:
        """Return the parent tracker used for hierarchy-aware lookup."""
        return self._parent

    def set_parent_tracker(self, parent_tracker: SandboxTracker | None) -> None:
        """Update the parent tracker used for hierarchy-aware lookup."""
        self._parent = parent_tracker

    def merge_from(self, other: SandboxTracker) -> None:
        """Absorb sandbox registrations from another tracker.

        Used during merge() to copy sandboxes created in a fork into the
        parent tracker so that subsequent forks can discover them.
        """
        for sandbox_id, sandbox in other._sandboxes.items():
            if sandbox_id not in self._sandboxes:
                self._sandboxes[sandbox_id] = sandbox
                logger.debug(
                    "Absorbed sandbox %s from merged fork into scope %s",
                    sandbox_id,
                    self._scope_id or "unknown",
                )

    def get(self, sandbox_id: str) -> Any | None:
        """Get a sandbox by ID, searching up the scope hierarchy.

        Args:
            sandbox_id: The sandbox ID to look up.

        Returns:
            ContainerSandbox if found, None otherwise.
        """
        if sandbox_id in self._sandboxes:
            return self._sandboxes[sandbox_id]
        if self._parent is not None:
            return self._parent.get(sandbox_id)
        return None

    def get_latest_for_context(self, context_name: str) -> Any | None:
        """Get the most recent sandbox for a given context name.

        Searches the effect stream for ContainerExecutionCompleted effects
        and returns the sandbox associated with the most recent one matching
        the context name.

        Args:
            context_name: The context binding name (e.g., "workspace").

        Returns:
            ContainerSandbox if found, None otherwise.
        """
        from shepherd_core.effects import ContainerExecutionCompleted

        # Search effect stream in reverse (most recent first)
        stream = self._get_stream() if self._get_stream else None
        if stream is not None:
            for layer in reversed(stream._layers):
                effect = layer.effect
                if isinstance(effect, ContainerExecutionCompleted) and effect.context_name == context_name:
                    sandbox = self.get(effect.sandbox_id)
                    if sandbox is not None:
                        return sandbox

        # Check parent scope
        if self._parent is not None:
            return self._parent.get_latest_for_context(context_name)

        return None

    def cleanup(self) -> None:
        """Clean up all registered sandboxes.

        Called during scope exit to unmount overlays and delete temp directories.
        Errors are logged but don't prevent cleanup of other sandboxes.
        """
        for sandbox_id, sandbox in list(self._sandboxes.items()):
            try:
                # Try cleanup() first (ContainerSandbox method)
                if hasattr(sandbox, "cleanup"):
                    sandbox.cleanup()
                    logger.debug("Cleaned up sandbox %s", sandbox_id)
                elif hasattr(sandbox, "discard"):
                    sandbox.discard()
                    logger.debug("Discarded sandbox %s", sandbox_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("Sandbox cleanup failed for %s: %s", sandbox_id, e)
        self._sandboxes.clear()

    def __repr__(self) -> str:
        return f"SandboxTracker(sandboxes={len(self._sandboxes)}, has_parent={self._parent is not None})"
