"""Logging runtime handler."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shepherd_core.effects import Effect

    from shepherd_runtime.handlers.protocol import EffectHandler, HandlerContext


class LoggingHandler:
    """Handler that logs effects and delegates to an inner handler."""

    def __init__(
        self,
        inner: EffectHandler,  # type: ignore[type-arg]
        logger: logging.Logger | None = None,
        level: int = logging.DEBUG,
    ) -> None:
        self._inner = inner
        self._logger = logger or logging.getLogger(__name__)
        self._level = level

    @property
    def effect_type(self) -> type[Effect]:
        return self._inner.effect_type

    async def handle(
        self,
        effect: Effect,
        context: HandlerContext,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        effect_type = effect.effect_type
        scope_id = context.scope_id

        self._logger.log(self._level, f"[{scope_id[:8]}] Handling {effect_type}")

        start = time.perf_counter()
        try:
            result = await self._inner.handle(effect, context, resume)
            duration = time.perf_counter() - start
            self._logger.log(self._level, f"[{scope_id[:8]}] Handled {effect_type} in {duration:.3f}s")
            return result
        except Exception as exc:
            duration = time.perf_counter() - start
            self._logger.exception(f"[{scope_id[:8]}] Failed {effect_type} after {duration:.3f}s: {exc}")
            raise

    def can_handle(self, effect: Effect) -> bool:
        return self._inner.can_handle(effect)


__all__ = ["LoggingHandler"]
