"""Testing utilities for runtime-owned handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shepherd_core.foundation import ScopeProtocol


class SimpleHandlerContext:
    """Minimal handler context implementation for tests."""

    def __init__(
        self,
        scope: ScopeProtocol,
        state: dict[str, Any] | None = None,
        device: str | None = None,
    ) -> None:
        self._scope = scope
        self._state = state or {}
        self._device = device

    @property
    def scope(self) -> ScopeProtocol:
        return self._scope

    @property
    def scope_id(self) -> str:
        return self._scope.id

    @property
    def device(self) -> str | None:
        return self._device

    def get_state(self, context_id: str) -> Any:
        return self._state.get(context_id)

    def set_state(self, context_id: str, state: Any) -> None:
        self._state[context_id] = state


__all__ = ["SimpleHandlerContext"]
