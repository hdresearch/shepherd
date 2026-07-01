"""Inline step syntax for ad-hoc LLM calls within tasks.

This module provides the self.step[T](...) syntax for inline steps,
allowing ad-hoc LLM calls without defining a full @step decorated method.

Usage:
    result = self.step[Literal["yes", "no"]]("Is {val} good?", val="test")
"""

from __future__ import annotations

import threading
from typing import (
    Any,
    Generic,
    TypeVar,
)

from .metadata import DEFAULT_STEP_TIMEOUT

T = TypeVar("T")

# Atomic counter for inline step IDs
_inline_step_counter = 0
_inline_step_lock = threading.Lock()


def _next_inline_id() -> int:
    """Get the next unique inline step ID."""
    global _inline_step_counter
    with _inline_step_lock:
        _inline_step_counter += 1
        return _inline_step_counter


# =============================================================================
# Inline Step Classes
# =============================================================================


class StepBuilder:
    """Descriptor enabling self.step[T](...) inline step syntax.

    When accessed on an instance, returns a BoundStepBuilder that
    supports type parameterization via __getitem__.

    Usage:
        result = self.step[Literal["yes", "no"]]("Is {val} good?", val="test")
    """

    def __get__(self, instance: Any, owner: type) -> BoundStepBuilder | StepBuilder:
        if instance is None:
            return self
        return BoundStepBuilder(instance)


class BoundStepBuilder:
    """Bound step builder for a specific task instance."""

    def __init__(self, task_instance: Any) -> None:
        self._task = task_instance

    def __getitem__(self, return_type: type) -> InlineStep[Any]:
        """Get an inline step executor for the given return type."""
        return InlineStep(self._task, return_type)


class InlineStep(Generic[T]):
    """Executes an inline step with a specific return type.

    Created via self.step[ReturnType], then called with a prompt template
    and keyword arguments for template substitution.
    """

    def __init__(self, task_instance: Any, return_type: type) -> None:
        self._task = task_instance
        self._return_type = return_type

    def __call__(
        self,
        prompt_template: str,
        *,
        timeout: float = DEFAULT_STEP_TIMEOUT,
        provider: str | None = None,
        **template_params: Any,
    ) -> Any:
        """Execute the inline step.

        Args:
            prompt_template: A format string for the prompt.
            timeout: Maximum execution time in seconds.
            provider: Optional provider name override.
            **template_params: Values to substitute into the prompt template.

        Returns:
            The parsed result coerced to the return type.
        """
        # Get scope and task info from Pydantic private attributes
        # (Direct __pydantic_private__ access required because TaskMixin's
        # __getattr__ intercepts normal attribute access)
        private = getattr(self._task, "__pydantic_private__", None) or {}
        scope = private.get("_task_scope")

        # Generate unique step name
        step_name = f"inline_step_{_next_inline_id()}"

        # Real execution requires a scope
        if scope is None:
            raise RuntimeError("Inline step requires a task scope. Ensure the parent task was executed properly.")

        # Format prompt with template params
        prompt = prompt_template.format(**template_params)

        # Check if calling execute() is async — return coroutine for await
        from ..task._mixin import _is_async_execute

        if _is_async_execute():
            from .decorator import _execute_step_async_entry

            return _execute_step_async_entry(
                task=self._task,
                scope=scope,
                step_name=step_name,
                prompt=prompt,
                return_type=self._return_type,
                timeout=timeout,
                provider_name=provider,
                shepherd=True,
            )

        # Sync path (runtime import to avoid circular dependency)
        from .decorator import _execute_step_sync

        return _execute_step_sync(
            task=self._task,
            scope=scope,
            step_name=step_name,
            prompt=prompt,
            return_type=self._return_type,
            timeout=timeout,
            provider_name=provider,
            shepherd=True,  # Inline steps default to shepherd
        )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "BoundStepBuilder",
    "InlineStep",
    "StepBuilder",
    "_next_inline_id",
]
