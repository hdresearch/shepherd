from __future__ import annotations

from typing import Any, cast

# Values are arbitrary Python objects. Schemas catch type errors at
# perform/resume/answer boundaries; intra-evaluator bindings are permissive.
Value = Any


class Env:
    """Persistent immutable variable environment backed by parent pointers.

    `bindings` remains a compatibility/materialization surface. Runtime
    extension is O(1), and later bindings shadow earlier ones.
    """

    __slots__ = ("_bindings_cache", "_depth", "_name", "_parent", "_value")

    _bindings_cache: tuple[tuple[str, Value], ...] | None
    _depth: int
    _name: str | None
    _parent: Env | None
    _value: Value | None

    def __init__(self, bindings: tuple[tuple[str, Value], ...] = ()) -> None:
        env = self._empty()
        for name, value in bindings:
            env = env.extend(name, value)
        object.__setattr__(self, "_parent", env._parent)
        object.__setattr__(self, "_name", env._name)
        object.__setattr__(self, "_value", env._value)
        object.__setattr__(self, "_depth", env._depth)
        object.__setattr__(self, "_bindings_cache", bindings or ())

    @classmethod
    def _empty(cls) -> Env:
        env = object.__new__(cls)
        object.__setattr__(env, "_parent", None)
        object.__setattr__(env, "_name", None)
        object.__setattr__(env, "_value", None)
        object.__setattr__(env, "_depth", 0)
        object.__setattr__(env, "_bindings_cache", ())
        return env

    @classmethod
    def _node(cls, parent: Env, name: str, value: Value) -> Env:
        env = object.__new__(cls)
        object.__setattr__(env, "_parent", parent)
        object.__setattr__(env, "_name", name)
        object.__setattr__(env, "_value", value)
        object.__setattr__(env, "_depth", parent._depth + 1)
        object.__setattr__(env, "_bindings_cache", None)
        return env

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Env is immutable")

    @property
    def bindings(self) -> tuple[tuple[str, Value], ...]:
        cached = self._bindings_cache
        if cached is not None:
            return cached
        bindings: list[tuple[str, Value]] = [("", None)] * self._depth
        env: Env | None = self
        idx = self._depth - 1
        while env is not None and env._parent is not None:
            bindings[idx] = (cast("str", env._name), env._value)
            idx -= 1
            env = env._parent
        materialized = tuple(bindings)
        object.__setattr__(self, "_bindings_cache", materialized)
        return materialized

    @property
    def depth(self) -> int:
        return self._depth

    def node_parts(self) -> tuple[Env | None, str | None, Value | None, int]:
        return self._parent, self._name, self._value, self._depth

    def lookup(self, name: str) -> Value:
        env: Env | None = self
        while env is not None and env._parent is not None:
            if env._name == name:
                return env._value
            env = env._parent
        raise KeyError(name)

    def extend(self, name: str, value: Value) -> Env:
        return self._node(self, name, value)

    def __contains__(self, name: str) -> bool:
        env: Env | None = self
        while env is not None and env._parent is not None:
            if env._name == name:
                return True
            env = env._parent
        return False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Env):
            return NotImplemented
        return self.bindings == other.bindings

    def __hash__(self) -> int:
        return hash(self.bindings)

    def __repr__(self) -> str:
        return f"Env(bindings={self.bindings!r})"
