"""Hierarchy ownership for ScopeProxy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from shepherd_core.errors import ContainmentError
from shepherd_core.foundation.errors import ScopeError

from .substrate import ImmutableScope, Stream

if TYPE_CHECKING:
    from shepherd_core.effects import Effect

    from ._provider_state import ProviderState
    from .scope import ScopeProxy

__all__ = ["HierarchyCoordinator", "HierarchyHost"]


class HierarchyHost(Protocol):
    """Narrow host contract for scope hierarchy operations."""

    def hierarchy_snapshot(self) -> ImmutableScope: ...

    def replace_hierarchy_snapshot(self, scope: ImmutableScope) -> None: ...

    def create_hierarchy_scope(
        self,
        scope: ImmutableScope,
        *,
        root: bool = False,
        provider_state: ProviderState | None = None,
    ) -> ScopeProxy: ...

    def hierarchy_provider_state(self) -> ProviderState: ...

    def replace_hierarchy_provider_state(self, state: ProviderState) -> None: ...

    def effective_hierarchy_provider_state(self) -> ProviderState: ...

    @property
    def hierarchy_parent(self) -> ScopeProxy | None: ...

    @hierarchy_parent.setter
    def hierarchy_parent(self, value: ScopeProxy | None) -> None: ...

    @property
    def hierarchy_depth(self) -> int: ...

    @hierarchy_depth.setter
    def hierarchy_depth(self, value: int) -> None: ...

    def set_hierarchy_sandbox_parent(self, parent: ScopeProxy) -> None: ...

    @property
    def hierarchy_has_project_path(self) -> bool: ...

    @property
    def hierarchy_persistence_requested(self) -> bool: ...

    @property
    def hierarchy_is_global(self) -> bool: ...

    def initialize_requested_root_persistence(self) -> None: ...

    def finalize_forked_scope(self, forked: ScopeProxy) -> None: ...

    @property
    def hierarchy_is_discarded(self) -> bool: ...

    @hierarchy_is_discarded.setter
    def hierarchy_is_discarded(self, value: bool) -> None: ...

    @property
    def hierarchy_is_materialized(self) -> bool: ...

    def cleanup_registered_sandboxes(self) -> None: ...

    def emit_merged_effect(self, effect: Effect) -> None: ...


class HierarchyCoordinator:
    """Owns child/fork/merge/discard transitions for ScopeProxy."""

    __slots__ = ("_host", "_owner")

    def __init__(self, owner: ScopeProxy, host: HierarchyHost) -> None:
        self._owner = owner
        self._host = host

    def child(self) -> ScopeProxy:
        """Create a child scope with live parent linkage."""
        child_scope = ImmutableScope(_parent=self._host.hierarchy_snapshot())
        child_proxy = self._host.create_hierarchy_scope(child_scope)
        child_proxy._attach_to_parent(self._owner)
        return child_proxy

    def attach_to_parent(self, parent: ScopeProxy) -> None:
        """Attach the current scope to a live parent proxy."""
        self._host.hierarchy_parent = parent
        self._host.replace_hierarchy_snapshot(self._host.hierarchy_snapshot().with_parent(parent._scope))
        self._host.hierarchy_depth = parent._depth + 1
        self._host.set_hierarchy_sandbox_parent(parent)

    def validate_auto_nesting_configuration(self) -> None:
        """Reject root-only configuration on implicitly nested scopes."""
        if self._host.hierarchy_has_project_path or self._host.hierarchy_persistence_requested:
            raise ValueError(
                "Nested Scope() cannot set project_path or enable persistence. Use root=True for an independent scope."
            )

    def initialize_root_persistence(self) -> None:
        """Initialize stream persistence once the scope is known to be root-like."""
        if self._host.hierarchy_parent is not None:
            return
        if not self._host.hierarchy_persistence_requested:
            return
        if not self._host.hierarchy_has_project_path:
            return
        if self._host.hierarchy_is_global:
            return
        self._host.initialize_requested_root_persistence()

    def fork(self) -> ScopeProxy:
        """Create an independent fork for speculative execution."""
        snapshot = self._host.hierarchy_snapshot()
        origin_id = snapshot._origin_id if snapshot._origin_id is not None else snapshot._id
        forked_scope = ImmutableScope(
            _bindings=snapshot.all_bindings,
            _origin_id=origin_id,
        )
        forked_proxy = self._host.create_hierarchy_scope(
            forked_scope,
            root=True,
            provider_state=self._host.effective_hierarchy_provider_state(),
        )
        self._host.finalize_forked_scope(forked_proxy)

        # Link the fork's sandbox tracker to the parent's tracker so that
        # get_latest_for_context() can discover sandboxes registered in
        # previously-merged forks.  This is a read-only lookup path
        # (propagate_on_register=False) — the fork's own registrations stay
        # in its local dict until an explicit merge() calls merge_from().
        forked_proxy._sandbox_tracker.set_parent_tracker(self._owner._sandbox_tracker)
        forked_proxy._sandbox_tracker._propagate_on_register = False

        return forked_proxy

    def cleanup_sandboxes(self) -> None:
        """Clean up all registered sandboxes."""
        self._host.cleanup_registered_sandboxes()

    def merge_effects(self, stream: Stream) -> None:
        """Merge effects from a forked scope into this scope."""
        import warnings

        warnings.warn(
            "merge_effects() is deprecated, use merge() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        for layer in stream.layers:
            self._host.emit_merged_effect(layer.effect)

    def merge(self, child: ScopeProxy) -> None:
        """Propagate a fork's effects, sandbox registrations, and context updates into this scope.

        In addition to copying effects (the original behaviour), merge now:
        1. Absorbs sandbox registrations so subsequent forks can discover
           sandboxes created in the merged fork (overlay stacking).
        2. Propagates context binding updates so subsequent forks snapshot
           up-to-date context state (e.g. pending_patches for transfer bundles).
        """
        if child.is_discarded:
            raise ScopeError("Cannot merge - child scope was discarded")
        if child.is_materialized:
            raise ContainmentError("Cannot merge - child scope has escaped containment")

        # 1. Copy effects into parent stream (existing behaviour).
        for layer in child.effects:
            self._host.emit_merged_effect(layer.effect)

        # 2. Absorb sandbox registrations from the merged fork so that
        #    get_latest_for_context() can find them via the parent tracker.
        self._owner._sandbox_tracker.merge_from(child._sandbox_tracker)

        # 3. Propagate context binding updates.  The fork's apply phase may
        #    have called update_context() to record new state (e.g.
        #    pending_patches on WorkspaceRef).  Without this, the parent's
        #    bindings remain stale and subsequent forks snapshot outdated
        #    context, breaking transfer bundles and overlay layering.
        import contextlib

        from shepherd_core.errors import BindingNotFoundError

        for child_binding in child.all_bindings():
            with contextlib.suppress(BindingNotFoundError, KeyError):
                self._owner.update_context(child_binding.name, child_binding.context)

    def discard(self) -> None:
        """Abandon this scope's effects and clean up registered sandboxes."""
        if self._host.hierarchy_is_discarded:
            return
        if self._host.hierarchy_is_materialized:
            raise ContainmentError("Cannot discard - effects have escaped via materialize()")

        self.cleanup_sandboxes()
        snapshot = self._host.hierarchy_snapshot()
        self._host.hierarchy_is_discarded = True
        self._host.replace_hierarchy_snapshot(
            ImmutableScope(
                _id=snapshot._id,
                _bindings=(),
                _stream=Stream(),
            )
        )
        self._host.replace_hierarchy_provider_state(self._host.hierarchy_provider_state())
