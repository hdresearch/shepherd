"""Runtime-owned field markers for task definition."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, TypeVar

from pydantic import Field as PydanticField

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")


class _InferMarker:
    """Metadata marker indicating a field can be inferred from context."""

    def __call__(self, typ: type[T]) -> type[T]:
        """Wrap a type as inferable: ``Infer(str)`` -> ``Annotated[str, _InferMarker()]``."""
        return Annotated[typ, _InferMarker()]  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "Infer"


Infer = _InferMarker()


@dataclass(frozen=True, kw_only=True)
class InputMarker:
    """Marker indicating a field is a task input.

    The nucleus stores this marker opaquely on `Annotated[T, InputMarker(...)]`
    parameters so meta-tasks, autoconfig, and prompt construction can read
    the per-input metadata. The nucleus does not interpret `description` or
    `infer` semantics; downstream consumers do.

    See ``docs/design/proposed/260505-plans/DECISIONS.md`` D1.
    """

    description: str = ""
    infer: bool = False


@dataclass(frozen=True)
class OutputMarker:
    """Marker indicating a field is a task output."""


@dataclass(frozen=True)
class ContextMarker:
    """Marker indicating a field is an execution context."""

    inner_type: type[Any]


class Input:
    """Mark a type as a task input.

    Supports both call syntax and bracket syntax::

        class MyTask(BaseModel):
            query: Input[str]
            query: Input(str)
    """

    def __class_getitem__(cls, typ: type[T]) -> type[T]:
        """Bracket syntax: ``Input[str]``."""
        return Annotated[typ, InputMarker()]  # type: ignore[return-value]

    def __new__(cls, typ: type[T], *, infer: bool = False) -> type[T]:  # type: ignore[misc]
        """Call syntax: ``Input(str)``."""
        return Annotated[typ, InputMarker(infer=infer)]  # type: ignore[return-value]


class Output:
    """Mark a type as a task output.

    Supports both call syntax and bracket syntax::

        class MyTask(BaseModel):
            result: Output[str]
            result: Output(str)
    """

    def __class_getitem__(cls, typ: type[T]) -> type[T | None]:
        """Bracket syntax: ``Output[str]``."""
        return Annotated[typ | None, OutputMarker(), PydanticField(default=None)]  # type: ignore[return-value]

    def __new__(cls, typ: type[T]) -> type[T | None]:  # type: ignore[misc]
        """Call syntax: ``Output(str)``."""
        return Annotated[typ | None, OutputMarker(), PydanticField(default=None)]  # type: ignore[return-value]


class Context:
    """Mark a type as an execution context.

    Supports both call syntax and bracket syntax::

        class MyTask(BaseModel):
            workspace: Context[WorkspaceRef]
            workspace: Context(WorkspaceRef)
    """

    def __class_getitem__(cls, typ: type[T]) -> type[T | None]:
        """Bracket syntax: ``Context[WorkspaceRef]``."""
        return Annotated[typ | None, ContextMarker(inner_type=typ), PydanticField(default=None)]  # type: ignore[return-value]

    def __new__(cls, typ: type[T]) -> type[T | None]:  # type: ignore[misc]
        """Call syntax: ``Context(WorkspaceRef)``."""
        return Annotated[typ | None, ContextMarker(inner_type=typ), PydanticField(default=None)]  # type: ignore[return-value]


@dataclass(frozen=True)
class Check:
    """Marker for runtime verification of inputs/outputs."""

    predicate: Callable[..., bool]
    message: str = ""

    def __call__(self, value: Any) -> bool:
        return self.predicate(value)

    def format_message(self, value: Any, field_name: str = "field") -> str:
        if not self.message:
            return f"Check failed for {field_name}: {value!r}"
        try:
            return self.message.format(value=value, field=field_name)
        except (KeyError, IndexError):
            return self.message


class TaskRef:
    """Type marker for task class references in meta-tasks."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        del source_type, handler
        from pydantic_core import core_schema

        return core_schema.any_schema()


class CompletedTask:
    """Type marker for completed task instances in meta-tasks."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        del source_type, handler
        from pydantic_core import core_schema

        return core_schema.any_schema()


@dataclass(frozen=True)
class ArtifactMarker:
    """Marker for artifact output fields."""

    inner_type: type[Any]
    filename: str
    description: str = ""
    required: bool = True

    def __repr__(self) -> str:
        type_name = getattr(self.inner_type, "__name__", str(self.inner_type))
        return f"Artifact({type_name}, filename={self.filename!r})"


def Artifact(
    typ: type[T],
    *,
    filename: str,
    description: str = "",
    required: bool = True,
) -> type[T | None]:
    """Mark a type as an artifact output."""
    marker = ArtifactMarker(
        inner_type=typ,
        filename=filename,
        description=description,
        required=required,
    )
    return Annotated[typ | None, marker, PydanticField(default=None)]  # type: ignore[return-value]


def FileExists(message: str = "") -> Check:
    """Check that a file or directory exists at the given path."""

    def _check(v: Any) -> bool:
        return Path(v).exists()

    return Check(predicate=_check, message=message or "File does not exist: {value}")


def NonEmpty(message: str = "") -> Check:
    """Check that a value is not empty."""

    def _check(v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            return bool(v.strip())
        if isinstance(v, (list, dict, set, tuple)):
            return len(v) > 0
        return True

    return Check(predicate=_check, message=message or "Value must not be empty: {value!r}")


def InRange(min_val: Any = None, max_val: Any = None, message: str = "") -> Check:
    """Check that a numeric value is within inclusive bounds."""

    def _check(v: Any) -> bool:
        if min_val is not None and v < min_val:
            return False
        return not (max_val is not None and v > max_val)

    if not message:
        if min_val is not None and max_val is not None:
            message = f"Value {{value}} not in range [{min_val}, {max_val}]"
        elif min_val is not None:
            message = f"Value {{value}} must be >= {min_val}"
        else:
            message = f"Value {{value}} must be <= {max_val}"
    return Check(predicate=_check, message=message)


def Matches(pattern: str, message: str = "") -> Check:
    """Check that a string value matches a regex pattern."""
    compiled = re.compile(pattern)
    safe_pattern = pattern.replace("{", "{{").replace("}", "}}")

    def _check(v: Any) -> bool:
        return compiled.search(str(v)) is not None

    return Check(
        predicate=_check,
        message=message or f"Value {{value!r}} does not match pattern '{safe_pattern}'",
    )


def MaxLength(length: int, message: str = "") -> Check:
    """Check that len(value) <= length."""

    def _check(v: Any) -> bool:
        return len(v) <= length

    return Check(predicate=_check, message=message or f"Length exceeds maximum of {length}")


__all__ = [
    "Artifact",
    "ArtifactMarker",
    "Check",
    "CompletedTask",
    "Context",
    "ContextMarker",
    "FileExists",
    "InRange",
    "Infer",
    "Input",
    "InputMarker",
    "Matches",
    "MaxLength",
    "NonEmpty",
    "Output",
    "OutputMarker",
    "TaskRef",
    "_InferMarker",
]
