"""Prompt building utilities for runtime-owned step authoring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .metadata import StepMetadata


def build_step_prompt(
    metadata: StepMetadata,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """Build a prompt from step metadata and bound arguments."""
    sections = []

    if metadata.docstring:
        sections.append(metadata.docstring.strip())

    param_names = list(metadata.parameters.keys())
    bound_params = {}
    for i, arg in enumerate(args):
        if i < len(param_names):
            bound_params[param_names[i]] = arg
    bound_params.update(kwargs)

    if bound_params:
        param_lines = ["## Inputs"]
        for name, value in bound_params.items():
            str_val = str(value)
            if len(str_val) > 500:
                str_val = str_val[:500] + "..."
            param_lines.append(f"- **{name}**: {str_val}")
        sections.append("\n".join(param_lines))

    return "\n\n".join(sections)


def summarize_inputs(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    param_names: dict[str, type],
) -> dict[str, str]:
    """Create a summary of inputs for effect emission."""
    result = {}
    names = list(param_names.keys())
    for i, arg in enumerate(args):
        if i < len(names):
            val_str = str(arg)
            result[names[i]] = val_str[:100] + "..." if len(val_str) > 100 else val_str
    for k, v in kwargs.items():
        val_str = str(v)
        result[k] = val_str[:100] + "..." if len(val_str) > 100 else val_str
    return result


__all__ = ["build_step_prompt", "summarize_inputs"]
