"""Verbose output formatting for shepherd task execution.

This module provides real-time console output that mirrors what Claude Code CLI
displays during execution. It uses an observer pattern where VerboseFormatter
observes the execution stream without affecting it.

There are two sources of verbose output:
1. Streaming deltas (from StreamEvent objects) - real-time character-by-character
2. Complete effects (from EffectLayer) - tool calls, task lifecycle, etc.

Usage:
    from shepherd_providers import ClaudeProvider
    from shepherd_providers.verbose import VerboseConfig

    # Enable verbose output
    provider = ClaudeProvider(
        verbose=VerboseConfig(enabled=True)
    )

    # From environment variables
    provider = ClaudeProvider(
        verbose=VerboseConfig.from_env()
    )

    # Customized output
    provider = ClaudeProvider(
        verbose=VerboseConfig(
            enabled=True,
            show_prompts=True,
            show_tool_results=True,
            thinking_style="prefix",
        )
    )

Note on thread safety:
    Each task execution creates its own VerboseFormatter instance. However,
    the output stream (sys.stdout by default) is shared. If multiple tasks
    run concurrently with verbose mode enabled, their output will interleave.
    This is expected behavior for CLI tools which typically don't parallelize.
    Use separate output streams if isolated output is required.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from shepherd_core import Effect


class VerboseConfig(BaseModel):
    """Configuration for verbose output during task execution.

    Controls what information is displayed and how it's formatted
    when running tasks with verbose mode enabled.

    Attributes:
        enabled: Master switch for verbose output (default: False)
        stream_partial: Use SDK partial messages for real-time streaming.
            When True, text and thinking appear character-by-character.
            When False, complete blocks are shown when finished.
        show_thinking: Display agent thinking/reasoning (dimmed)
        show_text: Display assistant text responses
        show_tool_calls: Display tool invocations with inputs
        show_tool_results: Display tool results (can be verbose)
        show_tool_input_streaming: Stream tool input JSON as it arrives
        show_prompts: Display system/user prompts sent to model
        show_task_lifecycle: Display TaskStarted/Completed effects
        show_context_info: Display context_id attribution on effects
        show_artifacts: Display artifact collection results
        show_cost: Display cost information from ResultMessage
        output: Stream to write output to (default: sys.stdout)
        use_color: Use ANSI color codes for formatting
        use_emoji: Use emoji in output (disable for limited terminals)
        thinking_style: How to display thinking ("dim", "prefix", "hidden")
        auto_debug_on_failure: Auto-print debug_summary() when a task fails (default: True)

    Example:
        # Minimal verbose output
        config = VerboseConfig(enabled=True, show_tool_results=False)

        # Full debugging output
        config = VerboseConfig(
            enabled=True,
            show_prompts=True,
            show_tool_results=True,
            show_context_info=True,
        )

        # From environment variables
        config = VerboseConfig.from_env()
    """

    model_config = {"arbitrary_types_allowed": True}

    enabled: bool = False
    stream_partial: bool = True
    show_thinking: bool = True
    show_text: bool = True
    show_tool_calls: bool = True
    show_tool_results: bool = False
    show_tool_input_streaming: bool = False
    show_prompts: bool = False
    show_task_lifecycle: bool = True
    show_lifecycle_phases: bool = True
    show_context_info: bool = False
    show_artifacts: bool = True
    show_cost: bool = True
    show_profile: bool = False
    output: Any = Field(default_factory=lambda: sys.stdout)  # TextIO
    use_color: bool = True
    use_emoji: bool = True
    thinking_style: Literal["dim", "prefix", "hidden"] = "dim"
    auto_debug_on_failure: bool = True  # Auto-print debug_summary() on task failure

    @classmethod
    def from_env(cls) -> VerboseConfig:
        """Create VerboseConfig from environment variables.

        Supports:
            SHEPHERD_VERBOSE: Enable verbose mode ("1", "true", "yes")
            SHEPHERD_STREAM: Enable streaming ("1", "true", default: "1")
            NO_COLOR: Disable colors (standard convention)
            SHEPHERD_NO_EMOJI: Disable emoji output

        Returns:
            VerboseConfig configured from environment
        """

        def is_truthy(val: str | None) -> bool:
            return (val or "").lower() in ("1", "true", "yes")

        return cls(
            enabled=is_truthy(os.environ.get("SHEPHERD_VERBOSE")),
            stream_partial=is_truthy(os.environ.get("SHEPHERD_STREAM", "1")),
            use_color=sys.stdout.isatty() and "NO_COLOR" not in os.environ,
            use_emoji="SHEPHERD_NO_EMOJI" not in os.environ,
        )


class VerboseFormatter:
    """Formats and prints execution events to console.

    Handles both streaming deltas (from StreamEvent objects) and
    complete effects (from EffectLayer). Manages output state to properly
    handle newlines and block transitions.

    The formatter tracks block indices to handle multiple concurrent blocks
    (e.g., thinking at index 0, text at index 1). Block tracking state is
    owned by the formatter, not the provider.

    Example:
        formatter = VerboseFormatter(VerboseConfig(enabled=True))

        # For streaming deltas
        formatter.on_thinking_delta("Let me think...")
        formatter.on_text_delta("Hello, ")
        formatter.on_text_delta("world!")

        # For complete effects
        formatter.on_effect(ToolCallStarted(tool_name="Read", ...))

        # Always call finalize in a finally block
        formatter.finalize()
    """

    # ANSI escape codes
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    GRAY = "\033[90m"
    MAGENTA = "\033[35m"

    def __init__(self, config: VerboseConfig, *, effects_stream: Any | None = None):
        self.config = config
        self._effects_stream = effects_stream
        # Block tracking state - which block type is active at which index
        self._current_blocks: dict[int, str] = {}
        # Currently streaming block type (for output formatting)
        self._active_block: tuple[str, int] | None = None  # (block_type, index)
        self._needs_newline: bool = False

    def _write(self, text: str, end: str = "") -> None:
        """Write to output stream."""
        print(text, end=end, file=self.config.output, flush=True)

    def _color(self, code: str) -> str:
        """Return color code if colors enabled, empty string otherwise."""
        return code if self.config.use_color else ""

    def _emoji(self, emoji: str, fallback: str = "") -> str:
        """Return emoji if enabled, fallback otherwise."""
        return emoji if self.config.use_emoji else fallback

    def _ensure_newline(self) -> None:
        """Ensure we're on a new line."""
        if self._needs_newline:
            self._write("\n")
            self._needs_newline = False

    def _end_current_block(self) -> None:
        """End the current streaming block if any."""
        if self._active_block:
            self._write(self._color(self.RESET))
            self._ensure_newline()
            self._active_block = None

    def _format_context_id(self, context_id: str | None) -> str:
        """Format context_id for display (shortened)."""
        if not context_id or not self.config.show_context_info:
            return ""
        # Show abbreviated context: "workspace:/repo/foo:abc123" -> "(workspace:abc123)"
        parts = context_id.split(":")
        if len(parts) >= 3:
            return f" {self._color(self.GRAY)}({parts[0]}:{parts[-1][:8]}){self._color(self.RESET)}"
        return f" {self._color(self.GRAY)}({context_id[:20]}){self._color(self.RESET)}"

    # -------------------------------------------------------------------------
    # Block Tracking (for StreamEvent processing)
    # -------------------------------------------------------------------------

    def on_block_start(self, block_type: str, block_index: int) -> None:
        """Track the start of a content block.

        Args:
            block_type: Type of block ("text", "thinking", "tool_use")
            block_index: Index of the block in the message
        """
        self._current_blocks[block_index] = block_type

    def on_block_stop(self, block_index: int | None = None) -> None:
        """Handle end of a content block.

        Args:
            block_index: Index of the block that stopped (optional)
        """
        if block_index is not None and block_index in self._current_blocks:
            del self._current_blocks[block_index]
        self._end_current_block()

    # -------------------------------------------------------------------------
    # Streaming Delta Handlers (for include_partial_messages mode)
    # -------------------------------------------------------------------------

    def on_thinking_delta(self, text: str, block_index: int = 0) -> None:
        """Handle incremental thinking content."""
        if not self.config.show_thinking or self.config.thinking_style == "hidden":
            return

        if self._active_block != ("thinking", block_index):
            self._end_current_block()
            if self.config.thinking_style == "prefix":
                self._write(f"{self._color(self.DIM)}{self._emoji('', '[thinking] ')}")
            elif self.config.thinking_style == "dim":
                self._write(self._color(self.DIM))
            self._active_block = ("thinking", block_index)

        self._write(text)
        self._needs_newline = not text.endswith("\n")

    def on_text_delta(self, text: str, block_index: int = 0) -> None:
        """Handle incremental text content."""
        if not self.config.show_text:
            return

        if self._active_block != ("text", block_index):
            self._end_current_block()
            self._active_block = ("text", block_index)

        self._write(text)
        self._needs_newline = not text.endswith("\n")

    def on_tool_input_delta(self, partial_json: str, block_index: int = 0) -> None:
        """Handle incremental tool input JSON.

        Only displayed when show_tool_input_streaming is enabled.
        """
        if not self.config.show_tool_input_streaming:
            return

        if self._active_block != ("tool_input", block_index):
            self._end_current_block()
            self._write(f"{self._color(self.GRAY)}")
            self._active_block = ("tool_input", block_index)

        self._write(partial_json)
        self._needs_newline = not partial_json.endswith("\n")

    def finalize(self) -> None:
        """Ensure terminal is in clean state after execution.

        Call this in a finally block to handle interrupted streams
        and ensure ANSI codes are properly reset. If show_profile is
        enabled and an effects stream is available, renders the profile.
        """
        self._end_current_block()
        if self._needs_newline:
            self._write("\n")
            self._needs_newline = False
        # Clear block tracking
        self._current_blocks.clear()

        # Render profile if enabled and stream available
        if self.config.show_profile and self._effects_stream is not None:
            try:
                from shepherd_core.effects.formatters import format_profile

                summary = self._effects_stream.profile().summarize()
                self._write("\n" + format_profile(summary) + "\n")
            except Exception:  # noqa: BLE001, S110
                pass

    # -------------------------------------------------------------------------
    # Effect Handlers (for complete effects from EffectLayer)
    # -------------------------------------------------------------------------

    def on_task_started(self, task_name: str, task_id: str, inputs_summary: str) -> None:
        """Handle TaskStarted effect."""
        if not self.config.show_task_lifecycle:
            return
        self._end_current_block()
        self._write(f"\n{self._color(self.BOLD)}[Task: {task_name}]{self._color(self.RESET)}\n")
        if inputs_summary:
            self._write(f"{self._emoji('', 'IN: ')}{inputs_summary}\n")
        self._write("\n")

    def on_task_completed(
        self,
        task_name: str,
        outputs_summary: str,
        cost_usd: float | None = None,
    ) -> None:
        """Handle TaskCompleted effect."""
        if not self.config.show_task_lifecycle:
            return
        self._end_current_block()
        self._write(
            f"\n{self._color(self.GREEN)}{self._emoji('✅ ', '[OK] ')}{task_name} completed{self._color(self.RESET)}\n"
        )
        if outputs_summary:
            self._write(f"{self._emoji('📤 ', 'OUT: ')}{outputs_summary}\n")
        if cost_usd is not None and self.config.show_cost:
            self._write(f"{self._emoji('', 'COST: ')}${cost_usd:.4f}\n")

    def on_task_failed(self, task_name: str, error_type: str, error_message: str) -> None:
        """Handle TaskFailed effect."""
        self._end_current_block()
        self._write(
            f"\n{self._color(self.RED)}{self._emoji('', '[FAIL] ')}"
            f"{task_name} failed ({error_type}): "
            f"{error_message}{self._color(self.RESET)}\n"
        )

    def on_tool_call_started(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context_id: str | None = None,
    ) -> None:
        """Handle ToolCallStarted effect."""
        if not self.config.show_tool_calls:
            return
        self._end_current_block()

        context_suffix = self._format_context_id(context_id)
        self._write(
            f"\n{self._color(self.CYAN)}{self._emoji('', '> ')}{tool_name}{self._color(self.RESET)}{context_suffix}\n"
        )

        # Show key inputs (abbreviated)
        for key, value in tool_input.items():
            value_str = str(value)
            if len(value_str) > 60:
                value_str = value_str[:57] + "..."
            self._write(f"   {key}: {value_str}\n")

    def on_tool_call_completed(
        self,
        tool_name: str,
        result: str | None,
        is_error: bool,
    ) -> None:
        """Handle ToolCallCompleted effect."""
        if not self.config.show_tool_results:
            return
        self._end_current_block()

        if is_error:
            self._write(f"   {self._color(self.RED)}error: {result}{self._color(self.RESET)}\n")
        elif result:
            result_preview = result[:100] + "..." if len(result) > 100 else result
            self._write(f"   {self._emoji('', '-> ')}{result_preview}\n")

    def on_tool_call_rejected(
        self,
        tool_name: str,
        reason: str,
        required_capability: str,
    ) -> None:
        """Handle ToolCallRejected effect."""
        self._end_current_block()
        self._write(
            f"   {self._color(self.RED)}{self._emoji('', '[BLOCKED] ')}"
            f"{tool_name} rejected: {reason} "
            f"(requires: {required_capability}){self._color(self.RESET)}\n"
        )

    def on_prompt_sent(self, system_prompt: str, user_prompt: str) -> None:
        """Handle PromptSent effect."""
        if not self.config.show_prompts:
            return
        self._end_current_block()
        divider = "-" * 20
        self._write(f"\n{self._color(self.YELLOW)}{divider} System Prompt {divider}{self._color(self.RESET)}\n")
        self._write(f"{system_prompt[:500]}{'...' if len(system_prompt) > 500 else ''}\n")
        self._write(f"\n{self._color(self.YELLOW)}{divider} User Prompt {divider}{self._color(self.RESET)}\n")
        self._write(f"{user_prompt}\n")
        self._write(f"{self._color(self.YELLOW)}{divider * 2}{self._color(self.RESET)}\n\n")

    def on_thinking_complete(self, content: str) -> None:
        """Handle complete AgentThinking effect (non-streaming fallback).

        When stream_partial is True, thinking is already shown via deltas,
        so this method returns early to avoid duplicate output.
        """
        if not self.config.show_thinking or self.config.stream_partial:
            return  # Already shown via deltas
        if self.config.thinking_style == "hidden":
            return
        self._end_current_block()
        prefix = self._emoji("", "[thinking] ") if self.config.thinking_style == "prefix" else ""
        self._write(f"{self._color(self.DIM)}{prefix}{content}{self._color(self.RESET)}\n")

    def on_text_complete(self, content: str) -> None:
        """Handle complete AgentMessage effect (non-streaming fallback).

        When stream_partial is True, text is already shown via deltas,
        so this method returns early to avoid duplicate output.
        """
        if not self.config.show_text or self.config.stream_partial:
            return  # Already shown via deltas
        self._end_current_block()
        self._write(f"{content}\n")

    def on_artifact_written(
        self,
        filename: str,
        content_length: int,
        context_id: str | None = None,
    ) -> None:
        """Handle ArtifactWritten effect."""
        if not self.config.show_artifacts:
            return
        self._end_current_block()
        context_suffix = self._format_context_id(context_id)
        self._write(
            f"{self._color(self.MAGENTA)}{self._emoji('', '[ARTIFACT] ')}"
            f"Artifact: {filename} ({content_length} bytes)"
            f"{self._color(self.RESET)}{context_suffix}\n"
        )

    def on_artifact_missing(
        self,
        filename: str,
        required: bool,
        context_id: str | None = None,
    ) -> None:
        """Handle ArtifactMissing effect."""
        if not self.config.show_artifacts:
            return
        self._end_current_block()
        context_suffix = self._format_context_id(context_id)
        level = "Missing required" if required else "Optional artifact not found"
        color = self.RED if required else self.YELLOW
        emoji = self._emoji("", "[WARN] ")
        self._write(f"{self._color(color)}{emoji}{level}: {filename}{self._color(self.RESET)}{context_suffix}\n")

    def on_file_created(self, path: str, context_id: str | None = None) -> None:
        """Handle FileCreate effect."""
        if not self.config.show_tool_results:
            return
        self._end_current_block()
        context_suffix = self._format_context_id(context_id)
        self._write(f"   {self._emoji('', '[FILE] ')}Created: {path}{context_suffix}\n")

    def on_file_patched(self, path: str, context_id: str | None = None) -> None:
        """Handle FilePatch effect."""
        if not self.config.show_tool_results:
            return
        self._end_current_block()
        context_suffix = self._format_context_id(context_id)
        self._write(f"   {self._emoji('', '[EDIT] ')}Modified: {path}{context_suffix}\n")

    # -------------------------------------------------------------------------
    # Lifecycle Phase Handlers
    # -------------------------------------------------------------------------

    def on_lifecycle_phase_started(
        self,
        phase: str,
        context_count: int = 0,
    ) -> None:
        """Handle LifecyclePhaseStarted effect.

        Shows the start of a lifecycle phase (configure, prepare, execute,
        capture, cleanup) with an appropriate emoji.
        """
        if not self.config.show_lifecycle_phases:
            return
        self._end_current_block()

        # Phase-specific emoji mapping
        phase_emoji = {
            "configure": ("", "[CFG] "),
            "prepare": ("", "[PREP] "),
            "execute": ("", "[EXEC] "),
            "capture": ("", "[CAP] "),
            "cleanup": ("", "[CLN] "),
        }
        emoji, fallback = phase_emoji.get(phase, ("", f"[{phase.upper()}] "))

        ctx_info = f" ({context_count} ctx)" if context_count > 0 else ""
        self._write(
            f"   {self._color(self.GRAY)}{self._emoji(emoji, fallback)}"
            f"{phase.capitalize()}{ctx_info}...{self._color(self.RESET)}\n"
        )

    def on_lifecycle_phase_completed(
        self,
        phase: str,
        duration_ms: float = 0.0,
    ) -> None:
        """Handle LifecyclePhaseCompleted effect.

        Shows completion of a lifecycle phase with timing information.
        """
        if not self.config.show_lifecycle_phases:
            return
        self._end_current_block()

        duration_str = f"{duration_ms:.0f}ms" if duration_ms > 0 else ""
        self._write(
            f"   {self._color(self.GREEN)}{self._emoji('', '[OK] ')}"
            f"{phase.capitalize()} done"
            f"{f' ({duration_str})' if duration_str else ''}"
            f"{self._color(self.RESET)}\n"
        )

    # -------------------------------------------------------------------------
    # Generic Effect Handler
    # -------------------------------------------------------------------------

    def on_effect(self, effect: Effect) -> None:
        """Route an effect to the appropriate handler.

        This is the main entry point for processing effects from
        the provider's execute_streaming() method.
        """
        # Import here to avoid circular imports
        from shepherd_core import (
            AgentMessage,
            AgentThinking,
            ArtifactMissing,
            ArtifactWritten,
            FileCreate,
            FilePatch,
            LifecyclePhaseCompleted,
            LifecyclePhaseStarted,
            PromptSent,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
            ToolCallCompleted,
            ToolCallRejected,
            ToolCallStarted,
        )

        if isinstance(effect, TaskStarted):
            # Format inputs dict as summary string
            inputs_str = ", ".join(f'{k}="{v}"' for k, v in effect.inputs.items())
            self.on_task_started(effect.task_name or "", "", inputs_str)
        elif isinstance(effect, TaskCompleted):
            # Note: cost_usd comes from ResultMessage, not TaskCompleted
            # Format outputs dict as summary string
            outputs_str = ", ".join(f'{k}="{v}"' for k, v in effect.outputs.items())
            self.on_task_completed(effect.task_name or "", outputs_str)
        elif isinstance(effect, TaskFailed):
            self.on_task_failed(effect.task_name or "", effect.error_type, effect.error)
        elif isinstance(effect, ToolCallStarted):
            self.on_tool_call_started(
                effect.tool_name,
                effect.params,
                getattr(effect, "context_id", None),
            )
        elif isinstance(effect, ToolCallCompleted):
            self.on_tool_call_completed(effect.tool_name, effect.output_preview, not effect.success)
        elif isinstance(effect, ToolCallRejected):
            self.on_tool_call_rejected(
                effect.tool_name,
                effect.reason,
                effect.rejected_by,
            )
        elif isinstance(effect, PromptSent):
            self.on_prompt_sent(effect.system_prompt_preview, effect.user_prompt_preview)
        elif isinstance(effect, AgentThinking):
            self.on_thinking_complete(effect.content)
        elif isinstance(effect, AgentMessage):
            self.on_text_complete(effect.content)
        elif isinstance(effect, ArtifactWritten):
            self.on_artifact_written(
                effect.filename,
                effect.size_bytes,
                getattr(effect, "context_id", None),
            )
        elif isinstance(effect, ArtifactMissing):
            self.on_artifact_missing(
                effect.filename,
                effect.required,
                getattr(effect, "context_id", None),
            )
        elif isinstance(effect, FileCreate):
            self.on_file_created(
                effect.path,
                getattr(effect, "context_id", None),
            )
        elif isinstance(effect, FilePatch):
            self.on_file_patched(
                effect.path,
                getattr(effect, "context_id", None),
            )
        elif isinstance(effect, LifecyclePhaseStarted):
            self.on_lifecycle_phase_started(
                effect.phase,
                effect.context_count,
            )
        elif isinstance(effect, LifecyclePhaseCompleted):
            self.on_lifecycle_phase_completed(
                effect.phase,
                effect.duration_ms,
            )


__all__ = [
    "VerboseConfig",
    "VerboseFormatter",
]
