"""Runtime-owned SDK hooks for per-tool-call overlay isolation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shepherd_runtime.device.container.effect_collector import EffectCollector
    from shepherd_runtime.device.container.fuse_overlay import FuseOverlayManager

logger = logging.getLogger(__name__)


class StackHooks:
    """PreToolUse/PostToolUse hooks for per-tool-call overlay isolation."""

    MUTATING_TOOLS = frozenset({"Bash", "Write", "Edit", "NotebookEdit"})

    def __init__(self, overlay: FuseOverlayManager, collector: EffectCollector) -> None:
        self.overlay = overlay
        self.collector = collector
        self._layer_active = False

    async def pre_tool_use(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Prepare a fresh overlay layer before each mutating tool call."""
        tool_name = input_data.get("tool_name", "")
        approval: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }

        if tool_name not in self.MUTATING_TOOLS:
            return approval

        try:
            self.overlay.push_layer(tool_use_id or "unknown")
            self._layer_active = True
        except (ImportError, OSError, RuntimeError) as e:
            logger.warning("push_layer failed, proceeding without isolation: %s", e)
            self.overlay.cleanup_partial()
            self._layer_active = False

        return approval

    async def post_tool_use(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Merge the tool layer and emit attributed file effects."""
        if not self._layer_active:
            return {}

        try:
            effects = self.overlay.pop_and_merge(tool_use_id or "unknown")
            for effect_dict in effects:
                if self.overlay.merge_failed:
                    effect_dict["_merge_degraded"] = True
                self._emit_effect(effect_dict)
        except (ImportError, OSError, RuntimeError) as e:
            logger.warning("pop_and_merge failed: %s", e)

        self._layer_active = False
        return {}

    def _emit_effect(self, effect_dict: dict[str, Any]) -> None:
        """Convert an effect dict into a core effect object and emit it."""
        from shepherd_core.effects import FileCreate, FileDelete, FilePatch

        effect_type = effect_dict.get("effect_type")
        caused_by = effect_dict.get("caused_by")

        if effect_type == "file_create":
            self.collector.emit(
                FileCreate(
                    path=effect_dict["path"],
                    content=effect_dict.get("content", ""),
                    caused_by=caused_by,
                )
            )
        elif effect_type == "file_patch":
            self.collector.emit(
                FilePatch(
                    path=effect_dict["path"],
                    old_content=effect_dict.get("old_content", ""),
                    new_content=effect_dict.get("new_content", ""),
                    caused_by=caused_by,
                )
            )
        elif effect_type == "file_delete":
            self.collector.emit(
                FileDelete(
                    path=effect_dict["path"],
                    had_content=effect_dict.get("had_content", ""),
                    caused_by=caused_by,
                )
            )
        else:
            logger.warning("Unknown effect type: %s", effect_type)

    def as_hooks_dict(self) -> dict[str, list[Any]]:
        """Return Claude SDK hooks config using HookMatcher when available."""
        try:
            from claude_agent_sdk.types import HookMatcher
        except ImportError:
            logger.debug("claude_agent_sdk not installed -- using plain dict hooks (will not fire)")
            return {
                "PreToolUse": [
                    {"matcher": None, "hooks": [self.pre_tool_use]},
                ],
                "PostToolUse": [
                    {"matcher": None, "hooks": [self.post_tool_use]},
                ],
            }

        return {
            "PreToolUse": [
                HookMatcher(matcher=None, hooks=[self.pre_tool_use]),
            ],
            "PostToolUse": [
                HookMatcher(matcher=None, hooks=[self.post_tool_use]),
            ],
        }


__all__ = ["StackHooks"]
