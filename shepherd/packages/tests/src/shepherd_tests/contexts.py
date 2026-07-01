"""Shared test context implementations.

These contexts are designed for testing scope, lifecycle, and effect behavior.
They provide predictable, minimal implementations of the ExecutionContext protocol.
"""

from dataclasses import dataclass, replace
from typing import Self

from shepherd_core import Effect
from shepherd_core.context import ExecutionContextDefaults
from shepherd_core.types import ReversibilityLevel


@dataclass(frozen=True)
class CounterContext(ExecutionContextDefaults):
    """Simple counter context for testing state changes via effects.

    Responds to effects with effect_type="increment" by incrementing count.
    All other effects are ignored (returns self unchanged).

    Usage:
        ctx = CounterContext()
        ctx2 = ctx.apply_effect(Effect(effect_type="increment"))
        assert ctx2.count == 1
    """

    count: int = 0
    _context_id: str = "counter:test"

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    def apply_effect(self, effect: Effect) -> Self:
        if effect.effect_type == "increment":
            return replace(self, count=self.count + 1)
        return self


@dataclass(frozen=True)
class SimpleContext(ExecutionContextDefaults):
    """General-purpose test context with name and value fields.

    Responds to:
    - effect_type="increment": value += 1
    - effect_type="set_value" with new_value attr: value = new_value

    Usage:
        ctx = SimpleContext(name="test", value=0)
        ctx2 = ctx.apply_effect(Effect(effect_type="increment"))
        assert ctx2.value == 1
    """

    name: str = "test"
    value: int = 0

    @property
    def context_id(self) -> str:
        return f"simple:{self.name}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    def apply_effect(self, effect: Effect) -> Self:
        if effect.effect_type == "increment":
            return replace(self, value=self.value + 1)
        if effect.effect_type == "set_value":
            new_value = getattr(effect, "new_value", self.value)
            return replace(self, value=new_value)
        return self


@dataclass(frozen=True)
class NoOpContext(ExecutionContextDefaults):
    """Minimal context that ignores all effects.

    Useful when you need a context but don't care about state changes.
    """

    _context_id: str = "noop:test"

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    def apply_effect(self, effect: Effect) -> Self:
        return self
