"""Runtime-owned handler registry."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shepherd_core.effects import Effect

    from .builtin import CompositeHandler
    from .protocol import EffectHandler, HandlerContext

logger = logging.getLogger(__name__)


class HandlerNotFoundError(Exception):
    """Raised when no handler is registered for an effect type."""

    def __init__(self, effect: Effect):
        self.effect = effect
        super().__init__(f"No handler registered for effect type: {effect.effect_type}")


class HandlerRegistry:
    """Registry for effect handlers with type-based and device-specific dispatch."""

    def __init__(self) -> None:
        self._handlers: dict[type, EffectHandler] = {}  # type: ignore[type-arg]
        self._device_handlers: dict[str, dict[type, EffectHandler]] = {}  # type: ignore[type-arg]

    def register(
        self,
        handler: EffectHandler,  # type: ignore[type-arg]
        device: str | None = None,
    ) -> None:
        """Register a handler, optionally for a specific device."""
        effect_type = handler.effect_type

        if device is None:
            if effect_type in self._handlers:
                logger.warning(f"Replacing existing handler for {effect_type.__name__}")
            self._handlers[effect_type] = handler
            logger.debug(f"Registered handler for {effect_type.__name__}")
            return

        if device not in self._device_handlers:
            self._device_handlers[device] = {}
        device_handlers = self._device_handlers[device]
        if effect_type in device_handlers:
            logger.warning(f"Replacing existing {device} handler for {effect_type.__name__}")
        device_handlers[effect_type] = handler
        logger.debug(f"Registered {device} handler for {effect_type.__name__}")

    def unregister(
        self,
        effect_type: type,
        device: str | None = None,
    ) -> EffectHandler | None:  # type: ignore[type-arg]
        """Remove and return the handler for an effect type."""
        if device is None:
            return self._handlers.pop(effect_type, None)
        if device in self._device_handlers:
            return self._device_handlers[device].pop(effect_type, None)
        return None

    def get(
        self,
        effect: Effect,
        device: str | None = None,
    ) -> EffectHandler | None:  # type: ignore[type-arg]
        """Get the handler for an effect, checking device-specific then default."""
        effect_cls = type(effect)

        if device and device in self._device_handlers:
            device_handlers = self._device_handlers[device]
            if effect_cls in device_handlers:
                handler = device_handlers[effect_cls]
                if handler.can_handle(effect):
                    return handler

            for base in effect_cls.__mro__[1:]:
                if base in device_handlers:
                    handler = device_handlers[base]
                    if handler.can_handle(effect):
                        return handler

        if effect_cls in self._handlers:
            handler = self._handlers[effect_cls]
            if handler.can_handle(effect):
                return handler

        for base in effect_cls.__mro__[1:]:
            if base in self._handlers:
                handler = self._handlers[base]
                if handler.can_handle(effect):
                    return handler

        return None

    def has_handler(self, effect: Effect, device: str | None = None) -> bool:
        """Check if a handler exists for an effect."""
        return self.get(effect, device=device) is not None

    def handlers_for_device(self, device: str) -> CompositeHandler:
        """Build a composite handler combining device-specific and defaults."""
        from .builtin import CompositeHandler

        combined: dict[type, EffectHandler] = dict(self._handlers)  # type: ignore[type-arg]
        if device in self._device_handlers:
            combined.update(self._device_handlers[device])

        return CompositeHandler(*combined.values())

    async def handle(
        self,
        effect: Effect,
        context: HandlerContext,
        resume: Callable[[Any], Awaitable[Any]] | None = None,
        device: str | None = None,
    ) -> Any:
        """Handle an effect using the registered handler."""
        handler = self.get(effect, device=device)
        if handler is None:
            raise HandlerNotFoundError(effect)

        if resume is None:

            async def identity(result: Any) -> Any:
                return result

            resume = identity

        return await handler.handle(effect, context, resume)

    def registered_types(self, device: str | None = None) -> list[type]:
        """Return registered effect types."""
        if device is None:
            return list(self._handlers.keys())
        if device in self._device_handlers:
            return list(self._device_handlers[device].keys())
        return []

    def __len__(self) -> int:
        return len(self._handlers)

    def __contains__(self, effect_type: type) -> bool:
        return effect_type in self._handlers


_default_registry: HandlerRegistry | None = None


def get_default_registry() -> HandlerRegistry:
    """Get the global default handler registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = HandlerRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """Reset the global registry."""
    global _default_registry
    _default_registry = None


def register_handler(handler: EffectHandler) -> None:  # type: ignore[type-arg]
    """Register a handler in the global registry."""
    get_default_registry().register(handler)


def get_handler(effect: Effect) -> EffectHandler | None:  # type: ignore[type-arg]
    """Get a handler from the global registry."""
    return get_default_registry().get(effect)


__all__ = [
    "HandlerNotFoundError",
    "HandlerRegistry",
    "get_default_registry",
    "get_handler",
    "register_handler",
    "reset_default_registry",
]
