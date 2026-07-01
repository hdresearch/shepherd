"""Task metadata, Input/Output serde, prompt, and source introspection (W3b).

Function-form re-pins (tranche D1) of the legacy class-form authoring surface
(`shepherd_runtime.task.metadata` / `markers` / source extraction):

- ``extract_task_metadata(fn)`` reads the *signature*: inputs (with defaults
  and Check markers, via W1's ``extract_checks``), the return type, docstring.
- ``task_input_model(fn)`` builds the Pydantic Input model; ``dump_task_args``
  / ``load_task_args`` are the JSON-boundary roundtrip the spike2 matrix pins
  — and the typed fourth-row args key (S1 seam 4: the ratified upgrade from
  the nucleus's v1 call-``repr``; key-order independent, value-sensitive).
- ``task_prompt(fn, args)`` renders the docstring + inputs + output schema —
  the prompt-generation observable.
- ``extract_task_source(fn)`` is the introspection half of the source story;
  reconstruction-and-run is superseded per D2-refined
  (`source-validation-is-advisory-the-jail-enforces`).
- ``TaskRef`` / ``CompletedTask`` port as the meta-task *type markers* only;
  meta-task execution semantics ride the transform feature re-home.
"""

from __future__ import annotations

import inspect
import json
import textwrap
import typing
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import BaseModel, create_model

from shepherd_dialect.checks import Check, extract_checks
from shepherd_dialect.steps import return_type_to_output_schema

__all__ = [
    "CompletedTask",
    "FieldInfo",
    "TaskMetadata",
    "TaskRef",
    "dump_task_args",
    "extract_task_metadata",
    "extract_task_source",
    "load_task_args",
    "task_input_model",
    "task_prompt",
]


class TaskRef:
    """Type marker for task references in meta-tasks (markers port; D1)."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        del source_type, handler
        from pydantic_core import core_schema

        return core_schema.any_schema()


class CompletedTask:
    """Type marker for completed task instances in meta-tasks (markers port; D1)."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        del source_type, handler
        from pydantic_core import core_schema

        return core_schema.any_schema()


@dataclass
class FieldInfo:
    """Information about a task input field (function-form)."""

    name: str
    inner_type: Any
    required: bool = True
    default: Any = None
    checks: tuple[Check, ...] = ()


@dataclass
class TaskMetadata:
    """Metadata extracted from a function-form ``@task``."""

    name: str = ""
    docstring: str = ""
    inputs: dict[str, FieldInfo] = field(default_factory=dict)
    return_type: Any = None
    input_checks: dict[str, tuple[Check, ...]] = field(default_factory=dict)
    output_checks: tuple[Check, ...] = ()


def _unwrap(fn: Any) -> Any:
    return getattr(fn, "_fn", fn)  # accept a TaskCallable or a bare function


def _plain(annotation: Any) -> Any:
    """Strip ``Annotated`` metadata down to the carried type."""
    if typing.get_origin(annotation) is Annotated:
        return typing.get_args(annotation)[0]
    return annotation


def extract_task_metadata(fn: Any) -> TaskMetadata:
    """Function-form extraction: signature -> inputs; return annotation -> output."""
    fn = _unwrap(fn)
    hints = typing.get_type_hints(fn, include_extras=True)
    input_checks, output_checks = extract_checks(fn)
    sig = inspect.signature(fn)
    inputs: dict[str, FieldInfo] = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        has_default = param.default is not inspect.Parameter.empty
        inputs[name] = FieldInfo(
            name=name,
            inner_type=_plain(hints.get(name, Any)),
            required=not has_default,
            default=param.default if has_default else None,
            checks=input_checks.get(name, ()),
        )
    return TaskMetadata(
        name=getattr(fn, "__name__", "?"),
        docstring=inspect.getdoc(fn) or "",
        inputs=inputs,
        return_type=_plain(hints.get("return")),
        input_checks=input_checks,
        output_checks=output_checks,
    )


def task_input_model(fn: Any) -> type[BaseModel]:
    """The task's Input model, built from the signature (the spike2 wrapper)."""
    meta = extract_task_metadata(fn)
    fields: dict[str, Any] = {
        f.name: (f.inner_type, f.default if not f.required else ...) for f in meta.inputs.values()
    }
    return create_model(f"{meta.name.title().replace('_', '')}Input", **fields)


def dump_task_args(fn: Any, args: tuple, kwargs: dict) -> dict[str, Any]:
    """Bind + dump the call's arguments JSON-safe — the typed fourth-row key.

    Same values ⇒ same dump regardless of positional/keyword spelling or key
    order (S1 seam 4); the digest downstream is ``canonical_digest({"args": …})``.
    """
    bound = inspect.signature(_unwrap(fn)).bind(*args, **kwargs)
    bound.apply_defaults()
    model = task_input_model(fn)
    return model(**bound.arguments).model_dump(mode="json")


def load_task_args(fn: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Validate a JSON-boundary dump back into typed arguments (roundtrip)."""
    model = task_input_model(fn)
    return dict(model.model_validate(data))


def task_prompt(fn: Any, args: dict[str, Any] | None = None) -> str:
    """Render the task prompt: docstring + inputs + output schema (observable)."""
    meta = extract_task_metadata(fn)
    lines = [meta.docstring or f"Task: {meta.name}", "", "Inputs:"]
    for f in meta.inputs.values():
        rendered = f"  {f.name}" + (f" = {args[f.name]!r}" if args and f.name in args else f": {f.inner_type!r}")
        lines.append(rendered)
    schema = return_type_to_output_schema(meta.return_type if meta.return_type is not type(None) else None)
    lines += ["", "Respond with JSON matching:", textwrap.indent(json.dumps(schema["schema"], indent=2), "  ")]
    return "\n".join(lines)


def extract_task_source(fn: Any) -> str:
    """The task body's source (introspection; reconstruction is superseded, D2-refined)."""
    return textwrap.dedent(inspect.getsource(_unwrap(fn)))
