"""Provider registration and lookup.

This module extracts provider management from scope.py, following the
composition pattern used by BindingRegistry.

The ProviderRegistry owns runtime-local provider registration state rather than
storing providers on the kernel `ImmutableScope` substrate. It provides:
- Mutable facade via getter/setter callbacks
- Live parent-scope lookup for nested scopes
- Flattened provider snapshots for forked scopes
- Type-safe interface for provider management
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shepherd_core.errors import ProviderNotFoundError

from ._provider_state import ProviderState

if TYPE_CHECKING:
    from collections.abc import Callable

    from shepherd_core.provider import Provider

__all__ = ["ProviderRegistry"]


class ProviderRegistry:
    """Manages provider registration and lookup.

    Uses getter/setter callbacks to update runtime-owned provider state while
    supporting live parent-scope lookup through the owning `ScopeProxy`.

    This design enables:
    - Unit testing ProviderRegistry with mock getters/setters
    - Clear separation of concerns (provider management vs scope lifecycle)
    - Consistent mutation pattern across all scope subsystems

    Example:
        # In ScopeProxy.__init__:
        self._scope = ImmutableScope(...)
        self._providers = ProviderRegistry(
            scope_getter=lambda: self._scope,
            scope_setter=lambda s: setattr(self, '_scope', s),
        )

        # Usage:
        self._providers.register("analyst", ClaudeProvider(), default=True)
        provider = self._providers.get("analyst")
    """

    def __init__(
        self,
        state_getter: Callable[[], ProviderState],
        state_setter: Callable[[ProviderState], None],
        parent_registry_getter: Callable[[], ProviderRegistry | None],
    ) -> None:
        """Initialize provider registry with runtime provider-state callbacks.

        Args:
            state_getter: Callable that returns the current local provider state
            state_setter: Callable that updates the local provider state
            parent_registry_getter: Callable returning the live parent registry
        """
        self._get_state = state_getter
        self._set_state = state_setter
        self._get_parent_registry = parent_registry_getter

    def register(
        self,
        name: str,
        provider: Provider,
        *,
        default: bool = False,
    ) -> None:
        """Register a provider by name.

        Args:
            name: Name/role for the provider (e.g., "analyst", "fetcher")
            provider: The provider instance
            default: If True, set as default provider. If no default is set,
                    the first registered provider becomes the default.
        """
        state = self._get_state()
        self._set_state(state.with_provider(name, provider, default))

    def get(self, name: str | None = None) -> Provider:
        """Get provider by name, or the default provider.

        Provider lookup follows inheritance: if a provider isn't found locally,
        the parent scope is checked.

        Args:
            name: Provider name to look up. If None, returns the default provider.

        Returns:
            The requested provider instance

        Raises:
            ProviderNotFoundError: If the provider is not registered
        """
        state = self._get_state()
        parent = self._get_parent_registry()

        if name is None:
            default_name = state.default_provider
            if default_name is not None:
                name = default_name
            elif parent is not None:
                return parent.get(None)
            else:
                raise ProviderNotFoundError("default", self._available_provider_names())

        provider = state.get_local(name)
        if provider is not None:
            return provider

        if parent is not None:
            return parent.get(name)

        raise ProviderNotFoundError(name, self._available_provider_names())

    def has(self, name: str) -> bool:
        """Check if a provider is registered.

        Args:
            name: Provider name to check

        Returns:
            True if the provider exists (locally or in parent scope)
        """
        state = self._get_state()
        if state.has_local(name):
            return True
        parent = self._get_parent_registry()
        return parent.has(name) if parent is not None else False

    @property
    def default_name(self) -> str | None:
        """Name of the default provider, or None if no providers registered."""
        return self._get_state().default_provider

    @property
    def effective_default_name(self) -> str | None:
        """Effective default provider name, including inherited scope state."""
        state = self._get_state()
        if state.default_provider is not None:
            return state.default_provider
        parent = self._get_parent_registry()
        return parent.effective_default_name if parent is not None else None

    @property
    def all_providers(self) -> dict[str, Provider]:
        """Get all locally registered providers as a dict.

        Note: Does not include inherited providers from parent scope.
        For full provider lookup including inheritance, use get().

        Returns:
            Dict mapping provider name to provider instance
        """
        return dict(self._get_state().providers)

    def effective_snapshot(self) -> ProviderState:
        """Flatten inherited provider state for forked-scope snapshots."""
        local_state = self._get_state()
        parent = self._get_parent_registry()
        if parent is None:
            return local_state

        parent_state = parent.effective_snapshot()
        local_names = {name for name, _ in local_state.providers}
        inherited = tuple(provider for provider in parent_state.providers if provider[0] not in local_names)
        return ProviderState(
            providers=inherited + local_state.providers,
            default_provider=self.effective_default_name,
        )

    def _available_provider_names(self) -> list[str]:
        snapshot = self.effective_snapshot()
        return [provider_name for provider_name, _ in snapshot.providers]

    def __repr__(self) -> str:
        state = self._get_state()
        count = len(state.providers)
        default = state.default_provider
        return f"ProviderRegistry({count} providers, default={default!r})"
