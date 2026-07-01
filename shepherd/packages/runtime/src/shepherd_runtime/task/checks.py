"""Runtime check evaluation for declarative Check markers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .metadata import TaskMetadata


def run_input_checks(instance: Any, meta: TaskMetadata) -> None:
    """Run precondition checks on input fields. Raises on first failure."""
    from shepherd_core.errors import CheckFailedError

    for name, checks in meta.input_checks.items():
        value = getattr(instance, name)
        for check in checks:
            if not check(value):
                raise CheckFailedError(
                    task_name=meta.name,
                    field_name=name,
                    value=value,
                    check=check,
                    phase="precondition",
                )


def run_output_checks(instance: Any, meta: TaskMetadata) -> None:
    """Run postcondition checks on output fields. Raises on first failure."""
    from shepherd_core.errors import CheckFailedError

    for name, checks in meta.output_checks.items():
        value = getattr(instance, name)
        for check in checks:
            if not check(value):
                raise CheckFailedError(
                    task_name=meta.name,
                    field_name=name,
                    value=value,
                    check=check,
                    phase="postcondition",
                )
