"""Composite runtime handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shepherd_core.effects import Effect

    from shepherd_runtime.handlers.protocol import EffectHandler, HandlerContext


class CompositeHandler:
    """Handler that delegates to child handlers based on effect type."""

    def __init__(
        self,
        *handlers: EffectHandler,  # type: ignore[type-arg]
        strict: bool = False,
    ) -> None:
        self._handlers = list(handlers)
        self._dispatch: dict[type, EffectHandler] = {}  # type: ignore[type-arg]
        self._strict = strict
        for handler in handlers:
            self._dispatch[handler.effect_type] = handler

    @property
    def effect_type(self) -> type[Effect]:
        from shepherd_core.effects import Effect

        return Effect

    def add_handler(self, handler: EffectHandler) -> None:  # type: ignore[type-arg]
        self._handlers.append(handler)
        self._dispatch[handler.effect_type] = handler

    def remove_handler(self, effect_type: type) -> EffectHandler | None:  # type: ignore[type-arg]
        handler = self._dispatch.pop(effect_type, None)
        if handler:
            self._handlers.remove(handler)
        return handler

    def get_handler(self, effect: Effect) -> EffectHandler | None:  # type: ignore[type-arg]
        effect_cls = type(effect)

        if effect_cls in self._dispatch:
            return self._dispatch[effect_cls]

        for base in effect_cls.__mro__[1:]:
            if base in self._dispatch:
                handler = self._dispatch[base]
                if handler.can_handle(effect):
                    return handler

        return None

    async def handle(
        self,
        effect: Effect,
        context: HandlerContext,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        handler = self.get_handler(effect)
        if handler is None:
            if self._strict:
                raise ValueError(f"No handler found for effect type: {effect.effect_type}")
            return await resume(None)
        return await handler.handle(effect, context, resume)

    def can_handle(self, effect: Effect) -> bool:
        if self._strict:
            return self.get_handler(effect) is not None
        return True

    @property
    def handlers(self) -> list[EffectHandler]:  # type: ignore[type-arg]
        return list(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)


__all__ = ["CompositeHandler"]
