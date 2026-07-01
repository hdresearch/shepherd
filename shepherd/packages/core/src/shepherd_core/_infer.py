"""Infer markers shared between core autoconfig and runtime task authoring."""

from __future__ import annotations

from typing import Annotated, TypeVar

T = TypeVar("T")


class _InferMarker:
    """Metadata marker indicating a field can be inferred from context."""

    def __call__(self, typ: type[T]) -> type[T]:
        """Wrap a type as inferable: ``Infer(str)`` -> ``Annotated[str, _InferMarker()]``."""
        return Annotated[typ, _InferMarker()]  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "Infer"


Infer = _InferMarker()

__all__ = ["Infer", "_InferMarker"]
