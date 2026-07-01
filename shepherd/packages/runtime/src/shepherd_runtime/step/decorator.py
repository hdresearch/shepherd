"""@step decorator for LLM-powered methods within composite tasks.

The @step decorator turns a method into an LLM-powered operation. The method's
docstring becomes the prompt, parameters become inputs, and the return type
annotation defines the expected output schema.

Scope behavior (tasks-as-scopes):
    Steps do NOT create their own child scopes. They execute within the parent
    task's scope, and their effects (StepStarted, StepCompleted) are emitted
    directly to that scope. This differs from @task, where each task creates
    its own child scope for effect isolation.

    This means:
    - task._task_scope.effects contains all step effects from that task
    - Steps share the task's scope for provider access and context resolution
    - Nested steps within a task all emit to the same scope

Usage:
    @task
    class AnalyzeBug(BaseModel):
        bug: Input(str)
        analysis: Output(str)

        @step
        def classify(self, description: str) -> Literal["logic", "null", "type"]:
            '''Classify the type of bug.'''
            ...  # Body ignored, LLM executes based on docstring

        def execute(self):
            bug_type = self.classify(self.bug)
            self.analysis = f"This is a {bug_type} bug"

Testing:
    Use MockProvider for fast execution without real LLM calls:

        with Scope() as scope:
            scope.register_provider("default", MockProvider(), default=True)
            result = AnalyzeBug(bug="NPE")
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from typing import TYPE_CHECKING, Any, TypeVar, overload

from shepherd_core.errors import StepExecutionError, StepOutputError
from shepherd_core.provider import DefaultProviderRuntime
from shepherd_core.types import ProviderBinding

from ..sync import run_sync
from .inline import (
    BoundStepBuilder,
    InlineStep,
    StepBuilder,
)
from .metadata import (
    DEFAULT_STEP_TIMEOUT,
    StepInputInfo,
    StepMetadata,
)
from .metadata import (
    extract_step_metadata as _extract_step_metadata,
)
from .mock import (
    mock_execute_from_schema,
)
from .output import SINGLE_OUTPUT_KEY
from .output import return_type_to_output_schema as _return_type_to_output_schema
from .parsing import (
    coerce_step_value,
    coerce_to_bool,
    coerce_to_enum,
    coerce_to_list,
    parse_single_output,
    parse_step_output,
    parse_tuple_output,
)
from .prompt import build_step_prompt as _build_step_prompt
from .prompt import summarize_inputs as _summarize_inputs

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shepherd_core.provider import Provider

    from shepherd_runtime.scope import Scope

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _coerce_step_value(value: Any, expected_type: Any, step_name: str = "step", field_name: str = "value") -> Any:
    return coerce_step_value(value, expected_type, step_name, field_name)


def _coerce_to_bool(value: Any) -> bool:
    return coerce_to_bool(value)


def _coerce_to_enum(value: Any, enum_type: type, step_name: str) -> Any:
    return coerce_to_enum(value, enum_type, step_name)


def _coerce_to_list(value: Any, list_args: tuple[Any, ...], step_name: str) -> list[Any]:
    return coerce_to_list(value, list_args, step_name)


def _parse_single_output(result: dict[str, Any], return_type: type, step_name: str) -> Any:
    return parse_single_output(result, return_type, step_name)


def _parse_step_output(result: dict[str, Any], return_type: type | None, step_name: str = "step") -> Any:
    return parse_step_output(result, return_type, step_name)


def _parse_tuple_output(result: dict[str, Any], tuple_args: tuple[Any, ...], step_name: str) -> tuple[Any, ...]:
    return parse_tuple_output(result, tuple_args, step_name)


def _mock_execute_from_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    return mock_execute_from_schema(output_schema)


def _generate_mock_value(return_type: type | None) -> Any:
    output_schema = _return_type_to_output_schema(return_type)
    result = _mock_execute_from_schema(output_schema)
    if result and SINGLE_OUTPUT_KEY in result and return_type is not None:
        return _coerce_step_value(result[SINGLE_OUTPUT_KEY], return_type, "step", SINGLE_OUTPUT_KEY)
    return _parse_step_output(result, return_type, "step")


# =============================================================================
# Step Execution
# =============================================================================


async def _execute_with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    retries: int,
    retry_delay: float,
    step_name: str,
) -> T:
    """Execute a coroutine with retry logic.

    Args:
        coro_factory: Callable that creates a new coroutine for each attempt
        retries: Number of retry attempts (0 = no retries)
        retry_delay: Base delay between retries (exponential backoff)
        step_name: Step name for error messages

    Returns:
        Result from successful execution

    Raises:
        Last exception if all retries exhausted
    """
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt < retries:
                # Exponential backoff: delay * 2^attempt
                wait_time = retry_delay * (2**attempt)
                await asyncio.sleep(wait_time)
            # Continue to next attempt

    # All retries exhausted
    raise last_error  # type: ignore


async def _execute_step_async(
    provider: Provider,
    prompt: str,
    output_schema: dict[str, Any] | None,
    task_name: str,
    timeout: float,
    shepherd: bool = True,
    scope: Scope | None = None,
) -> dict[str, Any]:
    """Execute a step via the provider asynchronously.

    Args:
        provider: The provider to execute with
        prompt: The formatted step prompt
        output_schema: JSON schema for structured output
        task_name: Task name for effect attribution (format: "ParentTask:step_name")
        timeout: Maximum execution time in seconds
        shepherd: Whether the step has tool access
        scope: The scope for effect emission (required for real execution)

    Returns:
        Dict with structured output (contains SINGLE_OUTPUT_KEY or output_N keys)
    """
    # Mock mode check (provider-level mock)
    if getattr(provider, "mock", False):
        # Return empty dict - caller handles mock value generation
        return {}

    # Real execution requires scope
    if scope is None:
        raise RuntimeError("Real step execution requires a scope")

    # Create binding with output schema
    binding = ProviderBinding(
        context_id=f"step:{task_name}",
        capabilities=frozenset({"read", "write", "bash"}) if shepherd else frozenset(),
        output_format=output_schema,
    )

    # Execute via provider
    result = await provider.execute_sdk(
        prompt=prompt,
        binding=binding,
        runtime=DefaultProviderRuntime.from_emitter(scope, task_name=task_name),
    )

    # Extract structured output
    if result.structured_output:
        return result.structured_output

    # Fallback: try to parse output_text as JSON
    if result.output_text:
        try:
            parsed = json.loads(result.output_text)
            if isinstance(parsed, dict):
                return parsed
            return {SINGLE_OUTPUT_KEY: parsed}
        except json.JSONDecodeError:
            # Return as single output key
            return {SINGLE_OUTPUT_KEY: result.output_text}

    return {}


def _execute_step_sync(
    task: Any,
    scope: Scope,
    step_name: str,
    prompt: str,
    return_type: type | None,
    timeout: float,
    provider_name: str | None,
    shepherd: bool = True,
    metadata: StepMetadata | None = None,
) -> Any:
    """Execute a step synchronously — bridges to the async implementation via run_sync."""
    retries = metadata.retries if metadata else 0
    effective_timeout = timeout * (retries + 1)
    return run_sync(
        _execute_step_async_entry(
            task=task,
            scope=scope,
            step_name=step_name,
            prompt=prompt,
            return_type=return_type,
            timeout=timeout,
            provider_name=provider_name,
            shepherd=shepherd,
            metadata=metadata,
        ),
        timeout=effective_timeout,
    )


async def _execute_step_async_entry(
    task: Any,
    scope: Scope,
    step_name: str,
    prompt: str,
    return_type: type | None,
    timeout: float,
    provider_name: str | None,
    shepherd: bool = True,
    metadata: StepMetadata | None = None,
) -> Any:
    """Execute a step asynchronously — no thread bridge."""
    from ._execution import StepEffectEmitter

    private = getattr(task, "__pydantic_private__", None) or {}
    parent_task = private.get("_task_name", "UnknownTask")
    emitter = StepEffectEmitter(scope, step_name, parent_task)

    provider = scope.get_provider(provider_name) if provider_name else scope.get_provider()
    emitter.started({"prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt})
    start = time.time()

    try:
        output_schema = _return_type_to_output_schema(return_type)

        if getattr(provider, "mock", False):
            parsed = _generate_mock_value(return_type)
        else:
            retries = metadata.retries if metadata else 0
            retry_delay = metadata.retry_delay if metadata else 1.0

            def make_coro() -> Awaitable[dict[str, Any]]:
                return _execute_step_async(
                    provider=provider,
                    prompt=prompt,
                    output_schema=output_schema,
                    task_name=f"{parent_task}:{step_name}",
                    timeout=timeout,
                    shepherd=shepherd,
                    scope=scope,
                )

            result = await _execute_with_retry(make_coro, retries, retry_delay, step_name)

            if result and SINGLE_OUTPUT_KEY in result and return_type is not None:
                parsed = _coerce_step_value(result[SINGLE_OUTPUT_KEY], return_type, step_name, SINGLE_OUTPUT_KEY)
            else:
                parsed = _parse_step_output(result, return_type, step_name)

        duration_ms = (time.time() - start) * 1000
        emitter.completed(parsed, duration_ms)
        return parsed

    except Exception as e:
        emitter.failed(e)
        raise StepExecutionError(
            step_name=step_name,
            parent_task=parent_task,
            cause=e,
        ) from e


# =============================================================================
# Step Decorator
# =============================================================================


@overload
def step(func: Callable[..., T], /) -> Callable[..., T]: ...


@overload
def step(
    *,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    provider: str | None = None,
    shepherd: bool = True,
    retries: int = 0,
    retry_delay: float = 1.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]: ...


def step(  # noqa: D417 (positional-only `func` is the decorated callable, not a user arg)
    func: Callable[..., Any] | None = None,
    /,
    *,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    provider: str | None = None,
    shepherd: bool = True,
    retries: int = 0,
    retry_delay: float = 1.0,
) -> Callable[..., Any] | Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare an LLM-powered step within a task.

    The @step decorator turns a method into an LLM-powered operation.
    The method's docstring becomes the prompt, and the return type
    annotation defines the expected output.

    Args:
        timeout: Maximum time for step execution (seconds)
        provider: Override provider for this step
        shepherd: Whether the step has tool access (default True)
        retries: Number of retry attempts on failure (default 0 = no retries)
        retry_delay: Base delay between retries in seconds (exponential backoff)

    Usage:
        @step
        def classify(self, text: str) -> Literal["a", "b"]:
            '''Classify the text.'''
            ...

        @step(timeout=60)
        def classify(self, text: str) -> str:
            '''Classify with custom timeout.'''
            ...

        @step(shepherd=False)
        def pure_reasoning(self, text: str) -> str:
            '''Pure reasoning step without tool access.'''
            ...

        @step(retries=3, retry_delay=0.5)
        def might_fail(self, text: str) -> str:
            '''Step with automatic retries.'''
            ...
    """
    if func is not None:
        return _apply_step_decorator(func, timeout, provider, shepherd, retries, retry_delay)

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        return _apply_step_decorator(func, timeout, provider, shepherd, retries, retry_delay)

    return decorator


def _apply_step_decorator(
    func: Callable[..., Any],
    timeout: float,
    provider: str | None,
    shepherd: bool,
    retries: int = 0,
    retry_delay: float = 1.0,
) -> Callable[..., Any]:
    """Apply @step decorator to a function."""
    metadata = _extract_step_metadata(func)
    metadata.timeout = timeout
    metadata.provider = provider
    metadata.shepherd = shepherd
    metadata.retries = retries
    metadata.retry_delay = retry_delay

    @functools.wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        # Get scope from task's Pydantic private attributes
        # (Direct __pydantic_private__ access required because TaskMixin's
        # __getattr__ intercepts normal attribute access)
        private = getattr(self, "__pydantic_private__", None) or {}
        scope = private.get("_task_scope")

        # Real execution requires a scope
        if scope is None:
            raise RuntimeError(
                f"Step {metadata.name} requires a task scope. Ensure the parent task was executed properly."
            )

        # Build prompt from docstring and parameters
        prompt = _build_step_prompt(metadata, args, kwargs)

        # Check if calling execute() is async — return coroutine for await
        from ..task._mixin import _is_async_execute

        if _is_async_execute():
            return _execute_step_async_entry(
                task=self,
                scope=scope,
                step_name=metadata.name,
                prompt=prompt,
                return_type=metadata.return_type,
                timeout=timeout,
                provider_name=provider,
                shepherd=shepherd,
                metadata=metadata,
            )

        # Sync path — unchanged
        return _execute_step_sync(
            task=self,
            scope=scope,
            step_name=metadata.name,
            prompt=prompt,
            return_type=metadata.return_type,
            timeout=timeout,
            provider_name=provider,
            shepherd=shepherd,
            metadata=metadata,
        )

    # Store metadata on wrapper
    wrapper._step_metadata = metadata  # type: ignore[attr-defined]
    return wrapper


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Constants
    "DEFAULT_STEP_TIMEOUT",
    "SINGLE_OUTPUT_KEY",
    "BoundStepBuilder",
    "InlineStep",
    # Inline step syntax
    "StepBuilder",
    "StepInputInfo",
    # Metadata
    "StepMetadata",
    # Errors
    "StepOutputError",
    "_build_step_prompt",
    "_coerce_step_value",
    "_coerce_to_bool",
    "_coerce_to_enum",
    "_coerce_to_list",
    "_execute_step_sync",
    # Internal functions (exported for testing)
    "_extract_step_metadata",
    "_generate_mock_value",
    "_mock_execute_from_schema",
    "_parse_single_output",
    "_parse_step_output",
    "_parse_tuple_output",
    "_return_type_to_output_schema",
    "_summarize_inputs",
    # Decorator
    "step",
]
