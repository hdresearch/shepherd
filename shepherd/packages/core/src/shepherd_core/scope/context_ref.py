"""Live reference to bound contexts with auto-updating values.

This module provides ContextRef, a live reference that automatically reflects
updates from effect application. It also defines the ContextAccessor protocol
that breaks the circular dependency between ContextRef and ScopeProxy.

Example:
    session = scope.bind("session", SessionState())
    result = MyTask()
    print(session.session_id)  # Always current value

The reference delegates attribute access to the underlying context,
so `ref.foo` is equivalent to `ref.value.foo`.

Type Safety:
    For full IDE autocomplete and type checking, use .value:
        session.value.session_id  # IDE knows this is str | None

    Direct attribute access works at runtime but types as Any:
        session.session_id  # Works, but IDE sees Any
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, cast

from ..errors import BindingNotFoundError

if TYPE_CHECKING:
    from shepherd_core.context import ExecutionContext

__all__ = ["ContextAccessor", "ContextRef"]


# =============================================================================
# TypeVar for Generic Context References
# =============================================================================

T_Context = TypeVar("T_Context", bound="ExecutionContext")


# =============================================================================
# Protocol for Scope Access (breaks circular dependency)
# =============================================================================


class ContextAccessor(Protocol):
    """Protocol for accessing context state from a scope.

    This protocol breaks the circular dependency between ContextRef and ScopeProxy.
    ScopeProxy implicitly implements this protocol via duck typing.

    The protocol defines the minimal interface needed by ContextRef:
    - is_closed: Check if the scope has been closed
    - get_context: Retrieve a context by binding name
    """

    @property
    def is_closed(self) -> bool:
        """Whether the scope has been closed."""
        ...

    def get_context(self, name: str) -> Any | None:
        """Get context by binding name, or None if not found."""
        ...


# =============================================================================
# Live Context Reference
# =============================================================================


class ContextRef(Generic[T_Context]):
    """Name-keyed live reference to a context bound in a scope.

    Returned by :meth:`Scope.bind` for both bare and name-keyed forms;
    holds an ``(accessor, name)`` pair and re-resolves on each access.

    Disambiguation note. The :func:`shepherd_runtime.scope.current_binding`
    lookup returns a sibling class
    ``shepherd_runtime.scope_bindings.ContextRef`` (also exported as
    ``TypedContextRef``). That one is *type-keyed* — it stores a
    ``target_type`` rather than a name. Both classes expose ``.value``
    and delegate attribute access, but they are distinct types with
    different identity semantics.

    Provides ergonomic access to context state that automatically
    reflects updates from effect application.

    Example:
        session = scope.bind("session", SessionState())
        result = MyTask()
        print(session.session_id)  # Always current value

    The reference delegates attribute access to the underlying context,
    so `ref.foo` is equivalent to `ref.value.foo`.

    Type Safety:
        For full IDE autocomplete and type checking, use .value:

            session.value.session_id  # IDE knows this is str | None

        Direct attribute access works at runtime but types as Any:

            session.session_id  # Works, but IDE sees Any

    Note:
        The reference becomes invalid if the scope is closed. Accessing
        attributes after scope exit will raise RuntimeError.

    Thread Safety:
        ContextRef is not thread-safe. Concurrent access to the same scope
        from multiple threads requires external synchronization.

    Memory:
        ContextRef holds a strong reference to its scope. The scope cannot
        be garbage collected while any refs exist. This is rarely an issue
        since refs typically share the scope's lifetime.
    """

    __slots__ = ("_accessor", "_name")
    _accessor: ContextAccessor
    _name: str

    def __init__(self, accessor: ContextAccessor, name: str) -> None:
        """Initialize a context reference.

        Args:
            accessor: Object implementing ContextAccessor protocol (typically ScopeProxy)
            name: The binding name for the context
        """
        object.__setattr__(self, "_accessor", accessor)
        object.__setattr__(self, "_name", name)

    @property
    def value(self) -> T_Context:
        """Get the current context from the scope.

        Returns the context as it currently exists in the scope,
        reflecting any effects that have been applied.

        Raises:
            RuntimeError: If the scope has been closed.
            BindingNotFoundError: If the binding no longer exists.
        """
        if self._accessor.is_closed:
            raise RuntimeError(
                f"Cannot access ContextRef for '{self._name}': scope has been closed. "
                f"Store the context value before exiting the scope if needed."
            )
        ctx = self._accessor.get_context(self._name)
        if ctx is None:
            raise BindingNotFoundError(self._name)
        return cast("T_Context", ctx)

    @property
    def binding_name(self) -> str:
        """The name this context is bound under in the scope."""
        return self._name

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the current context."""
        return getattr(self.value, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot set '{name}' on ContextRef. Contexts are immutable; effects are applied via ExecutionLifecycle."
        )

    def __iter__(self) -> Any:
        raise TypeError("ContextRef is not iterable. Use ref.value to access the underlying context.")

    def __len__(self) -> int:
        raise TypeError("ContextRef has no len(). Use ref.value to access the underlying context.")

    def __getitem__(self, key: Any) -> Any:
        raise TypeError("ContextRef is not subscriptable. Use ref.value to access the underlying context.")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ContextRef):
            return self._accessor is other._accessor and self._name == other._name
        return NotImplemented

    def __hash__(self) -> int:
        return hash((id(self._accessor), self._name))

    def __repr__(self) -> str:
        try:
            ctx = self.value
            return f"ContextRef({ctx!r})"
        except RuntimeError:
            return f"ContextRef(closed: {self._name!r})"
        except KeyError:
            return f"ContextRef(unbound: {self._name!r})"

    def __dir__(self) -> list[str]:
        """Include context attributes for REPL/debugger discoverability."""
        own = ["value", "binding_name"]
        try:
            ctx = self.value
            return own + [a for a in dir(ctx) if not a.startswith("_")]
        except (KeyError, RuntimeError):
            return own

    def __bool__(self) -> bool:
        """Ref is always truthy, even if context would be falsy.

        This prevents surprising behavior like `if ref:` being False
        when the underlying context happens to be falsy.
        """
        return True

    def __copy__(self) -> ContextRef[T_Context]:
        """Copying returns the same ref (it's just a pointer)."""
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> ContextRef[T_Context]:
        """Deep copying returns the same ref (it's just a pointer)."""
        return self
