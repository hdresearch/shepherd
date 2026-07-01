"""Runtime-owned provider registry for container reconstruction."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shepherd_core.provider import Provider


ProviderFactory = Callable[[dict[str, Any]], "Provider"]
_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


class ProviderCreationError(Exception):
    """Error during provider creation from config."""

    def __init__(self, provider_type: str, message: str):
        self.provider_type = provider_type
        super().__init__(f"Failed to create provider '{provider_type}': {message}")


def register_provider_factory(
    provider_type: str,
    factory: ProviderFactory,
) -> None:
    """Register a factory for a provider type."""
    _PROVIDER_FACTORIES[provider_type] = factory


def get_provider_factory(provider_type: str) -> ProviderFactory | None:
    """Get the factory for a provider type."""
    return _PROVIDER_FACTORIES.get(provider_type)


def create_provider(config: dict[str, Any]) -> Provider:
    """Create a provider from configuration."""
    provider_type = config.get("provider_type")
    if not provider_type:
        raise ProviderCreationError("unknown", "config missing 'provider_type' field")

    factory = get_provider_factory(provider_type)
    if factory is None:
        available = list(_PROVIDER_FACTORIES.keys())
        raise ProviderCreationError(
            provider_type, f"no factory registered for provider type '{provider_type}'. Available: {available}"
        )

    try:
        return factory(config)
    except Exception as e:
        raise ProviderCreationError(provider_type, f"factory raised: {e}") from e


def list_registered_provider_types() -> list[str]:
    """Return list of registered provider types."""
    return list(_PROVIDER_FACTORIES.keys())


__all__ = [
    "ProviderCreationError",
    "create_provider",
    "get_provider_factory",
    "list_registered_provider_types",
    "register_provider_factory",
]
