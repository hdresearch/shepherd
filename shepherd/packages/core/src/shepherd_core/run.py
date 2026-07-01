"""Generic run() / run_sync() convenience functions.

Replaces per-package entrypoint glue by creating a scope, registering
the caller-provided provider, and delegating to ``task_class.arun()``.

Usage::

    from shepherd_core import run, run_sync
    from shepherd_providers.claude import ClaudeProvider

    provider = ClaudeProvider(name="review", model="claude-sonnet-4-5")
    result = run_sync(MyTask, provider=provider, some_input="value")
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shepherd_runtime.scope import Scope  # type: ignore[import-not-found,unused-ignore]

    from shepherd_core.provider import Provider


async def run(
    task_class: type[Any],
    *,
    provider: Provider,
    scope: Scope | None = None,
    **inputs: Any,
) -> Any:
    """Run a task with automatic scope/provider setup.

    If *scope* is provided, delegates directly to ``task_class.arun()``
    (the provider argument is ignored in this case).

    If no scope is provided, creates a root scope, registers the given
    provider, and runs the task inside it.

    Args:
        task_class: The ``@task``-decorated class to run.
        provider: A ``Provider`` instance to register as the default.
        scope: An existing scope (skips auto-scope creation).
        **inputs: Keyword arguments forwarded to the task constructor.
            Pass ``config=`` here if the task accepts configuration.

    Returns:
        The task instance after execution.
    """
    from shepherd_runtime.scope import Scope as _Scope

    if scope is not None:
        return await task_class.arun(scope=scope, **inputs)

    async with _Scope(root=True) as auto_scope:
        auto_scope.register_provider("default", provider, default=True)
        return await task_class.arun(scope=auto_scope, **inputs)


def run_sync(
    task_class: type[Any],
    *,
    provider: Provider,
    scope: Scope | None = None,
    **inputs: Any,
) -> Any:
    """Synchronous wrapper around :func:`run`.

    Creates a new event loop via ``asyncio.run()``. Do not call this from
    within an already-running event loop.
    """
    return asyncio.run(run(task_class, provider=provider, scope=scope, **inputs))


__all__ = [
    "run",
    "run_sync",
]
