"""Inspection and debug-summary helpers for ScopeProxy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from shepherd_core.effects import Effect

    from .substrate import ImmutableScope

__all__ = ["ScopeInspectionFacade", "ScopeInspectionHost"]


class ScopeInspectionHost(Protocol):
    """Narrow host contract for inspection helpers."""

    def inspection_snapshot(self) -> ImmutableScope: ...

    def inspection_resolve_provider_id(self, provider: str) -> str: ...


class ScopeInspectionFacade:
    """Owns message extraction and debug summaries for ScopeProxy."""

    __slots__ = ("_host",)

    def __init__(self, host: ScopeInspectionHost) -> None:
        self._host = host

    def get_messages(
        self,
        task_name: str | None = None,
        provider: str | None = None,
    ) -> list[dict[str, str]]:
        """Extract conversation messages from the current effect stream."""
        from shepherd_core.effects import AgentMessage, AgentThinking, PromptSent

        stream = self._host.inspection_snapshot()._stream
        if task_name:
            stream = stream.by_task(task_name)
        if provider:
            stream = stream.by_provider(self._host.inspection_resolve_provider_id(provider))

        messages: list[dict[str, str]] = []
        for layer in stream.layers:
            effect = layer.effect
            if isinstance(effect, PromptSent) and effect.user_prompt_preview:
                messages.append(
                    {
                        "role": "user",
                        "content": effect.user_prompt_preview,
                        "task": layer.task_name or "",
                    }
                )
            elif isinstance(effect, AgentThinking) and effect.content and not effect.is_partial:
                messages.append(
                    {
                        "role": "thinking",
                        "content": effect.content,
                        "task": layer.task_name or "",
                    }
                )
            elif isinstance(effect, AgentMessage) and effect.content and not effect.is_partial:
                messages.append(
                    {
                        "role": "assistant",
                        "content": effect.content,
                        "task": layer.task_name or "",
                    }
                )
        return messages

    def effect_counts(self) -> dict[str, int]:
        """Count effects by concrete effect type name."""
        from collections import Counter

        stream = self._host.inspection_snapshot()._stream
        return dict(Counter(type(layer.effect).__name__ for layer in stream.layers))

    def effects_by_binding(self) -> dict[str, list[Effect]]:
        """Group effects by binding name for debugging."""
        from collections import defaultdict

        result: dict[str, list[Effect]] = defaultdict(list)
        stream = self._host.inspection_snapshot()._stream
        for layer in stream.layers:
            binding = layer.effect.binding_name or ""
            result[binding].append(layer.effect)
        return dict(result)

    def summary(self) -> str:
        """Render a quick textual summary of scope state."""
        stream = self._host.inspection_snapshot()._stream
        lines = [
            "Scope Summary",
            "=" * 40,
            f"Total effects: {len(stream.layers)}",
            "",
            "By type:",
        ]

        for type_name, count in sorted(self.effect_counts().items(), key=lambda item: -item[1]):
            lines.append(f"  {type_name}: {count}")

        by_binding = self.effects_by_binding()
        if len(by_binding) > 1 or "" not in by_binding:
            lines.extend(["", "By binding:"])
            for binding, effects in sorted(by_binding.items()):
                label = binding or "(lifecycle)"
                lines.append(f"  {label}: {len(effects)}")

        return "\n".join(lines)
