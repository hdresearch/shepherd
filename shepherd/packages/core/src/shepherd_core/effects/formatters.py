"""Effect stream formatters for various output formats.

This module provides formatters for converting effect streams to different formats:
- MarkdownFormatter: Structured markdown for LLM consumption and documentation
- CompactFormatter: Single-line-per-effect, grep-friendly logs
- JSONFormatter: Structured data for programmatic consumption
- TreeFormatter: Hierarchical view showing causality relationships

Example:
    # Format as markdown
    print(stream.to_markdown())

    # Format as compact log
    print(stream.to_compact())

    # Format as causality tree
    print(stream.to_tree())
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from shepherd_core.effects import Effect
    from shepherd_core.effects.views import CausalityNode, ProfileSummary
    from shepherd_core.scope.stream import Stream


# =============================================================================
# Formatter Options
# =============================================================================


@dataclass
class FormatterOptions:
    """Common options for all formatters.

    Timestamp handling:
    - Timestamps are extracted from `Effect.timestamp` (float epoch)
    - If `include_timestamps=True` and timestamp is None, shows "---"
    - If `relative_timestamps=True`, shows offset from first effect's timestamp
    """

    max_effects: int | None = None  # Truncate after N effects
    include_types: set[type[Effect]] | None = None
    exclude_types: set[type[Effect]] | None = None
    include_metadata: bool = False  # Include sequence, scope_id, etc.
    include_timestamps: bool = True  # Show timestamps in output
    relative_timestamps: bool = True  # Relative to first effect (e.g., "+1.23s")
    truncate_content: int = 200  # Max chars for content fields


# =============================================================================
# Effect Formatter Base
# =============================================================================


class EffectFormatter(ABC):
    """Base class for effect stream formatters."""

    @abstractmethod
    def format_stream(self, stream: Stream, options: FormatterOptions | None = None) -> str:
        """Format an entire stream."""
        ...

    @abstractmethod
    def format_effect(self, effect: Effect, options: FormatterOptions | None = None) -> str:
        """Format a single effect."""
        ...


# =============================================================================
# Markdown Formatter
# =============================================================================


class MarkdownFormatter(EffectFormatter):
    """Format stream as structured markdown.

    Output structure:

    ## Execution Summary: {TaskName}
    **Status**: Completed / Failed
    **Duration**: 1.23s
    **Tool Calls**: 5
    **Total Effects**: 17

    ### Timeline
    | # | Event | Details |
    |---|-------|---------|
    | 0 | Task Started | Task: FixBug |
    | 1 | Agent Thinking | "I need to first understand..." |
    | 2 | Tool Call Started | `read_file` (path=src/auth.py) |
    ...

    ### Files Accessed
    **Read**: `src/auth.py`, `src/utils.py`
    **Modified**: `src/auth.py`
    **Created**: `tests/test_auth.py`

    ### Errors (if any)
    **Type**: TypeError
    **Message**: ...
    **Location**: line 42
    **Suggestions**: ...

    ### Agent Reasoning
    > First, I'll read the file to understand...
    > I found the issue on line 42...

    Note: Format validated for LLM comprehension in spike_formatter_llm_comprehension.py.
    All ground truth questions (tools, files, outcome, etc.) extractable from output.
    """

    def format_stream(self, stream: Stream, options: FormatterOptions | None = None) -> str:
        """Format an entire stream as markdown."""
        from shepherd_core.effects import (
            AgentMessage,
            AgentThinking,
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
            ToolCallCompleted,
        )

        opts = options or FormatterOptions()

        if len(stream) == 0:
            return "## Execution Summary\n\n**Status**: No effects recorded"

        # Extract key info
        task_name = "Unknown"
        task_started = stream.first(TaskStarted)
        if task_started:
            task_name = task_started.effect.task_name or "Unknown"

        # Determine outcome
        task_failed = stream.first(TaskFailed)
        task_completed = stream.first(TaskCompleted)

        if task_failed:
            status = "Failed"
        elif task_completed:
            status = "Completed"
        else:
            status = "In Progress"

        # Count tool calls
        tool_call_count = stream.count(ToolCallCompleted)

        # Get duration
        duration_str = "---"
        if task_completed:
            duration_ms = task_completed.effect.duration_ms  # type: ignore[attr-defined]
            if duration_ms:
                duration_str = f"{duration_ms / 1000:.2f}s"

        # Build sections
        sections: list[str] = []

        # Header
        sections.append(f"## Execution Summary: {task_name}")
        sections.append("")
        sections.append(f"**Status**: {status}")
        sections.append(f"**Duration**: {duration_str}")
        sections.append(f"**Tool Calls**: {tool_call_count}")
        sections.append(f"**Total Effects**: {len(stream)}")
        sections.append("")

        # Timeline
        sections.append("### Timeline")
        sections.append("")
        sections.append("| # | Event | Details |")
        sections.append("|---|-------|---------|")

        base_time = stream[0].effect.timestamp if len(stream) > 0 else 0
        effect_count = 0

        for layer in stream:
            if opts.max_effects and effect_count >= opts.max_effects:
                sections.append(f"| ... | *{len(stream) - effect_count} more effects* | |")
                break

            if opts.include_types and type(layer.effect) not in opts.include_types:
                continue
            if opts.exclude_types and type(layer.effect) in opts.exclude_types:
                continue

            effect = layer.effect
            event_name = effect.effect_type.replace("_", " ").title()
            details = self._format_effect_details(effect, opts)

            # Add relative timestamp if requested
            if opts.include_timestamps and opts.relative_timestamps:
                rel_time = effect.timestamp - base_time
                event_name = f"+{rel_time:.2f}s {event_name}"

            sections.append(f"| {layer.sequence} | {event_name} | {details} |")
            effect_count += 1

        sections.append("")

        # Files Accessed
        files_read: set[str] = set()
        files_modified: set[str] = set()
        files_created: set[str] = set()
        files_deleted: set[str] = set()

        for layer in stream:
            effect = layer.effect
            if isinstance(effect, FileRead):
                files_read.add(effect.path)
            elif isinstance(effect, FilePatch):
                files_modified.add(effect.path)
            elif isinstance(effect, FileCreate):
                files_created.add(effect.path)
            elif isinstance(effect, FileDelete):
                files_deleted.add(effect.path)

        if files_read or files_modified or files_created or files_deleted:
            sections.append("### Files Accessed")
            sections.append("")
            if files_read:
                sections.append(f"**Read**: {', '.join(f'`{f}`' for f in sorted(files_read))}")
            if files_modified:
                sections.append(f"**Modified**: {', '.join(f'`{f}`' for f in sorted(files_modified))}")
            if files_created:
                sections.append(f"**Created**: {', '.join(f'`{f}`' for f in sorted(files_created))}")
            if files_deleted:
                sections.append(f"**Deleted**: {', '.join(f'`{f}`' for f in sorted(files_deleted))}")
            sections.append("")

        # Errors
        if task_failed:
            failed_effect = task_failed.effect
            sections.append("### Errors")
            sections.append("")
            sections.append(f"**Type**: {failed_effect.error_type}")  # type: ignore[attr-defined]
            sections.append(f"**Message**: {failed_effect.error}")  # type: ignore[attr-defined]
            if failed_effect.error_location:  # type: ignore[attr-defined]
                sections.append(f"**Location**: {failed_effect.error_location}")  # type: ignore[attr-defined]
            if failed_effect.suggestions:  # type: ignore[attr-defined]
                sections.append(f"**Suggestions**: {', '.join(failed_effect.suggestions)}")  # type: ignore[attr-defined]
            sections.append("")

        # Agent Reasoning
        thinking_content: list[str] = []
        for layer in stream:
            effect = layer.effect
            if isinstance(effect, AgentThinking):
                content = effect.content[: opts.truncate_content]
                if len(effect.content) > opts.truncate_content:
                    content += "..."
                thinking_content.append(content)
            elif isinstance(effect, AgentMessage):
                content = effect.content[: opts.truncate_content]
                if len(effect.content) > opts.truncate_content:
                    content += "..."
                thinking_content.append(f"[Message] {content}")

        if thinking_content:
            sections.append("### Agent Reasoning")
            sections.append("")
            for content in thinking_content[:10]:  # Limit to 10 entries
                # Escape content for markdown quote
                escaped = content.replace("\n", " ")
                sections.append(f"> {escaped}")
            if len(thinking_content) > 10:
                sections.append(f"> *... {len(thinking_content) - 10} more*")
            sections.append("")

        return "\n".join(sections)

    def _format_effect_details(self, effect: Effect, opts: FormatterOptions) -> str:
        """Format effect-specific details for the timeline table."""
        from shepherd_core.effects import (
            AgentMessage,
            AgentThinking,
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
            ToolCallCompleted,
            ToolCallRejected,
            ToolCallStarted,
        )

        if isinstance(effect, TaskStarted):
            return f"Task: {effect.task_name}"
        if isinstance(effect, TaskCompleted):
            return f"Duration: {effect.duration_ms:.0f}ms"
        if isinstance(effect, TaskFailed):
            return f"{effect.error_type}: {effect.error[:50]}..."
        if isinstance(effect, ToolCallStarted):
            params_str = ", ".join(f"{k}={v}" for k, v in list(effect.params.items())[:3])
            return f"`{effect.tool_name}` ({params_str})"
        if isinstance(effect, ToolCallCompleted):
            status = "success" if effect.success else "failed"
            return f"`{effect.tool_name}` ({status})"
        if isinstance(effect, ToolCallRejected):
            return f"`{effect.tool_name}` rejected: {effect.reason[:50]}"
        if isinstance(effect, FileRead):
            return f"`{effect.path}`"
        if isinstance(effect, FilePatch):
            return f"`{effect.path}` modified"
        if isinstance(effect, FileCreate):
            return f"`{effect.path}` created"
        if isinstance(effect, FileDelete):
            return f"`{effect.path}` deleted"
        if isinstance(effect, (AgentThinking, AgentMessage)):
            content = effect.content[:50].replace("\n", " ")
            return f'"{content}..."'
        return effect.effect_type

    def format_effect(self, effect: Effect, options: FormatterOptions | None = None) -> str:
        """Format a single effect as markdown."""
        opts = options or FormatterOptions()
        details = self._format_effect_details(effect, opts)
        return f"**{effect.effect_type}**: {details}"


# =============================================================================
# Compact Formatter
# =============================================================================


class CompactFormatter(EffectFormatter):
    """One line per effect, grep-friendly.

    Output:
    [0.000] TaskStarted task=FixBug
    [0.123] ToolCallStarted tool=read_file path=src/auth.py
    [0.456] ToolCallCompleted tool=read_file success=true
    [1.234] TaskCompleted task=FixBug duration=1234ms
    """

    def format_stream(self, stream: Stream, options: FormatterOptions | None = None) -> str:
        """Format stream as compact log lines."""
        opts = options or FormatterOptions()

        if len(stream) == 0:
            return "# No effects recorded"

        lines: list[str] = []
        base_time = stream[0].effect.timestamp
        effect_count = 0

        for layer in stream:
            if opts.max_effects and effect_count >= opts.max_effects:
                lines.append(f"# ... {len(stream) - effect_count} more effects")
                break

            if opts.include_types and type(layer.effect) not in opts.include_types:
                continue
            if opts.exclude_types and type(layer.effect) in opts.exclude_types:
                continue

            line = self.format_effect(layer.effect, opts, base_time=base_time)
            if opts.include_metadata:
                line = f"#{layer.sequence:03d} {line}"
            lines.append(line)
            effect_count += 1

        return "\n".join(lines)

    def format_effect(
        self,
        effect: Effect,
        options: FormatterOptions | None = None,
        base_time: float | None = None,
    ) -> str:
        """Format a single effect as compact line."""
        from shepherd_core.effects import (
            AgentMessage,
            AgentThinking,
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
            ToolCallCompleted,
            ToolCallRejected,
            ToolCallStarted,
        )

        opts = options or FormatterOptions()

        # Timestamp
        if opts.include_timestamps:
            if base_time is not None and opts.relative_timestamps:
                rel_time = effect.timestamp - base_time
                timestamp = f"[{rel_time:07.3f}]"
            else:
                timestamp = f"[{effect.timestamp:.3f}]"
        else:
            timestamp = ""

        # Effect type (PascalCase)
        effect_name = effect.effect_type.replace("_", " ").title().replace(" ", "")

        # Key-value pairs based on effect type
        pairs: list[str] = []

        if isinstance(effect, TaskStarted):
            pairs.append(f"task={effect.task_name}")
        elif isinstance(effect, TaskCompleted):
            pairs.append(f"task={effect.task_name}")
            pairs.append(f"duration={effect.duration_ms:.0f}ms")
        elif isinstance(effect, TaskFailed):
            pairs.append(f"task={effect.task_name}")
            pairs.append(f"error={effect.error_type}")
        elif isinstance(effect, ToolCallStarted):
            pairs.append(f"tool={effect.tool_name}")
            for k, v in list(effect.params.items())[:2]:
                pairs.append(f"{k}={v}")
        elif isinstance(effect, ToolCallCompleted):
            pairs.append(f"tool={effect.tool_name}")
            pairs.append(f"success={str(effect.success).lower()}")
        elif isinstance(effect, ToolCallRejected):
            pairs.append(f"tool={effect.tool_name}")
            pairs.append(f"reason={effect.reason[:30]}")
        elif isinstance(effect, (FileRead, FilePatch, FileCreate, FileDelete)):
            pairs.append(f"path={effect.path}")
        elif isinstance(effect, (AgentThinking, AgentMessage)):
            content = effect.content[:30].replace("\n", " ")
            pairs.append(f'content="{content}..."')

        pairs_str = " ".join(pairs)
        return f"{timestamp} {effect_name} {pairs_str}".strip()


# =============================================================================
# JSON Formatter
# =============================================================================


class JSONFormatter(EffectFormatter):
    """JSON output with configurable verbosity.

    Modes:
    - "full": Complete effect data
    - "compact": Essential fields only
    - "summary": Aggregated statistics
    """

    def __init__(self, mode: Literal["full", "compact", "summary"] = "compact"):
        self.mode = mode

    def format_stream(self, stream: Stream, options: FormatterOptions | None = None) -> str:
        """Format stream as JSON."""
        opts = options or FormatterOptions()

        if self.mode == "summary":
            return self._format_summary(stream, opts)
        if self.mode == "compact":
            return self._format_compact(stream, opts)
        return self._format_full(stream, opts)

    def _format_full(self, stream: Stream, opts: FormatterOptions) -> str:
        """Full effect data."""
        return stream.to_json(indent=2)

    def _format_compact(self, stream: Stream, opts: FormatterOptions) -> str:
        """Essential fields only."""
        from shepherd_core.effects import (
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
            ToolCallCompleted,
            ToolCallStarted,
        )

        effects: list[dict[str, Any]] = []
        effect_count = 0

        for layer in stream:
            if opts.max_effects and effect_count >= opts.max_effects:
                break

            if opts.include_types and type(layer.effect) not in opts.include_types:
                continue
            if opts.exclude_types and type(layer.effect) in opts.exclude_types:
                continue

            effect = layer.effect
            compact: dict[str, Any] = {
                "type": effect.effect_type,
                "seq": layer.sequence,
            }

            # Add key fields based on type
            if isinstance(effect, TaskStarted):
                compact["task"] = effect.task_name
            elif isinstance(effect, TaskCompleted):
                compact["task"] = effect.task_name
                compact["duration_ms"] = effect.duration_ms
            elif isinstance(effect, TaskFailed):
                compact["task"] = effect.task_name
                compact["error"] = effect.error_type
            elif isinstance(effect, ToolCallStarted):
                compact["tool"] = effect.tool_name
                compact["tool_call_id"] = effect.tool_call_id
            elif isinstance(effect, ToolCallCompleted):
                compact["tool"] = effect.tool_name
                compact["success"] = effect.success
            elif isinstance(effect, (FileRead, FilePatch, FileCreate, FileDelete)):
                compact["path"] = effect.path

            effects.append(compact)
            effect_count += 1

        return json.dumps(effects, indent=2)

    def _format_summary(self, stream: Stream, opts: FormatterOptions) -> str:
        """Aggregated statistics."""
        from shepherd_core.effects import (
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            TaskCompleted,
            TaskFailed,
            ToolCallCompleted,
            ToolCallRejected,
        )

        summary: dict[str, Any] = {
            "total_effects": len(stream),
            "effect_types": {},
            "tool_calls": 0,
            "tool_calls_rejected": 0,
            "files_read": [],
            "files_modified": [],
            "files_created": [],
            "files_deleted": [],
            "succeeded": False,
            "failed": False,
            "duration_ms": None,
        }

        # Count effect types
        type_counts: dict[str, int] = {}
        files_read: set[str] = set()
        files_modified: set[str] = set()
        files_created: set[str] = set()
        files_deleted: set[str] = set()

        for layer in stream:
            effect = layer.effect
            effect_type = effect.effect_type
            type_counts[effect_type] = type_counts.get(effect_type, 0) + 1

            if isinstance(effect, ToolCallCompleted):
                summary["tool_calls"] += 1
            elif isinstance(effect, ToolCallRejected):
                summary["tool_calls_rejected"] += 1
            elif isinstance(effect, FileRead):
                files_read.add(effect.path)
            elif isinstance(effect, FilePatch):
                files_modified.add(effect.path)
            elif isinstance(effect, FileCreate):
                files_created.add(effect.path)
            elif isinstance(effect, FileDelete):
                files_deleted.add(effect.path)
            elif isinstance(effect, TaskCompleted):
                summary["succeeded"] = True
                summary["duration_ms"] = effect.duration_ms
            elif isinstance(effect, TaskFailed):
                summary["failed"] = True

        summary["effect_types"] = type_counts
        summary["files_read"] = sorted(files_read)
        summary["files_modified"] = sorted(files_modified)
        summary["files_created"] = sorted(files_created)
        summary["files_deleted"] = sorted(files_deleted)

        return json.dumps(summary, indent=2)

    def format_effect(self, effect: Effect, options: FormatterOptions | None = None) -> str:
        """Format a single effect as JSON."""
        return json.dumps(effect.model_dump(), indent=2, default=str)


# =============================================================================
# Tree Formatter
# =============================================================================


class TreeFormatter(EffectFormatter):
    """Tree view showing effect relationships.

    Output:
    TaskStarted: FixBug
    +-- ToolCallStarted: read_file
    |   +-- FileRead: src/auth.py
    +-- AgentThinking: "I see the bug is..."
    +-- ToolCallStarted: edit_file
    |   +-- FilePatch: src/auth.py (+3, -1)
    +-- TaskCompleted: FixBug (1.23s)
    """

    def format_stream(self, stream: Stream, options: FormatterOptions | None = None) -> str:
        """Format stream as causality tree."""
        from shepherd_core.effects.views import CausalityTreeView

        opts = options or FormatterOptions()

        if len(stream) == 0:
            return "# No effects recorded"

        tree_view = CausalityTreeView(stream)
        roots = tree_view.as_tree()

        lines: list[str] = []
        for i, node in enumerate(roots):
            is_last = i == len(roots) - 1
            self._format_node(node, lines, "", is_last, opts)

        return "\n".join(lines)

    def _format_node(
        self,
        node: CausalityNode,
        lines: list[str],
        prefix: str,
        is_last: bool,
        opts: FormatterOptions,
    ) -> None:
        """Recursively format a node and its children."""
        # Connector (same for all nodes in this simple format)
        connector = "+-- "

        # Format the effect
        effect_str = self._format_effect_brief(node.effect, opts)
        lines.append(f"{prefix}{connector}{effect_str}")

        # Format children
        child_prefix = prefix + ("|   " if not is_last else "    ")
        for i, child in enumerate(node.children):
            child_is_last = i == len(node.children) - 1
            self._format_node(child, lines, child_prefix, child_is_last, opts)

    def _format_effect_brief(self, effect: Effect, opts: FormatterOptions) -> str:
        """Format effect as brief string for tree display."""
        from shepherd_core.effects import (
            AgentMessage,
            AgentThinking,
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
            ToolCallCompleted,
            ToolCallStarted,
        )

        effect_name = effect.effect_type.replace("_", " ").title().replace(" ", "")

        if isinstance(effect, TaskStarted):
            return f"{effect_name}: {effect.task_name}"
        if isinstance(effect, TaskCompleted):
            return f"{effect_name}: {effect.task_name} ({effect.duration_ms:.0f}ms)"
        if isinstance(effect, TaskFailed):
            return f"{effect_name}: {effect.task_name} ({effect.error_type})"
        if isinstance(effect, ToolCallStarted):
            return f"{effect_name}: {effect.tool_name}"
        if isinstance(effect, ToolCallCompleted):
            status = "ok" if effect.success else "failed"
            return f"{effect_name}: {effect.tool_name} ({status})"
        if isinstance(effect, (FileRead, FilePatch, FileCreate, FileDelete)):
            return f"{effect_name}: {effect.path}"
        if isinstance(effect, (AgentThinking, AgentMessage)):
            content = effect.content[:30].replace("\n", " ")
            return f'{effect_name}: "{content}..."'
        return effect_name

    def format_effect(self, effect: Effect, options: FormatterOptions | None = None) -> str:
        """Format a single effect as tree node."""
        opts = options or FormatterOptions()
        return self._format_effect_brief(effect, opts)


# =============================================================================
# Profile Formatter
# =============================================================================


def format_profile(
    summary: ProfileSummary,
    *,
    show_tree: bool = True,
    show_tools: bool = True,
    show_phase_detail: bool = True,
    show_bar_chart: bool = True,
    show_turn_detail: bool = True,
) -> str:
    """Render a ProfileSummary as a compact terminal dashboard.

    Target: 80-column terminal. Sections are omitted when empty.

    Args:
        summary: The ProfileSummary to render.
        show_tree: Include the Tasks tree section.
        show_tools: Include the Tools table section.
        show_phase_detail: Include per-phase overhead breakdown.
        show_bar_chart: Include ASCII bar charts on time breakdown.
        show_turn_detail: Include API wait / Tool exec / Turn overhead sub-items.
    """
    from shepherd_core.effects.views import TaskNode  # noqa: TC001

    lines: list[str] = []
    cs = summary.cost_summary
    tb = summary.time_breakdown

    # --- Header ---
    lines.append("Execution Profile")
    lines.append("\u2550" * 50)

    duration_str = f"{tb.total_ms / 1000:.2f}s" if tb.total_ms is not None else "N/A"
    cost_str = f"${cs.cost_usd:.4f}" if cs.cost_usd is not None else "N/A"
    lines.append(
        f"Duration: {duration_str}    Cost: {cost_str}    "
        f"Tokens: {cs.total_tokens:,} (in: {cs.input_tokens:,}  out: {cs.output_tokens:,})"
    )

    rejected_part = f" ({cs.tool_calls_rejected} rejected)" if cs.tool_calls_rejected else ""
    files_parts: list[str] = []
    if cs.files_read:
        files_parts.append(f"{len(cs.files_read)} read")
    if cs.files_modified:
        files_parts.append(f"{len(cs.files_modified)} modified")
    if cs.files_created:
        files_parts.append(f"{cs.files_created} created")
    files_str = ", ".join(files_parts) if files_parts else "none"
    lines.append(f"LLM calls: {cs.llm_calls}       Tool calls: {cs.tool_calls}{rejected_part}   Files: {files_str}")

    # --- Time Breakdown ---
    if tb.total_ms is not None and tb.total_ms > 0:
        lines.append("")
        lines.append("Time Breakdown")
        total = tb.total_ms

        def _bar(frac: float, width: int = 40) -> str:
            if not show_bar_chart:
                return ""
            filled = round(frac * width)
            return "  " + "\u2588" * filled

        def _pct(ms: float) -> str:
            return f"{ms / total * 100:5.1f}%"

        # Level 1: LLM invocations
        llm_frac = tb.llm_wall_ms / total if total > 0 else 0
        lines.append(f"  LLM invocations  {tb.llm_wall_ms:,.0f}ms  {_pct(tb.llm_wall_ms)}{_bar(llm_frac)}")

        if show_turn_detail:
            lines.append(f"  \u251c\u2500 API wait      {tb.llm_api_ms:,.0f}ms  {_pct(tb.llm_api_ms)}")
            lines.append(f"  \u251c\u2500 Tool exec     {tb.tool_execution_ms:,.0f}ms  {_pct(tb.tool_execution_ms)}")
            lines.append(
                f"  \u2514\u2500 Turn overhead  {tb.intra_turn_overhead_ms:,.0f}ms  {_pct(tb.intra_turn_overhead_ms)}"
            )

        # Level 1: Framework
        overhead = tb.overhead_ms or 0.0
        fw_frac = overhead / total if total > 0 else 0
        lines.append(f"  Framework        {overhead:,.0f}ms  {_pct(overhead)}{_bar(fw_frac)}")

        if show_phase_detail and tb.phase_durations:
            sorted_phases = sorted(
                ((p, d) for p, d in tb.phase_durations.items() if p != "execute"),
                key=lambda x: x[1],
                reverse=True,
            )
            for phase, dur in sorted_phases:
                lines.append(f"    {phase:<16s} {dur:,.0f}ms  {_pct(dur)}")

    # --- Models ---
    if summary.models:
        lines.append("")
        cache_ratio = summary.prompt_cache_read_ratio
        cache_part = f"Prompt cache: {cache_ratio:.0%} read" if cache_ratio is not None else ""
        lines.append(f"Models{' ' * 42}{cache_part}")
        for m in summary.models:
            cost_s = f"${m.cost_usd:.4f}" if m.cost_usd is not None else "N/A"
            lines.append(
                f"  {m.model_id}   {m.input_tokens:,} in  {m.output_tokens:,} out   {cost_s}   {m.llm_calls} calls"
            )
            cache_parts: list[str] = []
            if m.cache_read_input_tokens:
                ratio = f" ({m.cache_read_ratio:.0%})" if m.cache_read_ratio is not None else ""
                cache_parts.append(f"{m.cache_read_input_tokens:,} read{ratio}")
            if m.cache_creation_input_tokens:
                cache_parts.append(f"{m.cache_creation_input_tokens:,} created")
            detail_parts: list[str] = []
            if cache_parts:
                detail_parts.append(f"Cache: {', '.join(cache_parts)}")
            if m.total_turns:
                avg = m.total_turns / max(m.llm_calls, 1)
                detail_parts.append(f"Turns: {m.total_turns} (avg {avg:.1f})")
            if detail_parts:
                lines.append(f"    {'   '.join(detail_parts)}")

    # --- Tools ---
    if show_tools and summary.tools:
        lines.append("")
        lines.append("Tools")
        lines.append(f"  {'Name':<18s} {'Calls':>5s}  {'Success':>7s}  {'Rejected':>8s}  {'Avg ms':>6s}")
        for t in summary.tools_by_calls:
            lines.append(
                f"  {t.tool_name:<18s} {t.call_count:>5d}  {t.success_rate:>6.1%}  "
                f"{t.rejected_count:>8d}  {t.avg_duration_ms:>5.0f}ms"
            )

    # --- Errors & Recovery ---
    total_error_calls = sum(m.error_calls for m in summary.models)
    has_errors = (
        summary.recovery.execution_failures > 0 or summary.recovery.recoveries_attempted > 0 or total_error_calls > 0
    )
    if has_errors:
        lines.append("")
        lines.append("Errors & Recovery")
        r = summary.recovery
        if r.execution_failures:
            types_str = ", ".join(f"{k} ({v})" for k, v in r.failure_types.items())
            tools_str = ""
            if r.triggering_tools:
                tools_str = " triggered by " + ", ".join(f"{k} ({v})" for k, v in r.triggering_tools.items())
            lines.append(f"  {r.execution_failures} execution failure(s): {types_str}{tools_str}")
        if r.recoveries_attempted:
            strats = ", ".join(f"{k} ({v})" for k, v in r.recovery_strategies.items())
            lines.append(f"  {r.recoveries_attempted} recovery attempt(s): {strats}")
        if total_error_calls:
            lines.append(f"  {total_error_calls} LLM error call(s) (tokens still consumed)")

    # --- Task Cache ---
    tc = summary.task_cache
    if tc.hits > 0 or tc.stores > 0:
        lines.append("")
        lines.append("Task Cache")
        rate_str = f"{tc.hit_rate:.1%} hit rate" if tc.hit_rate is not None else ""
        size_str = ""
        if tc.total_stored_bytes:
            if tc.total_stored_bytes >= 1024 * 1024:
                size_str = f", {tc.total_stored_bytes / (1024 * 1024):.1f} MB stored"
            elif tc.total_stored_bytes >= 1024:
                size_str = f", {tc.total_stored_bytes / 1024:.1f} KB stored"
            else:
                size_str = f", {tc.total_stored_bytes} B stored"
        lines.append(f"  {tc.hits} hits, {tc.stores} stores ({rate_str}{size_str})")

    # --- Tasks Tree ---
    if show_tree and summary.task_tree:
        lines.append("")
        lines.append("Tasks")

        def _render_node(node: TaskNode, prefix: str, is_last: bool) -> None:
            p = node.profile
            connector = "\u2514\u2500 " if is_last else "\u251c\u2500 "
            dur_s = f"{p.cost_summary.duration_ms / 1000:.2f}s" if p.cost_summary.duration_ms else "?"
            cost_s = f"${p.cost_summary.cost_usd:.4f}" if p.cost_summary.cost_usd is not None else ""
            detail_parts: list[str] = []
            if p.llm_calls:
                detail_parts.append(f"{p.llm_calls} LLM")
            if p.tool_calls:
                detail_parts.append(f"{p.tool_calls} tools")
            detail = f"   {'  '.join(detail_parts)}" if detail_parts else ""
            lines.append(f"{prefix}{connector}{p.task_name}  {dur_s}  {cost_s}  {p.status}{detail}")
            child_prefix = prefix + ("   " if is_last else "\u2502  ")
            for i, child in enumerate(node.children):
                _render_node(child, child_prefix, i == len(node.children) - 1)

        for _i, root in enumerate(summary.task_tree):
            p = root.profile
            dur_s = f"{p.cost_summary.duration_ms / 1000:.2f}s" if p.cost_summary.duration_ms else "?"
            cost_s = f"${p.cost_summary.cost_usd:.4f}" if p.cost_summary.cost_usd is not None else ""
            lines.append(f"  {p.task_name}  {dur_s}  {cost_s}  {p.status}")
            for j, child in enumerate(root.children):
                _render_node(child, "  ", j == len(root.children) - 1)

    return "\n".join(lines)


__all__ = [
    "CompactFormatter",
    # Base
    "EffectFormatter",
    # Options
    "FormatterOptions",
    "JSONFormatter",
    # Formatters
    "MarkdownFormatter",
    "TreeFormatter",
    # Profile formatter
    "format_profile",
]
