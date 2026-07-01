"""Scope protocol - container with fork/merge/discard/materialize.

The scope is the resource container. It owns:
- Effect stream (what happened)
- Context bindings (what resources are available)
- Provider registry (how to execute)

Scope Operations (The Four Primitives)
--------------------------------------
- fork(): Create isolated child scope
- merge(child): Propagate child's effects to this scope
- discard(): Abandon this scope's effects
- materialize(): Effects escape to real world

These four operations are PRIMITIVES. Everything else (checkpoint,
rollback, gate, retry) is built from them.

Containment Model
-----------------
Effects exist at containment levels:

    SANDBOX -> SCOPE -> MATERIALIZED -> ESCAPED

Contained effects (SANDBOX, SCOPE) can be discarded freely.
Escaped effects (MATERIALIZED, ESCAPED) may need reversal.

Design principle: Gate before escape, not reverse after.

See Also:
    design/syntax-api/DESIGN-primitives-layer.md - Full specification
    design/effect-system/FOUNDATIONS-unified-theory.md - Theoretical foundations
    design/syntax-api/DESIGN-combinators-library.md - Combinators built on these primitives
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from shepherd_core.foundation.protocols.effect import EffectProtocol
    from shepherd_core.foundation.protocols.stream import StreamProtocol


class ScopeProtocol(Protocol):
    """Container with fork/merge/discard semantics.

    The core invariant:
        state(t) = fold(apply_effect, effects[0:t], initial_state)
    """

    @property
    def id(self) -> str:
        """Unique identifier for this scope."""
        ...

    @property
    def effects(self) -> StreamProtocol:
        """The effect stream for this scope.

        Returns a scope-bound stream with context for direct()/by_depth().
        """
        ...

    @property
    def is_discarded(self) -> bool:
        """Whether this scope has been discarded.

        A discarded scope cannot be merged or used further.
        """
        ...

    @property
    def is_materialized(self) -> bool:
        """Whether this scope's effects have been materialized.

        A materialized scope's effects have escaped containment.
        """
        ...

    # --- The Four Primitives ---

    def fork(self) -> ScopeProtocol:
        """Create an isolated child scope for speculative execution.

        The forked scope:
        - Has independent effect stream (not linked to parent)
        - Copies current bindings (snapshot at fork time)
        - Copies provider registry

        Effects in the fork do NOT propagate to parent until merge().

        Returns:
            New independent scope

        Example:
            child = scope.fork()
            result = await task(scope=child)

            if approved:
                scope.merge(child)   # Effects propagate
            else:
                child.discard()      # Effects vanish
        """
        ...

    def merge(self, child: ScopeProtocol) -> None:
        """Propagate child's effects to this scope.

        After merge:
        - Child's effects appear in this scope's stream
        - Child scope should not be used further
        - State is recomputed via the fold invariant

        Args:
            child: A scope previously created by fork()

        Raises:
            ScopeError: If child was already discarded
        """
        ...

    def discard(self) -> None:
        """Abandon this scope's effects.

        After discard:
        - This scope's effects are lost
        - This scope cannot be merged
        - Parent scope is unchanged (fold invariant)

        Safe to call:
        - On any forked scope
        - Multiple times (idempotent)

        NOT safe after:
        - materialize() has been called (effects already escaped)

        Raises:
            ContainmentError: If effects have already escaped
        """
        ...

    async def materialize(self) -> None:
        """Apply effects to the real world.

        After materialize:
        - File patches are written to disk
        - Git commits are created
        - External APIs are called
        - Effects have ESCAPED containment

        Cannot be undone via discard() after this point.

        Raises:
            RuntimeError: If called from non-root scope
        """
        ...

    def emit(self, effect: EffectProtocol) -> None:
        """Emit an effect to this scope's stream.

        The effect is:
        1. Added to the stream with scope metadata
        2. Applied to derive new state (fold)
        3. Propagated to parent if this is a child scope

        Args:
            effect: The effect to emit
        """
        ...


__all__ = ["ScopeProtocol"]
