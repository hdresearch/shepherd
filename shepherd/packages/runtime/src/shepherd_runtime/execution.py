"""Runtime-owned execution, device-selection, and cache delegation for Scope."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from shepherd_core.context.kernel import ExecutionContext
    from shepherd_core.foundation.protocols.device import DeviceProtocol
    from shepherd_core.provider import Provider

    from ._scope.scope import ScopeProxy
    from .cache import CacheStore
    from .persistence import PersistenceConfig

__all__ = ["ScopeExecutionCacheFacade", "ScopeExecutionCacheHost"]


class ScopeExecutionCacheHost(Protocol):
    """Narrow host contract for execution/device/cache delegation."""

    @property
    def execution_parent(self) -> ScopeProxy | None: ...

    @property
    def execution_device_override(self) -> DeviceProtocol | None: ...

    @execution_device_override.setter
    def execution_device_override(self, value: DeviceProtocol | None) -> None: ...

    def execution_ambient_device(self) -> DeviceProtocol | None: ...

    def resolve_execution_provider(self, provider: Provider | str | None) -> Provider: ...

    def execution_cache_view(self) -> CacheStore | None: ...

    def execution_cache_store(self) -> CacheStore | None: ...

    def execution_cache_config(self) -> PersistenceConfig: ...


class ScopeExecutionCacheFacade:
    """Owns the remaining execution/device/cache seam for Scope."""

    __slots__ = ("_host", "_owner")

    def __init__(self, owner: ScopeProxy, host: ScopeExecutionCacheHost) -> None:
        self._owner = owner
        self._host = host

    @property
    def current_device(self) -> DeviceProtocol | None:
        """Resolve device override first, then ambient device context."""
        override = self._host.execution_device_override
        if override is not None:
            return override
        return self._host.execution_ambient_device()

    def set_device(self, device: DeviceProtocol) -> None:
        """Set an explicit device override for this scope."""
        self._host.execution_device_override = device

    @property
    def cache(self) -> CacheStore | None:
        """Return cache view without forcing initialization."""
        if self._host.execution_parent is not None:
            return self._host.execution_parent.cache
        return self._host.execution_cache_view()

    def get_cache_store(self) -> CacheStore | None:
        """Return cache store, delegating child scopes to the parent root."""
        if self._host.execution_parent is not None:
            return self._host.execution_parent._get_cache_store()
        return self._host.execution_cache_store()

    def get_cache_config(self) -> PersistenceConfig:
        """Return cache config, delegating child scopes to the parent root."""
        if self._host.execution_parent is not None:
            return self._host.execution_parent._get_cache_config()
        return self._host.execution_cache_config()

    async def execute(
        self,
        prompt: str,
        provider: Provider | str | None = None,
        task_name: str | None = None,
        auto_update_bindings: bool = True,
    ) -> tuple[Any, dict[str, ExecutionContext]]:
        """Execute a prompt through the lifecycle convenience path."""
        del auto_update_bindings

        from shepherd_runtime.lifecycle import execute as _execute

        resolved_provider = self._host.resolve_execution_provider(provider)
        return await _execute(
            self._owner,
            prompt,
            provider=resolved_provider,
            task_name=task_name,
        )

    def execute_sync(
        self,
        prompt: str,
        provider: Provider | str | None = None,
        task_name: str | None = None,
    ) -> tuple[Any, dict[str, ExecutionContext]]:
        """Synchronous wrapper around the async execute convenience path."""
        return asyncio.run(self.execute(prompt, provider=provider, task_name=task_name))
