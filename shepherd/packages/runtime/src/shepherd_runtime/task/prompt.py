"""Public runtime task prompt helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args, get_origin

from shepherd_runtime.task.source_analysis import SourceExtractionError, extract_task_source

from .markers import CompletedTask, TaskRef
from .metadata import strip_none_from_type

if TYPE_CHECKING:
    from .metadata import FieldInfo, TaskMetadata


def _format_constraints(constraints: dict[str, Any]) -> str:
    parts: list[str] = []

    if "ge" in constraints and "le" in constraints:
        parts.append(f"range {constraints['ge']}-{constraints['le']}")
    elif "ge" in constraints:
        parts.append(f"min {constraints['ge']}")
    elif "le" in constraints:
        parts.append(f"max {constraints['le']}")

    if "gt" in constraints:
        parts.append(f">{constraints['gt']}")
    if "lt" in constraints:
        parts.append(f"<{constraints['lt']}")

    if "min_length" in constraints and "max_length" in constraints:
        parts.append(f"length {constraints['min_length']}-{constraints['max_length']}")
    elif "min_length" in constraints:
        parts.append(f"min length {constraints['min_length']}")
    elif "max_length" in constraints:
        parts.append(f"max length {constraints['max_length']}")

    return ", ".join(parts)


def _format_field_description(field_info: FieldInfo) -> str:
    parts: list[str] = []
    if field_info.description:
        parts.append(field_info.description)
    if field_info.constraints:
        constraint_str = _format_constraints(field_info.constraints)
        if constraint_str:
            parts.append(constraint_str)
    return ", ".join(parts)


def _is_task_ref_type(inner_type: Any) -> bool:
    return inner_type is TaskRef


def _task_ref_output_kind(inner_type: Any) -> str | None:
    stripped = strip_none_from_type(inner_type)
    if stripped is TaskRef:
        return "single"

    origin = get_origin(stripped)
    if origin is list:
        args = get_args(stripped)
        if args and args[0] is TaskRef:
            return "list"

    return None


def _is_task_class(value: Any) -> bool:
    return isinstance(value, type) and hasattr(value, "_task_meta")


def _serialize_task_for_prompt(task_class: type) -> str:
    try:
        source = extract_task_source(task_class)
    except SourceExtractionError as e:
        return f"[Error extracting task source: {e}]"

    meta = task_class._task_meta  # type: ignore[attr-defined]
    docstring_first_line = meta.docstring.split("\n")[0] if meta.docstring else "No description"

    return "\n".join(
        [
            f"### Task: {meta.name}",
            "",
            f"**Purpose**: {docstring_first_line}",
            "",
            "**Source Code**:",
            "```python",
            source.strip(),
            "```",
        ]
    )


def _is_completed_task_type(inner_type: Any) -> bool:
    return inner_type is CompletedTask


def _is_task_instance(value: Any) -> bool:
    if isinstance(value, type):
        return False
    return hasattr(type(value), "_task_meta")


def _serialize_completed_task_for_prompt(instance: Any, *, max_effects: int = 200) -> str:
    task_class = type(instance)
    meta = task_class._task_meta
    sections: list[str] = []

    docstring_first_line = meta.docstring.split("\n")[0] if meta.docstring else "No description"
    sections.append(f"### Execution: {meta.name}")
    sections.append("")
    sections.append(f"**Purpose**: {docstring_first_line}")

    try:
        source = extract_task_source(task_class)
        sections.append("")
        sections.append("**Source Code**:")
        sections.append("```python")
        sections.append(source.strip())
        sections.append("```")
    except SourceExtractionError as e:
        sections.append(f"\n[Error extracting source: {e}]")

    if meta.inputs:
        sections.append("")
        sections.append("**Inputs**:")
        for name in meta.inputs:
            value = getattr(instance, name, None)
            sections.append(f"- `{name}`: {value!r}")

    if meta.outputs:
        sections.append("")
        sections.append("**Outputs**:")
        for name in meta.outputs:
            value = getattr(instance, name, None)
            if value is None:
                sections.append(f"- `{name}`: *(not produced)*")
            else:
                sections.append(f"- `{name}`: {value!r}")

    effects = getattr(instance, "effects", None)
    if effects is not None and len(effects) > 0:
        sections.append("")
        sections.append("**Effect Stream**:")
        sections.append(effects.to_markdown(max_effects=max_effects))
    else:
        sections.append("")
        sections.append("**Effect Stream**: *(empty)*")

    return "\n".join(sections)


def _serialize_completed_task_list_for_prompt(instances: list[Any], *, max_effects: int = 200) -> str:
    if not instances:
        return "*(no executions provided)*"

    seen_classes: dict[str, type] = {}
    for inst in instances:
        if _is_task_instance(inst):
            cls = type(inst)
            meta = cls._task_meta
            if meta.name not in seen_classes:
                seen_classes[meta.name] = cls

    sections: list[str] = []
    if seen_classes:
        sections.append("#### Task Definitions")
        for task_class in seen_classes.values():
            sections.append("")
            sections.append(_serialize_task_for_prompt(task_class))

    for i, inst in enumerate(instances):
        sections.append("")
        if _is_task_instance(inst):
            task_class = type(inst)
            meta = task_class._task_meta
            sections.append(f"#### Execution {i + 1}: {meta.name}")
            sections.append("")

            if meta.inputs:
                sections.append("**Inputs**:")
                for name in meta.inputs:
                    value = getattr(inst, name, None)
                    sections.append(f"- `{name}`: {value!r}")

            if meta.outputs:
                sections.append("")
                sections.append("**Outputs**:")
                for name in meta.outputs:
                    value = getattr(inst, name, None)
                    if value is None:
                        sections.append(f"- `{name}`: *(not produced)*")
                    else:
                        sections.append(f"- `{name}`: {value!r}")

            effects = getattr(inst, "effects", None)
            if effects is not None and len(effects) > 0:
                sections.append("")
                sections.append("**Effect Stream**:")
                sections.append(effects.to_markdown(max_effects=max_effects))
            else:
                sections.append("")
                sections.append("**Effect Stream**: *(empty)*")
        else:
            sections.append(
                f"#### Execution {i + 1}: [Invalid: expected completed @task instance, got {type(inst).__name__}]"
            )

    return "\n\n".join(sections)


def _serialize_input_value(name: str, value: Any, field_info: FieldInfo | None) -> str:
    if field_info and _is_task_ref_type(field_info.inner_type):
        if _is_task_class(value):
            return _serialize_task_for_prompt(value)
        return f"[Invalid: expected @task class, got {type(value).__name__}]"

    if field_info and _is_completed_task_type(field_info.inner_type):
        if _is_task_instance(value):
            return _serialize_completed_task_for_prompt(value)
        return f"[Invalid: expected completed @task instance, got {type(value).__name__}]"

    if field_info:
        inner = field_info.inner_type
        origin = get_origin(inner)
        if origin is list:
            args = get_args(inner)
            if args and _is_task_ref_type(args[0]) and isinstance(value, list):
                sections = []
                for i, task_cls in enumerate(value):
                    if _is_task_class(task_cls):
                        sections.append(f"#### Task {i + 1}")
                        sections.append(_serialize_task_for_prompt(task_cls))
                    else:
                        sections.append(f"#### Task {i + 1}: [Invalid]")
                return "\n\n".join(sections)
            if args and _is_completed_task_type(args[0]) and isinstance(value, list):
                return _serialize_completed_task_list_for_prompt(value)

    return str(value)


def _should_render_input_as_section(field_info: FieldInfo | None) -> bool:
    if field_info is None:
        return False
    if _task_ref_output_kind(field_info.inner_type) is not None:
        return True
    inner = field_info.inner_type
    if _is_completed_task_type(inner):
        return True
    origin = get_origin(inner)
    if origin is list:
        args = get_args(inner)
        if args and _is_completed_task_type(args[0]):
            return True
    return False


def _task_ref_output_guidance(meta: TaskMetadata) -> str | None:
    task_ref_fields: list[tuple[str, str]] = []
    for name, field_info in meta.outputs.items():
        kind = _task_ref_output_kind(field_info.inner_type)
        if kind is not None:
            task_ref_fields.append((name, kind))

    if not task_ref_fields:
        return None

    lines = ["## TaskRef Output Requirements"]
    for name, kind in task_ref_fields:
        if kind == "single":
            lines.append(
                f"- `{name}` must be a JSON string containing raw Python source for exactly one `@task` class."
            )
        else:
            lines.append(
                f"- `{name}` must be a JSON array of strings, where each string is raw Python source for one `@task` class."
            )

    lines.extend(
        [
            "- Do not wrap task source in Markdown code fences.",
            "- Do not include surrounding prose inside the JSON string values.",
            (
                "- Reconstruction already provides `BaseModel`, `Field`, `task`, `Input`, "
                "`Output`, `Context`, `Artifact`, `Any`, `Literal`, `Optional`, `Union`, "
                "and `Annotated`."
            ),
            (
                "- `Input()` and `Output()` take exactly one positional argument (the type). "
                "For descriptions or constraints, use `Annotated[Input(type), Field(description=...)]`. "
                "Do NOT pass keyword arguments to `Input()` or `Output()`."
            ),
        ]
    )
    return "\n".join(lines)


def generate_task_prompt(
    meta: TaskMetadata,
    inputs: dict[str, Any],
    contexts: dict[str, Any],
) -> str:
    """Generate a prompt from task metadata and inputs."""
    sections: list[str] = []

    if meta.docstring:
        sections.append(meta.docstring.strip())

    if inputs:
        input_lines = ["## Inputs"]
        for name, value in inputs.items():
            field_info = meta.inputs.get(name)
            serialized = _serialize_input_value(name, value, field_info)

            if _should_render_input_as_section(field_info) and "\n" in serialized:
                input_lines.append(f"\n### {name}\n{serialized}")
            elif field_info:
                desc = _format_field_description(field_info)
                if desc:
                    input_lines.append(f"- **{name}** ({desc}): {serialized}")
                else:
                    input_lines.append(f"- **{name}**: {serialized}")
            else:
                input_lines.append(f"- **{name}**: {serialized}")
        sections.append("\n".join(input_lines))

    context_descriptions: list[str] = []
    for name, ctx in contexts.items():
        ctx_str = str(ctx)
        if ctx_str:
            context_descriptions.append(f"### {name}\n{ctx_str}")

    if context_descriptions:
        sections.append("## Context\n" + "\n\n".join(context_descriptions))

    if meta.artifact_markers:
        artifact_lines = ["## Expected Outputs"]
        artifact_lines.append("Please write the following files to the `.artifacts/` directory:")
        for name, marker in meta.artifact_markers.items():
            desc = marker.description or f"Output for {name}"
            artifact_lines.append(f"- `.artifacts/{marker.filename}`: {desc}")
        sections.append("\n".join(artifact_lines))

    if meta.outputs:
        output_lines = ["## Response Format"]
        output_lines.append("Please provide your response as JSON with these fields:")
        for name, field_info in meta.outputs.items():
            type_name = getattr(field_info.inner_type, "__name__", str(field_info.inner_type))
            desc = _format_field_description(field_info)
            if desc:
                output_lines.append(f"- `{name}` ({type_name}): {desc}")
            else:
                output_lines.append(f"- `{name}` ({type_name})")
        sections.append("\n".join(output_lines))

    task_ref_guidance = _task_ref_output_guidance(meta)
    if task_ref_guidance:
        sections.append(task_ref_guidance)

    if meta.guidance:
        sections.append(f"## Additional Guidance\n{meta.guidance}")

    return "\n\n".join(sections)


__all__ = [
    "_format_constraints",
    "_format_field_description",
    "_is_completed_task_type",
    "_is_task_class",
    "_is_task_instance",
    "_is_task_ref_type",
    "_serialize_completed_task_for_prompt",
    "_serialize_completed_task_list_for_prompt",
    "_serialize_input_value",
    "_serialize_task_for_prompt",
    "generate_task_prompt",
]
