"""Context-aware public effect facade for the callable-spine package."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from shepherd_runtime.effects import Ask, Tell, sync_ask, sync_tell
from shepherd_runtime.effects import ask as async_ask
from shepherd_runtime.effects import tell as async_tell
from shepherd_runtime.nucleus.delivery import active_task_run

if TYPE_CHECKING:
    from collections.abc import Awaitable

R = TypeVar("R")


def ask(effect: Ask[R]) -> R | Awaitable[R]:
    """Perform an ``Ask`` through the public sync/async facade.

    Async task bodies receive the owner-path coroutine and should use
    ``await ask(...)``. Sync task bodies and non-task first-run code block via
    the explicit sync bridge.
    """
    context = active_task_run()
    if context is not None and context.is_async:
        return async_ask(effect)
    return sync_ask(effect)


def tell(effect: Tell) -> None | Awaitable[None]:
    """Perform a ``Tell`` through the public sync/async facade."""
    context = active_task_run()
    if context is not None and context.is_async:
        return async_tell(effect)
    return sync_tell(effect)


__all__ = ["ask", "tell"]
