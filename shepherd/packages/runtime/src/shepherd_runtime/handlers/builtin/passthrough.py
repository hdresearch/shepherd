"""Passthrough runtime handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shepherd_core.effects import Effect

    from shepherd_runtime.handlers.protocol import HandlerContext


class PassthroughHandler:
    """Handler that acknowledges an effect without taking action."""

    def __init__(self, result: Any = None) -> None:
        self._result = result

    @property
    def effect_type(self) -> type[Effect]:
        from shepherd_core.effects import Effect

        return Effect

    async def handle(
        self,
        effect: Effect,
        context: HandlerContext,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        return await resume(self._result)

    def can_handle(self, effect: Effect) -> bool:
        return True


__all__ = ["PassthroughHandler"]
