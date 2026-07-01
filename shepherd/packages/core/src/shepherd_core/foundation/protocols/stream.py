"""Stream protocol - immutable, append-only sequence of effects with queries.

The stream is the single source of truth. All state is derived
from it via the fold invariant:

    state(t) = fold(apply_effect, effects[0:t], initial_state)

See Also:
    design/syntax-api/DESIGN-primitives-layer.md - Full specification
    design/effect-system/FOUNDATIONS-unified-theory.md - Theoretical foundations
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shepherd_core.foundation.protocols.effect import EffectProtocol


class EffectLayerProtocol(Protocol):
    """Effect with stream metadata.

    Wraps an effect with positional and provenance information.

    Attributes:
        effect: The wrapped effect instance
        sequence: Position in the stream (0-indexed)
        source_context: Context that produced this effect (for filtering)
        scope_id: Scope that emitted this effect (for direct() queries)
        scope_depth: Depth in scope hierarchy (for by_depth() queries)
    """

    @property
    def effect(self) -> EffectProtocol:
        """The wrapped effect."""
        ...

    @property
    def sequence(self) -> int:
        """Position in the stream (0-indexed)."""
        ...

    @property
    def source_context(self) -> str | None:
        """Context that produced this effect."""
        ...

    @property
    def scope_id(self) -> str | None:
        """Scope that emitted this effect."""
        ...

    @property
    def scope_depth(self) -> int:
        """Depth in scope hierarchy."""
        ...


class StreamProtocol(Protocol):
    """Immutable, append-only sequence of effects.

    Streams support rich queries for filtering by type and attribution.
    """

    @property
    def layers(self) -> tuple[Any, ...]:
        """Access effect layers with metadata."""
        ...

    def append(self, effect: EffectProtocol) -> StreamProtocol:
        """Return new stream with effect appended."""
        ...

    def query(
        self,
        effect_type: type | None = None,
        *,
        task_name: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> Iterator[Any]:
        """Query effects by type and/or attribution.

        All filters are AND-ed together. None means "don't filter".

        Args:
            effect_type: Filter to this type (or subclass)
            task_name: Filter to effects from this task
            context_id: Filter to effects for this context
            binding_name: Filter to effects for this binding

        Yields:
            Matching EffectLayers
        """
        ...

    def __iter__(self) -> Iterator[Any]:
        """Iterate over effect layers."""
        ...

    def __len__(self) -> int:
        """Number of effects in stream."""
        ...


__all__ = ["EffectLayerProtocol", "StreamProtocol"]
