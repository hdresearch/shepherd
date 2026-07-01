"""Runtime-owned task metadata extraction and context resolution."""

from __future__ import annotations

import logging
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin, get_type_hints

from pydantic.fields import FieldInfo as PydanticFieldInfo
from pydantic.fields import PydanticUndefined  # type: ignore[attr-defined]

from .markers import ArtifactMarker, Check, ContextMarker, InputMarker, OutputMarker

if TYPE_CHECKING:
    from shepherd_core.context.kernel import ExecutionContext

    from shepherd_runtime.scope import Scope

logger = logging.getLogger(__name__)


@dataclass
class FieldInfo:
    """Information about a task field."""

    name: str
    inner_type: type
    marker_type: str
    description: str = ""
    required: bool = True
    default: Any = None
    has_default_factory: bool = False
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskMetadata:
    """Metadata extracted from a @task decorated class."""

    name: str = ""
    docstring: str = ""
    guidance: str = ""
    inputs: dict[str, FieldInfo] = field(default_factory=dict)
    outputs: dict[str, FieldInfo] = field(default_factory=dict)
    artifacts: dict[str, FieldInfo] = field(default_factory=dict)
    contexts: dict[str, FieldInfo] = field(default_factory=dict)
    artifact_markers: dict[str, ArtifactMarker] = field(default_factory=dict)
    input_checks: dict[str, list[Check]] = field(default_factory=dict)
    output_checks: dict[str, list[Check]] = field(default_factory=dict)
    cacheable: bool = True


def extract_task_metadata(cls: type) -> TaskMetadata:
    """Extract metadata from a task class."""
    meta = TaskMetadata(
        name=cls.__name__,
        docstring=cls.__doc__ or "",
    )

    try:
        hints = get_type_hints(cls, include_extras=True)
    except Exception as e:
        from shepherd_core.config import is_strict_mode
        from shepherd_core.errors import MetadataExtractionError

        if is_strict_mode():
            raise MetadataExtractionError(cls.__name__, e) from e
        logger.warning("Type hint extraction failed for %s, using annotations: %s", cls.__name__, e)
        hints = getattr(cls, "__annotations__", {})

    for name, hint in hints.items():
        if name.startswith("_"):
            continue

        marker = _extract_marker(hint)
        if marker is None:
            continue

        pydantic_field = _extract_pydantic_field(hint)
        description = (pydantic_field.description or "") if pydantic_field else ""
        constraints = _extract_constraints(pydantic_field)
        required, default, has_default_factory = _extract_model_field_state(cls, name)

        if isinstance(marker, InputMarker):
            meta.inputs[name] = FieldInfo(
                name=name,
                inner_type=_get_inner_type(hint),
                marker_type="input",
                description=description,
                required=required,
                default=default,
                has_default_factory=has_default_factory,
                constraints=constraints,
            )
            checks = _extract_checks(hint)
            if checks:
                meta.input_checks[name] = checks
        elif isinstance(marker, OutputMarker):
            meta.outputs[name] = FieldInfo(
                name=name,
                inner_type=_get_inner_type(hint),
                marker_type="output",
                description=description,
                required=required,
                default=default,
                has_default_factory=has_default_factory,
                constraints=constraints,
            )
            checks = _extract_checks(hint)
            if checks:
                meta.output_checks[name] = checks
        elif isinstance(marker, ArtifactMarker):
            meta.artifacts[name] = FieldInfo(
                name=name,
                inner_type=marker.inner_type,
                marker_type="artifact",
                description=description or marker.description,
                required=required,
                default=default,
                has_default_factory=has_default_factory,
                constraints=constraints,
            )
            meta.artifact_markers[name] = marker
        elif isinstance(marker, ContextMarker):
            meta.contexts[name] = FieldInfo(
                name=name,
                inner_type=marker.inner_type,
                marker_type="context",
                description=description,
                required=required,
                default=default,
                has_default_factory=has_default_factory,
                constraints=constraints,
            )

    return meta


def _extract_marker(hint: Any) -> Any:
    """Extract Input/Output/Artifact/Context marker from type hint."""
    if hasattr(hint, "__metadata__"):
        for meta in hint.__metadata__:
            if isinstance(meta, (InputMarker, OutputMarker, ArtifactMarker, ContextMarker)):
                return meta
    return None


def _extract_checks(hint: Any) -> list[Check]:
    """Extract all Check markers from type hint metadata."""
    if not hasattr(hint, "__metadata__"):
        return []
    return [meta for meta in hint.__metadata__ if isinstance(meta, Check)]


def _get_inner_type(hint: Any) -> Any:
    """Get the inner type from an Annotated hint."""
    if hasattr(hint, "__origin__"):
        args = getattr(hint, "__args__", ())
        if args:
            return args[0]
    return hint


def _strip_none_from_type(t: Any) -> Any:
    """Remove None from Union types."""
    origin = get_origin(t)
    is_union = origin is Union or (hasattr(types, "UnionType") and isinstance(t, types.UnionType))

    if not is_union:
        return t

    args = get_args(t)
    non_none = [a for a in args if a is not type(None)]

    if len(non_none) == 0:
        return t
    if len(non_none) == 1:
        return non_none[0]
    return Union[tuple(non_none)]  # noqa: UP007


def strip_none_from_type(t: Any) -> Any:
    """Public helper for stripping None from task field types."""
    return _strip_none_from_type(t)


def _extract_pydantic_field(hint: Any) -> PydanticFieldInfo | None:
    """Extract Pydantic FieldInfo from Annotated metadata."""
    if not hasattr(hint, "__metadata__"):
        return None

    field_infos: list[PydanticFieldInfo] = []
    for meta in hint.__metadata__:
        if isinstance(meta, PydanticFieldInfo):
            field_infos.append(meta)

    if not field_infos:
        return None
    if len(field_infos) == 1:
        return field_infos[0]

    for field_info in field_infos:
        if field_info.description or field_info.metadata:
            return field_info

    return field_infos[0]


def _extract_model_field_state(cls: type, field_name: str) -> tuple[bool, Any, bool]:
    """Extract required/default metadata from the compiled Pydantic model field."""
    model_fields = getattr(cls, "model_fields", {})
    model_field = model_fields.get(field_name)
    if model_field is None:
        return True, None, False

    has_default_factory = model_field.default_factory is not None
    default = None if model_field.default is PydanticUndefined else model_field.default
    return model_field.is_required(), default, has_default_factory


def _extract_constraints(pydantic_field: PydanticFieldInfo | None) -> dict[str, Any]:
    """Extract constraint dict from Pydantic FieldInfo.metadata."""
    constraints: dict[str, Any] = {}
    if pydantic_field is None or not pydantic_field.metadata:
        return constraints

    for constraint in pydantic_field.metadata:
        if hasattr(constraint, "ge"):
            constraints["ge"] = constraint.ge
        if hasattr(constraint, "le"):
            constraints["le"] = constraint.le
        if hasattr(constraint, "gt"):
            constraints["gt"] = constraint.gt
        if hasattr(constraint, "lt"):
            constraints["lt"] = constraint.lt
        if hasattr(constraint, "min_length"):
            constraints["min_length"] = constraint.min_length
        if hasattr(constraint, "max_length"):
            constraints["max_length"] = constraint.max_length

    return constraints


def _resolve_contexts(
    meta: TaskMetadata,
    scope: Scope,
    explicit_contexts: dict[str, Any],
) -> dict[str, ExecutionContext]:
    """Resolve Context fields from scope."""
    from shepherd_core.context.kernel import ExecutionContext
    from shepherd_core.errors import ContextResolutionError

    resolved: dict[str, ExecutionContext] = {}

    for name, field_info in meta.contexts.items():
        expected_type = field_info.inner_type

        if name in explicit_contexts:
            ctx = explicit_contexts[name]
            if not isinstance(ctx, ExecutionContext):
                raise ContextResolutionError(
                    field_name=name,
                    expected_type=expected_type,
                    available_contexts=_get_available_contexts(scope),
                )
            resolved[name] = ctx
            continue

        try:
            ctx = scope.get_context(name)
            if isinstance(ctx, expected_type):
                resolved[name] = cast("ExecutionContext", ctx)
                continue
        except KeyError:
            pass

        for binding in scope.all_bindings():
            if isinstance(binding.context, expected_type):
                resolved[name] = binding.context
                break
        else:
            raise ContextResolutionError(
                field_name=name,
                expected_type=expected_type,
                available_contexts=_get_available_contexts(scope),
            )

    return resolved


def resolve_contexts(
    meta: TaskMetadata,
    scope: Scope,
    explicit_contexts: dict[str, Any],
) -> dict[str, ExecutionContext]:
    """Public runtime-facing helper for task context resolution."""
    return _resolve_contexts(meta, scope, explicit_contexts)


def _get_available_contexts(scope: Scope) -> list[tuple[str, type]]:
    """Get list of available contexts for error messages."""
    return [(binding.name, type(binding.context)) for binding in scope.all_bindings()]


__all__ = [
    "FieldInfo",
    "TaskMetadata",
    "_extract_constraints",
    "_extract_marker",
    "_extract_pydantic_field",
    "_get_available_contexts",
    "_get_inner_type",
    "_resolve_contexts",
    "_strip_none_from_type",
    "extract_task_metadata",
    "resolve_contexts",
    "strip_none_from_type",
]
