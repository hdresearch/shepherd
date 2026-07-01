"""Effect emission and parent propagation for ScopeProxy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from .substrate import EffectLayer

if TYPE_CHECKING:
    from shepherd_core.effects import Effect

    from .substrate import ImmutableScope

__all__ = ["EmissionEngine", "EmissionHost"]


class EmissionHost(Protocol):
    """Narrow host contract for scope emission."""

    def emission_snapshot(self) -> ImmutableScope: ...

    def replace_emission_snapshot(self, scope: ImmutableScope) -> None: ...

    def persist_emitted_layer(self, layer: EffectLayer) -> None: ...

    def propagate_emitted_layer(self, layer: EffectLayer) -> None: ...

    @property
    def emission_lock(self) -> Any: ...

    @property
    def emission_scope_id(self) -> str: ...

    @property
    def emission_depth(self) -> int: ...


class EmissionEngine:
    """Owns emission ordering, layer metadata, and parent fan-in."""

    __slots__ = ("_host",)

    def __init__(self, host: EmissionHost) -> None:
        self._host = host

    def emit(self, effect: Effect) -> EffectLayer:
        """Emit one effect while preserving ScopeProxy's current ordering."""
        with self._host.emission_lock:
            snapshot = self._host.emission_snapshot()
            layer = EffectLayer(
                effect=effect,
                sequence=len(snapshot._stream._layers),
                source_context=getattr(effect, "context_id", None),
                scope_id=self._host.emission_scope_id,
                scope_depth=self._host.emission_depth,
            )
            snapshot = snapshot.with_layer(layer)
            snapshot = snapshot.apply_effect(effect)
            self._host.replace_emission_snapshot(snapshot)

        self._host.persist_emitted_layer(layer)
        self._host.propagate_emitted_layer(layer)
        return layer

    def receive_layer(self, layer: EffectLayer) -> None:
        """Apply a child-emitted layer to this scope, then persist and propagate."""
        with self._host.emission_lock:
            snapshot = self._host.emission_snapshot()
            snapshot = snapshot.with_layer(layer)
            snapshot = snapshot.apply_effect(layer.effect)
            self._host.replace_emission_snapshot(snapshot)

        self._host.persist_emitted_layer(layer)
        self._host.propagate_emitted_layer(layer)

    def emit_all(self, effects: tuple[Effect, ...] | list[Effect]) -> list[EffectLayer]:
        """Emit multiple effects through the same engine."""
        return [self.emit(effect) for effect in effects]
