"""Minimal runtime scope protocols for boundary-facing helpers.

These protocols capture the subset of the scope API needed by runtime-owned
context mixins and device-transfer helpers without exposing `ScopeProxy`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from shepherd_core.scope.context_ref import ContextRef

if TYPE_CHECKING:
    from collections.abc import Sequence


class EffectLayerLike(Protocol):
    """Minimal effect-layer surface used by visible-patch collection."""

    @property
    def effect(self) -> object:
        """The effect stored in this layer."""
        ...


class EffectStreamLike(Protocol):
    """Minimal effect-stream surface used by visible-patch collection."""

    @property
    def layers(self) -> Sequence[EffectLayerLike]:
        """Ordered effect layers visible to the current scope."""
        ...


class BindScope(Protocol):
    """Minimal scope interface required by `Bindable.bind()`."""

    def bind(self, name: str, value: object) -> ContextRef[Any]:
        """Bind a context-like object and return a live context ref."""
        ...


class TransferScope(Protocol):
    """Minimal scope interface required by runtime transfer helpers."""

    @property
    def effects(self) -> EffectStreamLike:
        """Effect stream visible to the current scope."""
        ...

    def get_context(self, name: str) -> Any | None:
        """Get the currently bound context for a binding name."""
        ...


class BindingViewLike(Protocol):
    """Minimal binding surface for cache-key computation.

    Captures the subset of ``ContextBinding`` needed by
    ``_cache_key._compute_contexts_hash`` — attribute access on ``.name``
    and ``.context`` — without importing the concrete kernel type.
    """

    @property
    def name(self) -> str: ...

    @property
    def context(self) -> Any: ...


def create_stream(layers: tuple[Any, ...] = ()) -> Any:
    """Construct a ``Stream`` from effect layers.

    This factory isolates the concrete ``shepherd_core.scope.stream.Stream``
    constructor behind a runtime-local seam so peripheral files do not need
    to import the kernel substrate directly.

    This is a runtime-internal helper, not a public API.
    """
    from shepherd_core.scope.stream import Stream

    return Stream(_layers=layers)


__all__ = [
    "BindScope",
    "BindingViewLike",
    "ContextRef",
    "EffectLayerLike",
    "EffectStreamLike",
    "TransferScope",
    "create_stream",
]
