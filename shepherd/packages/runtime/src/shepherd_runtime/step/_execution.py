"""Shared execution utilities for steps."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from .mock import generate_mock_value

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator

    from shepherd_runtime.scope import Scope

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


async def run_in_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a sync function in a thread pool, preserving ContextVars."""
    if sys.version_info >= (3, 12):
        return await asyncio.to_thread(func, *args, **kwargs)

    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: ctx.run(func, *args, **kwargs),
    )


@dataclass
class StepEffectEmitter:
    """Helper for emitting step lifecycle effects consistently."""

    scope: Scope
    step_name: str
    parent_task: str

    def started(self, inputs: dict[str, str]) -> None:
        from shepherd_core.effects import StepStarted

        self.scope.emit(
            StepStarted(
                step_name=self.step_name,
                parent_task=self.parent_task,
                inputs=inputs,
            )
        )

    def completed(self, result: Any, duration_ms: float) -> None:
        from shepherd_core.effects import StepCompleted

        self.scope.emit(
            StepCompleted(
                step_name=self.step_name,
                parent_task=self.parent_task,
                outputs=result,
                duration_ms=duration_ms,
            )
        )

    def failed(self, error: Exception) -> None:
        from shepherd_core.effects import StepFailed

        self.scope.emit(
            StepFailed(
                step_name=self.step_name,
                parent_task=self.parent_task,
                error=str(error),
                error_type=type(error).__name__,
            )
        )

    @contextmanager
    def track(self, inputs: dict[str, str]) -> Generator[_TrackingContext, None, None]:
        self.started(inputs)
        start_time = time.time()
        ctx = _TrackingContext()

        try:
            yield ctx
            duration_ms = (time.time() - start_time) * 1000
            self.completed(ctx.result, duration_ms)
        except Exception as e:
            self.failed(e)
            raise


@dataclass
class _TrackingContext:
    result: Any = None


def resolve_mock_value(
    explicit_mock: Any | None,
    return_type: type | None,
    field_name: str = "",
) -> Any:
    """Get mock value from explicit response or generate from type."""
    if explicit_mock is not None:
        return explicit_mock

    return generate_mock_value(return_type, field_name)


def summarize_for_effect(value: Any, max_length: int = 100) -> str:
    """Summarize a value for inclusion in an effect."""
    str_val = str(value)
    if len(str_val) > max_length:
        return str_val[:max_length] + "..."
    return str_val


__all__ = [
    "DEFAULT_SYNC_TIMEOUT",
    "StepEffectEmitter",
    "resolve_mock_value",
    "run_in_thread",
    "run_sync",
    "summarize_for_effect",
]
