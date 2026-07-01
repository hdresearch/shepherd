"""Immutable scope model types.

This module holds the pure scope data model:
- ContextBinding: immutable binding record
- ImmutableScope: immutable snapshot with pure update operations
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from ..context import ExecutionContext
    from ..effects import Effect
    from .stream import Stream

import logging

from ..errors import BindingNotFoundError
from .stream import Stream

__all__ = ["ContextBinding", "ImmutableScope"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextBinding:
    """Immutable binding record with initial state for checkpoint/restore.

    Links a name to an ExecutionContext. Lifecycle state (is_prepared,
    in_lifecycle) is tracked separately by ScopeProxy, not stored here.

    The initial_context captures the context state at bind time, enabling
    checkpoint/restore to reset to initial state and replay effects.
    """

    name: str
    context: ExecutionContext
    initial_context: ExecutionContext | None = None

    def __post_init__(self) -> None:
        if self.initial_context is None:
            object.__setattr__(self, "initial_context", self.context)

    def with_context(self, new_context: ExecutionContext) -> ContextBinding:
        return ContextBinding(
            name=self.name,
            context=new_context,
            initial_context=self.initial_context,
        )

    def __repr__(self) -> str:
        return f"ContextBinding({self.name})"


@dataclass(frozen=True)
class ImmutableScope:
    """Immutable scope state. All updates return new instances.

    This is the FUNCTIONAL CORE of the scope system. It maintains the
    core invariant:
        state(t) = fold(apply_effect, effects[0:t], initial_state)

    ScopeProxy wraps this class to provide the familiar imperative API.
    """

    _id: str = field(default_factory=lambda: f"scope_{uuid4().hex[:8]}")
    _bindings: tuple[ContextBinding, ...] = ()
    _stream: Stream = field(default_factory=Stream)
    _parent: ImmutableScope | None = None
    _origin_id: str | None = None
    _binding_index: dict[str, ContextBinding] = field(default_factory=dict, init=False, repr=False, compare=False)
    _context_id_index: dict[str, str] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Build binding indices for O(1) lookup."""
        binding_index = {b.name: b for b in self._bindings}
        context_id_index = {b.context.context_id: b.name for b in self._bindings}
        object.__setattr__(self, "_binding_index", binding_index)
        object.__setattr__(self, "_context_id_index", context_id_index)

    @property
    def bindings(self) -> tuple[ContextBinding, ...]:
        """All bindings in this scope (not including parent bindings)."""
        return self._bindings

    def with_binding(self, name: str, context: ExecutionContext) -> ImmutableScope:
        """Return new scope with binding added."""
        if name in self._binding_index:
            raise ValueError(f"Context '{name}' already bound")

        context_id = context.context_id
        if context_id in self._context_id_index:
            existing_name = self._context_id_index[context_id]
            raise ValueError(
                f"Context with context_id '{context_id}' already bound as '{existing_name}'. "
                f"Each context must have a unique context_id."
            )

        new_binding = ContextBinding(name=name, context=context, initial_context=context)
        return replace(self, _bindings=(*self._bindings, new_binding))

    def with_updated_context(self, name: str, new_context: ExecutionContext) -> ImmutableScope:
        for i, binding in enumerate(self._bindings):
            if binding.name == name:
                new_bindings = (*self._bindings[:i], binding.with_context(new_context), *self._bindings[i + 1 :])
                return replace(self, _bindings=new_bindings)

        if self._parent is not None:
            new_parent = self._parent.with_updated_context(name, new_context)
            return replace(self, _parent=new_parent)

        raise BindingNotFoundError(name)

    def with_effect(self, effect: Effect) -> ImmutableScope:
        return replace(self, _stream=self._stream.append(effect))

    def with_layer(self, layer: Any) -> ImmutableScope:
        return replace(self, _stream=self._stream.append_layer(layer))

    def apply_effect(self, effect: Effect) -> ImmutableScope:
        """Return new scope with state derived from effect.

        Applies effect to relevant contexts via their apply_effect() method.

        Routing Precedence
        ------------------
        1. If effect has binding_name, route to matching binding (stable routing)
        2. Otherwise, route by context_id (semantic routing)

        This dual-mode routing allows:
        - Lifecycle effects to route reliably regardless of context state changes
        - Domain effects to route by semantic identity (context_id)

        The binding_name routing is essential for contexts like SessionState where
        context_id changes during effect application (e.g., when session_id is set).
        """
        binding_name = getattr(effect, "binding_name", None)
        context_id = getattr(effect, "context_id", None)

        target_binding_names: set[str] = set()

        if binding_name is not None:
            if binding_name in self._binding_index:
                target_binding_names.add(binding_name)
            else:
                logger.debug(
                    "Effect %s has binding_name='%s' but no such binding exists (available: %s)",
                    effect.effect_type,
                    binding_name,
                    list(self._binding_index.keys()),
                )
        elif context_id is not None:
            for binding in self._bindings:
                if binding.context.context_id == context_id:
                    target_binding_names.add(binding.name)
            if not target_binding_names:
                logger.debug(
                    "Effect %s has context_id='%s' but no binding matches (available context_ids: %s)",
                    effect.effect_type,
                    context_id,
                    [binding.context.context_id for binding in self._bindings],
                )

        if not target_binding_names:
            return self

        new_bindings = []
        changed = False
        for binding in self._bindings:
            if binding.name in target_binding_names:
                new_ctx = binding.context.apply_effect(effect)
                if new_ctx is not binding.context:
                    new_bindings.append(binding.with_context(new_ctx))
                    changed = True
                else:
                    new_bindings.append(binding)
            else:
                new_bindings.append(binding)

        if changed:
            return replace(self, _bindings=tuple(new_bindings))
        return self

    def with_parent(self, parent: ImmutableScope | None) -> ImmutableScope:
        """Return new scope with parent set."""
        return replace(self, _parent=parent)

    def get_binding(self, name: str) -> ContextBinding | None:
        """Get binding by name, checking local then parent."""
        binding = self._binding_index.get(name)
        if binding is not None:
            return binding
        if self._parent is not None:
            return self._parent.get_binding(name)
        return None

    def get_context(self, name: str) -> ExecutionContext | None:
        """Get context by name."""
        binding = self.get_binding(name)
        return binding.context if binding else None

    @property
    def all_bindings(self) -> tuple[ContextBinding, ...]:
        """All bindings including inherited from parent."""
        if self._parent is None:
            return self._bindings

        parent_bindings = self._parent.all_bindings
        local_names = {binding.name for binding in self._bindings}
        result = tuple(binding for binding in parent_bindings if binding.name not in local_names)
        return result + self._bindings
