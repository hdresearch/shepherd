"""Owner-path adapters for class-form tasks and combinator callables.

These helpers support migration of the legacy class-form task/combinator layer.
They are not part of the top-level callable-spine facade, and new first-run
examples should use function-form ``@task`` directly.

This module provides:
- task_fn(): Convert a @task class to a combinator-compatible async callable
- TaskAdapter: The underlying adapter class

Why This Exists
---------------
@task classes provide a nice declarative API for defining tasks:

    @task
    class TellJoke(BaseModel):
        topic: Input(str)
        joke: Output(str)

    # Execute by instantiation
    joke = TellJoke(topic="cats")

But combinators like retry(), gate(), and Pipeline expect async callables:

    async def tell_joke(inputs: dict, scope: Scope) -> TellJoke:
        ...

task_fn() bridges this gap, allowing @task classes to be used with combinators:

    from shepherd.pipeline import Pipeline
    from shepherd.adapters import task_fn

    # Option 1: Direct use with combinators
    from shepherd_runtime.combinators import retry
    retrying_joke = retry(task_fn(TellJoke), max_attempts=3)

    # Option 2: Use with the owner-path Pipeline helper
    result = Pipeline(TellJoke).retry(3).run(topic="cats")

task_fn() is exposed for power users who want to compose tasks functionally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from shepherd_runtime.scope import Scope

T = TypeVar("T")  # The task class type


class TaskAdapter(Generic[T]):
    """Adapter wrapping a @task class as a combinator-compatible callable.

    TaskAdapter implements the combinator contract:
        async def __call__(inputs: dict, scope: Scope) -> T

    This allows @task classes to be passed to retry(), gate(), parallel(), etc.

    Attributes:
        task_class: The underlying @task class
        __name__: Preserved from task class for debugging
        __qualname__: Preserved from task class for debugging

    Example:
        adapter = TaskAdapter(TellJoke)
        result = await adapter({"topic": "cats"}, scope)
        # result is a TellJoke instance
    """

    def __init__(self, task_class: type[T]) -> None:
        """Initialize the adapter.

        Args:
            task_class: A @task-decorated class
        """
        self.task_class = task_class
        # Preserve identity for debugging
        self.__name__ = getattr(task_class, "__name__", "UnnamedTask")
        self.__qualname__ = getattr(task_class, "__qualname__", self.__name__)

    async def __call__(self, inputs: dict[str, Any], scope: Scope) -> T:
        """Execute the task with the given inputs and scope.

        Args:
            inputs: Dictionary of input field values
            scope: The scope to execute within

        Returns:
            The executed task instance (with Output fields populated)
        """
        # Use arun() with explicit scope
        return await self.task_class.arun(scope=scope, **inputs)

    def with_kwargs(self, scope: Scope, **kwargs: Any) -> TaskAdapter[T]:
        """Create a partial adapter with pre-bound kwargs.

        This is a convenience for when you want to bind some inputs
        before passing to a combinator.

        Args:
            scope: The scope to use
            **kwargs: Keyword arguments to pre-bind

        Returns:
            A new adapter with the kwargs pre-bound

        Example:
            adapter = TaskAdapter(TellJoke).with_kwargs(scope, topic="cats")
            result = await adapter({}, scope)  # topic already bound
        """
        return _PartialTaskAdapter(self.task_class, scope, kwargs)

    def __repr__(self) -> str:
        return f"TaskAdapter({self.__name__})"


class _PartialTaskAdapter(Generic[T]):
    """Partial adapter with pre-bound kwargs."""

    def __init__(
        self,
        task_class: type[T],
        scope: Scope,
        bound_kwargs: dict[str, Any],
    ) -> None:
        self.task_class = task_class
        self._scope = scope
        self._bound_kwargs = bound_kwargs
        self.__name__ = getattr(task_class, "__name__", "UnnamedTask")
        self.__qualname__ = getattr(task_class, "__qualname__", self.__name__)

    async def __call__(self, inputs: dict[str, Any], scope: Scope) -> T:
        """Execute with merged inputs (bound kwargs + call-time inputs)."""
        merged = {**self._bound_kwargs, **inputs}
        return await self.task_class.arun(scope=scope, **merged)

    def __repr__(self) -> str:
        return f"TaskAdapter({self.__name__}, bound={list(self._bound_kwargs.keys())})"


def task_fn(task_class: type[T]) -> TaskAdapter[T]:
    """Convert a @task class to a combinator-compatible async callable.

    This is the bridge between the legacy class-form @task API and the
    functional combinator API. Use it when maintaining owner-path workflow
    code that needs the ``(inputs, scope)`` callable signature.

    Args:
        task_class: A @task-decorated class

    Returns:
        TaskAdapter that can be passed to retry(), gate(), parallel(), etc.

    Example:
        from shepherd.adapters import task_fn
        from shepherd_runtime.combinators import retry, gate

        # Wrap for combinator use
        tell_joke = task_fn(TellJoke)

        # Use with combinators
        retrying = retry(tell_joke, max_attempts=3)
        gated = gate(retrying, lambda r: len(r.joke) > 10)

        # Execute
        result = await gated({"topic": "cats"}, scope)

    See Also:
        - Pipeline: Owner-path workflow helper for class-form task migration
        - design/syntax-api/PROPOSAL-syntax-evolution.md: Design rationale
    """
    return TaskAdapter(task_class)


__all__ = [
    "TaskAdapter",
    "task_fn",
]
