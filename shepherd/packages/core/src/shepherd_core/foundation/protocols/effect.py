"""Effect protocol - the minimal contract for effect types.

Effects are VALUES, not actions. They describe what happened
without making it happen. This separation enables:

- Multiple interpretations (preview, mock, live)
- Replay and time-travel debugging
- Speculative execution with discard

See Also:
    design/syntax-api/DESIGN-primitives-layer.md - Full specification
    design/effect-system/FOUNDATIONS-unified-theory.md - Theoretical foundations
"""

from __future__ import annotations

from typing import Protocol, Self, runtime_checkable


@runtime_checkable
class EffectProtocol(Protocol):
    """Immutable description of a state change.

    Implementations should be frozen (immutable). The canonical
    implementation uses frozen Pydantic models.

    Attributes:
        effect_type: Discriminator for serialization (e.g., "task_started")
        timestamp: When the effect was created (for ordering)
        task_name: Task that produced this effect (optional)
        context_id: Context this effect targets (semantic routing)
        binding_name: Binding this effect targets (stable routing)
    """

    effect_type: str
    timestamp: float

    # Optional attribution (for routing and filtering)
    task_name: str | None
    context_id: str | None
    binding_name: str | None

    def with_attribution(
        self,
        task_name: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> Self:
        """Return copy with updated attribution.

        None values preserve existing attribution.
        """
        ...


__all__ = ["EffectProtocol"]
