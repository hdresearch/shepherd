"""Runtime-owned provider registration state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shepherd_core.provider import Provider

__all__ = ["ProviderState"]


@dataclass(frozen=True)
class ProviderState:
    """Immutable provider-registration state owned by the runtime scope shell."""

    providers: tuple[tuple[str, Provider], ...] = ()
    default_provider: str | None = None

    def with_provider(self, name: str, provider: Provider, default: bool = False) -> ProviderState:
        new_providers = (*self.providers, (name, provider))
        new_default = name if default or self.default_provider is None else self.default_provider
        return replace(self, providers=new_providers, default_provider=new_default)

    def get_local(self, name: str) -> Provider | None:
        for provider_name, provider in self.providers:
            if provider_name == name:
                return provider
        return None

    def has_local(self, name: str) -> bool:
        return self.get_local(name) is not None
