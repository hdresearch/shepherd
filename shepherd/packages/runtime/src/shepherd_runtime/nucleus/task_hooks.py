"""Task execution hooks for optional runtime integrations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar, Token
from typing import Any

TaskExecutionHook = Callable[[Any, Any], Any]

_task_execution_hooks: ContextVar[tuple[TaskExecutionHook, ...]] = ContextVar(
    "shepherd_nucleus_task_execution_hooks",
    default=(),
)


@contextmanager
def install_task_execution_hook(hook: TaskExecutionHook) -> Iterator[None]:
    """Install a task-body hook for the current dynamic extent."""
    token: Token[tuple[TaskExecutionHook, ...]] = _task_execution_hooks.set((*_task_execution_hooks.get(), hook))
    try:
        yield
    finally:
        _task_execution_hooks.reset(token)


@contextmanager
def enter_task_execution_hooks(metadata: object, context: object) -> Iterator[None]:
    """Enter installed task-body hooks in registration order."""
    hooks = _task_execution_hooks.get()
    if not hooks:
        yield
        return
    with ExitStack() as stack:
        for hook in hooks:
            manager = hook(metadata, context)
            if manager is not None:
                stack.enter_context(manager)
        yield


__all__ = [
    "TaskExecutionHook",
    "enter_task_execution_hooks",
    "install_task_execution_hook",
]
