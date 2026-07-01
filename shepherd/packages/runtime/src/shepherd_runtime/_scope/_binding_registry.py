"""Binding registry with lifecycle state tracking.

This module provides:
- LifecycleState: Ephemeral state tracked during execution
- BindingWithState: Wrapper combining immutable binding with mutable state
- BindingRegistry: Manages lifecycle state for context bindings

The BindingRegistry uses getter/setter callbacks to update the immutable scope,
allowing it to encapsulate binding lifecycle logic while ScopeProxy remains
the single owner of the ImmutableScope instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from shepherd_core.context.kernel import ExecutionContext

    from .substrate import ContextBinding

__all__ = ["BindingRegistry", "BindingWithState", "LifecycleState"]


# =============================================================================
# Lifecycle State
# =============================================================================


@dataclass
class LifecycleState:
    """Ephemeral lifecycle state tracked by ScopeProxy.

    This state is only relevant during execution and is NOT part of the
    immutable scope. It tracks whether prepare() and cleanup() have been
    called on a context.

    Attributes:
        is_prepared: Whether prepare() has been called
        in_lifecycle: Whether this binding is managed by an active ExecutionLifecycle
    """

    is_prepared: bool = False
    in_lifecycle: bool = False


# =============================================================================
# Binding With State (Wrapper for backward compatibility)
# =============================================================================


class BindingWithState:
    """Wrapper providing lifecycle state on immutable binding.

    For backward compatibility with code that accesses binding.is_prepared
    or binding.in_lifecycle. The wrapper delegates to the underlying
    immutable ContextBinding for name/context, and to a mutable LifecycleState
    for lifecycle properties.
    """

    __slots__ = ("_binding", "_state")

    def __init__(self, binding: ContextBinding, state: LifecycleState):
        self._binding = binding
        self._state = state

    @property
    def name(self) -> str:
        return self._binding.name

    @property
    def context(self) -> ExecutionContext:
        return self._binding.context

    @context.setter
    def context(self, value: ExecutionContext) -> None:
        """Allow setting context for backward compatibility.

        Note: This creates a new binding internally but the wrapper
        maintains the reference for the lifecycle state.
        """
        # This is a compatibility shim - actual update goes through proxy
        raise AttributeError(
            "Cannot set context directly on BindingWithState. Use scope.update_context(name, new_context) instead."
        )

    @property
    def is_prepared(self) -> bool:
        return self._state.is_prepared

    @is_prepared.setter
    def is_prepared(self, value: bool) -> None:
        self._state.is_prepared = value

    @property
    def in_lifecycle(self) -> bool:
        return self._state.in_lifecycle

    @in_lifecycle.setter
    def in_lifecycle(self, value: bool) -> None:
        self._state.in_lifecycle = value

    def __repr__(self) -> str:
        state = []
        if self.is_prepared:
            state.append("prepared")
        if self.in_lifecycle:
            state.append("in_lifecycle")
        state_str = f" [{', '.join(state)}]" if state else ""
        return f"ContextBinding({self.name}{state_str})"


# =============================================================================
# Binding Registry
# =============================================================================


class BindingRegistry:
    """Manages lifecycle state for context bindings.

    This class encapsulates the lifecycle state tracking that was previously
    spread across ScopeProxy. It uses getter/setter callbacks to access/update
    the ImmutableScope, keeping ScopeProxy as the single owner.

    The registry tracks:
    - Lifecycle state (is_prepared, in_lifecycle) for each binding
    - Provides BindingWithState wrappers for backward compatibility

    Note: The registry does NOT own bindings - those are stored in ImmutableScope.
    It only tracks the ephemeral lifecycle state.
    """

    __slots__ = ("_get_bindings", "_lifecycle_state")

    def __init__(
        self,
        bindings_getter: Callable[[], tuple[ContextBinding, ...]],
    ) -> None:
        """Initialize the registry.

        Args:
            bindings_getter: Callback to get current bindings from ImmutableScope
        """
        self._get_bindings = bindings_getter
        self._lifecycle_state: dict[str, LifecycleState] = {}

    def on_bind(self, name: str) -> None:
        """Called when a new binding is added.

        Creates lifecycle state for the binding.
        """
        self._lifecycle_state[name] = LifecycleState()

    def get_state(self, name: str) -> LifecycleState:
        """Get lifecycle state for a binding, creating if needed."""
        state = self._lifecycle_state.get(name)
        if state is None:
            state = LifecycleState()
            self._lifecycle_state[name] = state
        return state

    def mark_state(
        self,
        name: str,
        *,
        is_prepared: bool | None = None,
        in_lifecycle: bool | None = None,
    ) -> None:
        """Update lifecycle state for a binding.

        Args:
            name: Binding name
            is_prepared: Set prepared state (if not None)
            in_lifecycle: Set in_lifecycle state (if not None)
        """
        state = self.get_state(name)
        if is_prepared is not None:
            state.is_prepared = is_prepared
        if in_lifecycle is not None:
            state.in_lifecycle = in_lifecycle

    def wrap_binding(self, binding: ContextBinding) -> BindingWithState:
        """Wrap a binding with its lifecycle state.

        Returns a BindingWithState that provides both the immutable binding
        data and the mutable lifecycle state.
        """
        state = self.get_state(binding.name)
        return BindingWithState(binding, state)

    def all_with_state(self) -> list[BindingWithState]:
        """Get all local bindings wrapped with their lifecycle state.

        Note: This only returns bindings from the current scope, not inherited.
        Parent bindings should be fetched separately.
        """
        return [self.wrap_binding(b) for b in self._get_bindings()]

    def clear_state(self, name: str) -> None:
        """Remove lifecycle state for a binding.

        Called when a binding is removed or during cleanup.
        """
        self._lifecycle_state.pop(name, None)

    def copy_state_to(self, other: BindingRegistry) -> None:
        """Copy all lifecycle state to another registry.

        Used when forking scopes.
        """
        for name, state in self._lifecycle_state.items():
            other._lifecycle_state[name] = LifecycleState(
                is_prepared=state.is_prepared,
                in_lifecycle=state.in_lifecycle,
            )

    def reset_lifecycle_for_fork(self) -> None:
        """Reset lifecycle state for a forked scope.

        Forked scopes start with fresh lifecycle state.
        """
        for name in list(self._lifecycle_state.keys()):
            self._lifecycle_state[name] = LifecycleState()
