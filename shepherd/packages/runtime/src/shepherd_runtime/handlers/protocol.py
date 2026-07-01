"""Runtime handler protocols and result types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shepherd_core.effects import Effect
    from shepherd_core.foundation import ScopeProtocol

from shepherd_runtime.effect_materialization import (
    MaterializationError,
    MaterializationResult,
    Materializer,
    ReversalError,
)

E_contra = TypeVar("E_contra", bound="Effect", contravariant=True)
R_co = TypeVar("R_co", covariant=True)


@runtime_checkable
class HandlerContext(Protocol):
    """Minimal state and scope access required by handlers."""

    @property
    def scope(self) -> ScopeProtocol:
        """The scope in which the effect is being handled."""
        ...

    @property
    def scope_id(self) -> str:
        """ID of the current scope."""
        ...

    @property
    def device(self) -> str | None:
        """The current device name, if any."""
        ...

    def get_state(self, context_id: str) -> Any:
        """Get derived state for a specific context binding."""
        ...


@runtime_checkable
class EffectHandler(Protocol[E_contra, R_co]):  # type: ignore[misc]
    """Interprets effects with continuation-passing style."""

    @property
    def effect_type(self) -> type[E_contra]:
        """The effect type this handler processes."""
        ...

    async def handle(
        self,
        effect: E_contra,
        context: HandlerContext,
        resume: Callable[[R_co], Awaitable[Any]],
    ) -> Any:
        """Handle an effect and continue execution through ``resume``."""
        ...

    def can_handle(self, effect: Effect) -> bool:
        """Check if this handler can process the given effect."""
        ...


__all__ = [
    "EffectHandler",
    "HandlerContext",
    "MaterializationError",
    "MaterializationResult",
    "Materializer",
    "ReversalError",
]
