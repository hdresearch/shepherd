"""Public sync-over-async bridge."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable

T = TypeVar("T")

DEFAULT_SYNC_TIMEOUT = 300.0


def run_sync(coro: Awaitable[T], timeout: float = DEFAULT_SYNC_TIMEOUT) -> T:
    """Run a coroutine synchronously, handling nested event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        ctx = contextvars.copy_context()

        def run_with_context() -> T:
            return ctx.run(asyncio.run, coro)  # type: ignore[arg-type]

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(run_with_context)
            return future.result(timeout=timeout)

    return asyncio.run(coro)  # type: ignore[arg-type]


__all__ = ["DEFAULT_SYNC_TIMEOUT", "run_sync"]
