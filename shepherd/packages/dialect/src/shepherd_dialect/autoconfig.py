"""Autoconfig, the mechanical half (authoring re-pin W4; D3).

Ported from the legacy ``shepherd_core.{_infer,autoconfig}``: the ``Infer``
marker (``Annotated[str, Infer]`` / ``Infer(str)`` on Pydantic config models),
``extract_infer_fields``, and ``build_inference_model`` — pure schema
mechanics. The legacy class-form ``Input(..., infer=True)`` branch retires
(tranche D1); the LLM-backed inference that *fills* the built model rides the
battery/feature tranches (D3's fence), composed over the nucleus model seam
when it lands.
"""

from __future__ import annotations

from typing import Annotated, Any, TypeVar, get_args, get_type_hints

from pydantic import BaseModel, Field, create_model
from pydantic.fields import PydanticUndefined  # type: ignore[attr-defined]

T = TypeVar("T")

__all__ = ["Infer", "build_inference_model", "extract_infer_fields"]


class _InferMarker:
    """Metadata marker indicating a field can be inferred from context."""

    def __call__(self, typ: type[T]) -> type[T]:
        """Wrap a type as inferable: ``Infer(str)`` -> ``Annotated[str, _InferMarker()]``."""
        return Annotated[typ, _InferMarker()]  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "Infer"


Infer = _InferMarker()


def extract_infer_fields(cls: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """Extract ``Infer``-annotated fields from a config model.

    Returns ``{field_name: {type, description, default, has_default_factory}}``
    for inferable fields only (the legacy contract, function-form era: the
    ``Annotated[…, Infer]`` style is the one that survives D1).
    """
    hints = get_type_hints(cls, include_extras=True)
    model_fields = cls.model_fields
    result: dict[str, dict[str, Any]] = {}
    for name, hint in hints.items():
        if name.startswith("_"):
            continue
        metadata = getattr(hint, "__metadata__", ())
        if not any(isinstance(m, _InferMarker) for m in metadata):
            continue
        inner_args = get_args(hint)
        inner_type = inner_args[0] if inner_args else hint
        pydantic_field = model_fields.get(name)
        description = ""
        default: Any = ...
        has_default_factory = False
        if pydantic_field is not None:
            description = pydantic_field.description or ""
            if pydantic_field.default_factory is not None:
                has_default_factory = True
                default = pydantic_field.default_factory()  # type: ignore[call-arg]
            elif pydantic_field.default is not PydanticUndefined:
                default = pydantic_field.default
        result[name] = {
            "type": inner_type,
            "description": description,
            "default": default,
            "has_default_factory": has_default_factory,
        }
    return result


def build_inference_model(cls: type[BaseModel]) -> type[BaseModel]:
    """Build a Pydantic model carrying only the inferable fields (``Infer<Name>``)."""
    infer_fields = extract_infer_fields(cls)
    model_fields = cls.model_fields
    field_definitions: dict[str, Any] = {}
    for name, info in infer_fields.items():
        pydantic_field = model_fields.get(name)
        if pydantic_field is None:
            field_definitions[name] = (info["type"], ...)
        elif info["has_default_factory"]:
            field_definitions[name] = (
                info["type"],
                Field(default_factory=pydantic_field.default_factory, description=info["description"]),  # type: ignore[arg-type]
            )
        elif info["default"] is not ...:
            field_definitions[name] = (info["type"], Field(default=info["default"], description=info["description"]))
        else:
            field_definitions[name] = (info["type"], Field(description=info["description"]))
    return create_model(f"Infer{cls.__name__}", **field_definitions)
