"""Public runtime step parsing helpers."""

from __future__ import annotations

from typing import Any

from shepherd_core.output import (
    coerce_output_value as _coerce_output_value,
)
from shepherd_core.output import (
    coerce_to_bool as _coerce_to_bool,
)
from shepherd_core.output import (
    coerce_to_enum as _coerce_to_enum,
)
from shepherd_core.output import (
    coerce_to_list as _coerce_to_list,
)
from shepherd_core.output import (
    parse_single_output as _parse_single_output,
)
from shepherd_core.output import (
    parse_step_output as _parse_step_output,
)
from shepherd_core.output import (
    parse_tuple_output as _parse_tuple_output,
)


def coerce_step_value(value: Any, expected_type: Any, step_name: str, field_name: str) -> Any:
    """Coerce a raw step value into the expected type."""
    return _coerce_output_value(value, expected_type, step_name, field_name)


def coerce_to_bool(value: Any) -> bool:
    """Coerce a raw value to bool."""
    return _coerce_to_bool(value)


def coerce_to_enum(value: Any, enum_type: type, step_name: str) -> Any:
    """Coerce a raw value to an enum member."""
    return _coerce_to_enum(value, enum_type, step_name)


def coerce_to_list(value: Any, list_args: tuple[Any, ...], step_name: str) -> list[Any]:
    """Coerce a raw value to a list."""
    return _coerce_to_list(value, list_args, step_name)


def parse_single_output(result: dict[str, Any], return_type: type, step_name: str) -> Any:
    """Parse the single-output shape returned by a step provider."""
    return _parse_single_output(result, return_type, step_name)


def parse_step_output(result: dict[str, Any], return_type: type | None, step_name: str) -> Any:
    """Parse step output into the declared return type."""
    return _parse_step_output(result, return_type, step_name)


def parse_tuple_output(result: dict[str, Any], tuple_args: tuple[Any, ...], step_name: str) -> tuple[Any, ...]:
    """Parse tuple-typed step output."""
    return _parse_tuple_output(result, tuple_args, step_name)


__all__ = [
    "coerce_step_value",
    "coerce_to_bool",
    "coerce_to_enum",
    "coerce_to_list",
    "parse_single_output",
    "parse_step_output",
    "parse_tuple_output",
]
