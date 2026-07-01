"""Runtime-owned step metadata extraction and data classes."""

from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_type_hints

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_STEP_TIMEOUT = 120.0


@dataclass
class StepInputInfo:
    """Information about a step input parameter."""

    type_annotation: type
    is_required: bool = True
    default: Any = None


@dataclass
class StepMetadata:
    """Metadata extracted from a @step decorated method."""

    name: str
    docstring: str = ""
    parameters: dict[str, type] = field(default_factory=dict)
    return_type: type | None = None
    timeout: float = DEFAULT_STEP_TIMEOUT
    mock_response: Any = None
    provider: str | None = None
    shepherd: bool = True
    mock: bool = False
    retries: int = 0
    retry_delay: float = 1.0
    _param_details: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def step_id(self) -> str:
        return f"step:{self.name}"

    @property
    def inputs(self) -> dict[str, StepInputInfo]:
        param_details = self._param_details
        result = {}
        for name, typ in self.parameters.items():
            details = param_details.get(name, {})
            result[name] = StepInputInfo(
                type_annotation=typ,
                is_required=details.get("is_required", True),
                default=details.get("default"),
            )
        return result


def _extract_step_metadata(
    func: Callable[..., Any],
    *,
    shepherd: bool = True,
    timeout: float = DEFAULT_STEP_TIMEOUT,
) -> StepMetadata:
    """Extract metadata from a step function."""
    sig = inspect.signature(func)
    hints = get_type_hints(func)

    if not func.__doc__:
        warnings.warn(
            f"Step '{func.__name__}' has no docstring. The docstring is used as the LLM prompt.",
            stacklevel=3,
        )

    params = {}
    param_details = {}

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        param_type = hints.get(name, Any)
        params[name] = param_type

        has_default = param.default is not inspect.Parameter.empty
        param_details[name] = {
            "is_required": not has_default,
            "default": param.default if has_default else None,
        }

    return StepMetadata(
        name=func.__name__,
        docstring=func.__doc__ or "",
        parameters=params,
        return_type=hints.get("return"),
        shepherd=shepherd,
        timeout=timeout,
        _param_details=param_details,
    )


def extract_step_metadata(
    func: Callable[..., Any],
    *,
    shepherd: bool = True,
    timeout: float = DEFAULT_STEP_TIMEOUT,
) -> StepMetadata:
    """Public runtime-facing helper for step metadata extraction."""
    return _extract_step_metadata(func, shepherd=shepherd, timeout=timeout)


__all__ = [
    "DEFAULT_STEP_TIMEOUT",
    "StepInputInfo",
    "StepMetadata",
    "_extract_step_metadata",
    "extract_step_metadata",
]
