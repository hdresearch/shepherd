"""@task decorator for declarative task definition.

The @task decorator marks a Pydantic BaseModel subclass as an executable task.
Tasks define their inputs, outputs, contexts, and artifacts using marker types,
then execute via the three-layer architecture (Scope → ExecutionLifecycle → Provider).

Usage:
    from pydantic import BaseModel, Field
    from shepherd_runtime.task.authoring import Context, Input, Output, task
    from shepherd_runtime.scope import Scope
    from shepherd_contexts.workspace import WorkspaceRef

    @task
    class FixBug(BaseModel):
        '''Fix a bug in the codebase.'''
        bug_description: Input(str)
        workspace: Context(WorkspaceRef)
        fix_summary: Output(str)

    # Execute with explicit scope
    with Scope() as scope:
        scope.register_provider("default", provider, default=True)
        workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))

        result = await FixBug.run(bug_description="NPE on login")
        print(result.fix_summary)
        print(workspace.pending_patches)  # ContextRef auto-updates

    # For testing, use MockProvider
    from shepherd_tests import MockProvider
    with Scope() as scope:
        scope.register_provider("default", MockProvider(), default=True)
        result = await FixBug.run(bug_description="test")
        print(result.fix_summary)  # Mock value

Execution Flow:
    1. Resolve Context fields from scope (by name, then by type)
    2. Create ExecutionLifecycle with scope and provider
    3. Generate prompt from docstring + Input fields
    4. Execute via provider
    5. Extract outputs from structured response
    6. Collect artifacts from .artifacts/ directory
    7. Return task instance with all fields populated
"""

from __future__ import annotations

import inspect
import textwrap
from typing import (
    TYPE_CHECKING,
    Generic,
    TypeVar,
    overload,
)

from ._mixin import TaskMixin
from ._source_state import reconstruction_source
from .metadata import (
    FieldInfo,
    TaskMetadata,
    extract_task_metadata,
)

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

# =============================================================================
# Task Decorator
# =============================================================================


@overload
def task(cls: type[T], /) -> type[T]: ...


@overload
def task(
    *,
    guidance: str = "",
    cacheable: bool | None = None,
) -> Callable[[type[T]], type[T]]: ...


def task(  # noqa: D417 (positional-only `cls` is the decorated class, not a user arg)
    cls: type | None = None,
    /,
    *,
    guidance: str = "",
    cacheable: bool | None = None,
) -> type | Callable[[type], type]:
    """Declare a task class.

    The @task decorator marks a Pydantic BaseModel subclass as an executable task.
    Fields marked with Input() are task inputs, fields marked with Output() are
    populated during execution.

    Args:
        guidance: Additional guidance text appended to the system prompt.
        cacheable: Whether task results can be cached. Defaults to True for
            LLM tasks and False for programmatic tasks (those with execute()).
            Set explicitly to override the default.
    """
    if cls is not None:
        # Use actual parameter values, not hardcoded defaults (DRY principle)
        return _apply_task_decorator(cls, guidance=guidance, cacheable=cacheable)

    def decorator(cls: type) -> type:
        return _apply_task_decorator(cls, guidance=guidance, cacheable=cacheable)

    return decorator


def _has_task_mixin(cls: type) -> bool:
    """Check if cls already has TaskMixin in its inheritance hierarchy."""
    return any(TaskMixin in getattr(base, "__mro__", []) for base in cls.__mro__ if base is not cls)


def _apply_task_decorator(cls: type, guidance: str, cacheable: bool | None = None) -> type:
    """Apply @task decorator to a class using Mixin pattern.

    This uses standard Python inheritance instead of dynamic type creation.
    We create a subclass 'Taskified' that inherits from TaskMixin and the user's class.
    """
    # Extract metadata
    meta = extract_task_metadata(cls)
    meta.guidance = guidance

    # Determine cacheable default based on task type:
    # - LLM tasks (no execute()): default True (expensive LLM calls benefit from caching)
    # - Programmatic tasks (with execute()): default False (side-effectful, caching is unsafe)
    has_custom_execute = "execute" in cls.__dict__
    meta.cacheable = cacheable if cacheable is not None else (not has_custom_execute)

    # Store metadata on the class itself so the Mixin can access it
    # Note: We store it on the original class too, as it might be useful
    cls._task_meta = meta  # type: ignore[attr-defined]
    cls._task_guidance = guidance  # type: ignore[attr-defined]
    captured_source = reconstruction_source.get()
    if captured_source is not None:
        cls._task_source = captured_source  # type: ignore[attr-defined]
    else:
        try:
            captured_source = inspect.getsource(cls)
            if captured_source.startswith((" ", "\t")):
                captured_source = textwrap.dedent(captured_source)
            cls._task_source = captured_source  # type: ignore[attr-defined]
        except (OSError, TypeError):
            cls._task_source = None  # type: ignore[attr-defined]

    # Detect if we need to apply the Mixin
    if _has_task_mixin(cls):
        # Already has Mixin (nested inheritance case)
        # Just subclass to ensure any new behaviors are applied if needed,
        # or simpler: just return the class if we didn't need to wrap it?
        # But wait, 'cls' here IS the class being defined.
        # If it inherits from a Taskified class, then TaskMixin is in MRO.
        # Examples:
        # @task
        # class Child(Parent): ...
        # Parent is already Taskified.
        # So Child.__mro__ contains TaskMixin.

        # In this case, we simply wrap it in a standard class to attach new metadata/helpers?
        # Actually, if we re-apply TaskMixin, we get MRO conflict.
        # So we just define Taskified(cls).

        class Taskified(cls):  # type: ignore
            pass
    else:
        # Normal case: Apply Mixin
        # Check for Generics
        params = getattr(cls, "__parameters__", ())
        if params:
            # Re-apply Generic[params] to support __class_getitem__
            class Taskified(TaskMixin, cls, Generic[params]):  # type: ignore
                pass
        else:

            class Taskified(TaskMixin, cls):  # type: ignore
                pass

    # Import StepBuilder for inline step syntax
    from ..step.inline import StepBuilder

    Taskified.step = StepBuilder()

    # Metadata preservation
    Taskified.__name__ = cls.__name__
    Taskified.__doc__ = cls.__doc__
    Taskified.__module__ = cls.__module__
    Taskified.__qualname__ = cls.__qualname__

    # Ensure metadata is on the wrapper too
    Taskified._task_meta = meta
    Taskified._task_source = cls._task_source  # type: ignore[attr-defined]
    canary_spec = getattr(cls, "_kernel_v3_canary_spec", None)
    if canary_spec is not None:
        Taskified._kernel_v3_canary_spec = canary_spec

    return Taskified


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "FieldInfo",
    "TaskMetadata",
    "extract_task_metadata",
    "task",
]
