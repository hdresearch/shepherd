"""Public runtime lifecycle entrypoints."""

from __future__ import annotations

from ._lifecycle_impl import ExecutionLifecycle as _RuntimeExecutionLifecycle
from ._lifecycle_impl import execute as _runtime_execute
from .sandbox_registry import (
    SandboxFactory,
    SandboxRegistry,
    get_default_registry,
    register_sandbox_factory,
)

ExecutionLifecycle = _RuntimeExecutionLifecycle
execute = _runtime_execute

ExecutionLifecycle.__module__ = __name__
execute.__module__ = __name__


__all__ = [
    "ExecutionLifecycle",
    "SandboxFactory",
    "SandboxRegistry",
    "execute",
    "get_default_registry",
    "register_sandbox_factory",
]
