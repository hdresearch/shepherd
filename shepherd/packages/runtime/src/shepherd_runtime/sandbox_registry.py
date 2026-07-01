"""Runtime-owned sandbox registry and factory helpers."""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable, Iterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shepherd_core.context.kernel import ExecutionContext

    from shepherd_runtime.context import Sandbox

logger = logging.getLogger(__name__)

SandboxFactory = Callable[["ExecutionContext"], "Sandbox | None"]


class SandboxRegistry:
    """Injectable registry for sandbox factories."""

    def __init__(self) -> None:
        self._factories: dict[str, SandboxFactory] = {}

    def register(self, context_type_name: str, factory: SandboxFactory) -> None:
        """Register a sandbox factory for a context type."""
        self._factories[context_type_name] = factory

    def has_factory(self, context_type_name: str) -> bool:
        """Check whether a factory is registered for the given context type."""
        return context_type_name in self._factories

    def create_for(self, context: ExecutionContext) -> Sandbox | None:
        """Create a sandbox for a context using the registered factory."""
        type_name = type(context).__name__
        factory = self._factories.get(type_name)

        if factory is None:
            requires = getattr(type(context), "requires_sandbox", None)
            if callable(requires) and requires():
                warnings.warn(
                    f"{type_name} declares requires_sandbox()=True but no factory "
                    f"is registered. Call register_sandbox_factory('{type_name}', "
                    f"factory) in your module's __init__.py.",
                    UserWarning,
                    stacklevel=4,
                )

        return factory(context) if factory else None

    def copy(self) -> SandboxRegistry:
        """Create an independent copy of this registry."""
        new_registry = SandboxRegistry()
        new_registry._factories = dict(self._factories)
        return new_registry

    def clear(self) -> None:
        """Remove all registered factories."""
        self._factories.clear()

    @property
    def factories(self) -> dict[str, SandboxFactory]:
        """Read-only live view of registered factories."""
        return MappingProxyType(self._factories)  # type: ignore[return-value]

    def __len__(self) -> int:
        return len(self._factories)

    def __contains__(self, context_type_name: str) -> bool:
        return self.has_factory(context_type_name)


_default_registry: SandboxRegistry | None = None


def get_default_registry() -> SandboxRegistry:
    """Get the default global sandbox registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = SandboxRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """Reset the default registry to a fresh instance."""
    global _default_registry
    _default_registry = None


def register_sandbox_factory(
    context_type_name: str,
    factory: SandboxFactory,
) -> None:
    """Register a sandbox factory on the default runtime registry."""
    get_default_registry().register(context_type_name, factory)


def create_sandbox_for_context(context: ExecutionContext) -> Sandbox | None:
    """Create a sandbox for a context via the runtime registry."""
    return get_default_registry().create_for(context)


def get_sandbox_factories() -> Mapping[str, SandboxFactory]:
    """Return a live read-only view of registered sandbox factories."""
    return get_default_registry().factories


class _SandboxFactoriesView(Mapping[str, SandboxFactory]):
    """Read-only mapping that follows the current default runtime registry."""

    def __getitem__(self, key: str) -> SandboxFactory:
        return get_default_registry().factories[key]

    def __iter__(self) -> Iterator[str]:
        return iter(get_default_registry().factories)

    def __len__(self) -> int:
        return len(get_default_registry().factories)


sandbox_factories: Mapping[str, SandboxFactory] = _SandboxFactoriesView()


__all__ = [
    "SandboxFactory",
    "SandboxRegistry",
    "create_sandbox_for_context",
    "get_default_registry",
    "get_sandbox_factories",
    "register_sandbox_factory",
    "reset_default_registry",
    "sandbox_factories",
]
