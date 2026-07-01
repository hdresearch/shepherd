"""Stream views for filtered/transformed perspectives on effect streams.

This module provides lazy, reusable views that filter and transform effect streams:
- IntentsView: Tool calls (what the agent tried to do)
- OutcomesView: External world interactions (reads + writes)
- CostsView: Resource consumption metrics
- ThinkingView: Agent reasoning (excludes prompts)
- CausalityTreeView: Effects organized by causality chains

Views are:
- Lazy: Computed on iteration
- Reusable: Each iteration creates a fresh iterator (safe because Stream is immutable)
- Composable: Views can wrap other views
- Immutable: Don't modify underlying stream

Example:
    # Quick membership check
    if ToolCallRejected in stream.intents():
        print("Some tool calls were rejected")

    # Reusable iteration
    intents = stream.intents()
    print(f"Total intents: {len(intents)}")  # Safe
    for layer in intents:                     # Safe to iterate again
        print(layer.effect.tool_name)

    # View chaining
    task_intents = stream.by_task("FixBug").intents()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shepherd_core.effects import Effect
    from shepherd_core.scope.stream import EffectLayer, Stream


# =============================================================================
# Cost Summary
# =============================================================================


@dataclass(frozen=True)
class CostSummary:
    """Aggregated cost metrics from an effect stream.

    Note: frozen=True + frozenset ensures immutability.

    For counts, use len() on the frozenset fields:
        costs.files_read  # frozenset of paths
        len(costs.files_read)  # count of unique files read
    """

    tool_calls: int = 0
    tool_calls_rejected: int = 0
    files_created: int = 0
    files_deleted: int = 0
    duration_ms: float | None = None
    # File sets (use len() for counts)
    files_read: frozenset[str] = field(default_factory=frozenset)
    files_modified: frozenset[str] = field(default_factory=frozenset)
    # Timing metadata (useful for correlating with wall-clock time)
    start_time: datetime | None = None
    end_time: datetime | None = None
    # LLM token/cost metrics (aggregated from LLMResponseReceived effects)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    llm_calls: int = 0
    # Tool execution time (sum of ToolCallCompleted.duration_ms)
    tool_duration_ms: float = 0.0


# =============================================================================
# Causality Node
# =============================================================================


@dataclass
class CausalityNode:
    """A node in the causality tree."""

    layer: EffectLayer
    children: list[CausalityNode] = field(default_factory=list)

    @property
    def effect(self) -> Effect:
        return self.layer.effect

    @property
    def effect_type(self) -> str:
        return self.layer.effect.effect_type

    def walk(self) -> Iterator[CausalityNode]:
        """Pre-order traversal of this node and descendants."""
        yield self
        for child in self.children:
            yield from child.walk()

    def __repr__(self) -> str:
        return f"CausalityNode({self.effect_type}, children={len(self.children)})"


# =============================================================================
# Stream View Base
# =============================================================================


class StreamView(ABC):
    """A filtered/transformed view of an effect stream.

    Views are:
    - Lazy: Computed on iteration
    - Reusable: Each iteration creates a fresh iterator (safe because Stream is immutable)
    - Composable: Views can wrap other views
    - Immutable: Don't modify underlying stream

    Example:
        view = stream.intents()
        print(len(view))          # Safe: creates fresh iterator
        for layer in view:        # Safe: creates another fresh iterator
            print(layer.effect)
        for layer in view:        # Safe: can iterate again
            ...
    """

    def __init__(self, source: Stream | StreamView):
        self._source = source

    def __iter__(self) -> Iterator[EffectLayer]:
        """Iterate over filtered/transformed layers.

        Creates a fresh iterator each time, so views can be iterated multiple times.
        """
        return self._make_iterator()

    def _make_iterator(self) -> Iterator[EffectLayer]:
        """Create a fresh iterator over filtered layers."""
        yield from self._filter(self._source)

    def __len__(self) -> int:
        """Count matching effects.

        Safe to call multiple times; creates a fresh iterator each time.
        Note: O(n) - iterates the full view to count.
        """
        return sum(1 for _ in self._make_iterator())

    @abstractmethod
    def _filter(self, source: Stream | StreamView) -> Iterator[EffectLayer]:
        """Subclasses implement filtering logic."""
        ...

    def to_stream(self) -> Stream:
        """Materialize view as a new Stream.

        Note: O(n) operation - iterates the entire view and allocates a new Stream.
        For large views, consider whether you actually need materialization,
        or if you can work with the lazy view directly.
        """
        from shepherd_core.scope.stream import Stream

        return Stream(_layers=tuple(self))

    def first(self) -> EffectLayer | None:
        """Return first matching layer, or None if empty.

        More efficient than list(view)[0] for large views.
        """
        for layer in self:
            return layer
        return None

    def last(self) -> EffectLayer | None:
        """Return last matching layer, or None if empty.

        Note: O(n) - must iterate the full view.
        """
        result = None
        for layer in self:
            result = layer
        return result

    def __contains__(self, effect_type: type[Effect]) -> bool:
        """Check if view contains any effect of this type.

        Useful for quick membership checks without full iteration.
        Short-circuits on first match.

        Example:
            if ToolCallRejected in stream.intents():
                print("Some tool calls were rejected")
        """
        return any(isinstance(layer.effect, effect_type) for layer in self)

    # Composable: views can chain to other views
    def intents(self) -> IntentsView:
        """View showing only intent effects (tool calls)."""
        return IntentsView(self)

    def outcomes(self, *, include_types: tuple[type[Effect], ...] | None = None) -> OutcomesView:
        """View showing effects representing external world interactions."""
        return OutcomesView(self, include_types=include_types)

    def costs(self) -> CostsView:
        """View focused on resource consumption."""
        return CostsView(self)

    def thinking(self) -> ThinkingView:
        """View showing agent reasoning (excludes prompts)."""
        return ThinkingView(self)

    def as_causality_tree(self) -> CausalityTreeView:
        """View organizing effects by causality."""
        return CausalityTreeView(self)

    def profile(self) -> ProfileView:
        """View for computing profiling metrics."""
        return ProfileView(self)


# =============================================================================
# Intents View
# =============================================================================


class IntentsView(StreamView):
    """View showing only intent effects (tool calls).

    Useful for understanding what the agent attempted,
    separate from what actually happened.

    Includes:
    - ToolCallStarted
    - ToolCallCompleted
    - ToolCallRejected
    """

    def _filter(self, source: Stream | StreamView) -> Iterator[EffectLayer]:
        from shepherd_core.effects import is_intent_effect

        for layer in source:
            if is_intent_effect(layer.effect):
                yield layer


# =============================================================================
# Outcomes View
# =============================================================================


class OutcomesView(StreamView):
    """View showing effects representing interactions with the external world.

    Criterion: Effects that represent the agent's interaction with the world
    outside its reasoning process - both observations (reads) and mutations (writes).

    Includes:
    - File operations: read, create, modify, delete
    - Task outcomes: completed, failed
    - Artifacts: produced outputs
    - Workspace: captured patches

    This is distinct from:
    - IntentsView: what the agent tried to do (tool calls)
    - ThinkingView: how the agent reasoned (internal thoughts)

    Note: We use explicit type checking rather than relying solely on
    `is_result_effect()` (which checks for `caused_by` attribute) because
    some outcome effects like FileRead don't have causality tracking - reads
    are observations, not results of tool calls.

    Extensibility: Pass `include_types` to include custom effect types that
    represent outcomes in your domain.

    Example:
        # Include custom domain effects
        outcomes = OutcomesView(stream, include_types=(TransactionEffect,))

        # Get just mutations (files that were changed)
        mutations = [layer for layer in outcomes
                     if not isinstance(layer.effect, FileRead)]
    """

    def __init__(
        self,
        source: Stream | StreamView,
        *,
        include_types: tuple[type[Effect], ...] | None = None,
    ):
        super().__init__(source)
        self._extra_types = include_types or ()

    def _filter(self, source: Stream | StreamView) -> Iterator[EffectLayer]:
        from shepherd_core.effects import (
            ArtifactWritten,
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            TaskCompleted,
            TaskFailed,
        )

        # Built-in effects representing external world interactions
        outcome_types = (
            FileRead,
            FileCreate,
            FilePatch,
            FileDelete,
            TaskCompleted,
            TaskFailed,
            ArtifactWritten,
        )

        all_types = outcome_types + self._extra_types

        for layer in source:
            if isinstance(layer.effect, all_types) or layer.effect.effect_type == "workspace_patch_captured":
                yield layer


# =============================================================================
# Costs View
# =============================================================================


class CostsView(StreamView):
    """View focused on resource consumption metrics."""

    def _filter(self, source: Stream | StreamView) -> Iterator[EffectLayer]:
        # Pass through all effects; summarize() does aggregation
        yield from source

    def summarize(self) -> CostSummary:
        """Compute aggregate cost metrics.

        Safe to call multiple times; creates a fresh iterator each time.
        """
        from shepherd_core.effects import (
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            LLMResponseReceived,
            TaskCompleted,
            ToolCallCompleted,
            ToolCallRejected,
        )

        # Collect mutable state during iteration
        tool_calls = 0
        tool_calls_rejected = 0
        files_created = 0
        files_deleted = 0
        duration_ms: float | None = None
        files_read: set[str] = set()
        files_modified: set[str] = set()
        start_time: datetime | None = None
        end_time: datetime | None = None
        # LLM token/cost aggregation
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        cost_usd_sum = 0.0
        cost_available = False
        llm_calls = 0
        tool_duration_ms = 0.0

        for layer in self:  # Iterate self, not self._source (respects view chain)
            # Track timestamps (effects always have timestamp as float epoch; 0 is valid)
            effect_timestamp = getattr(layer.effect, "timestamp", None)
            if effect_timestamp is not None:
                dt = datetime.fromtimestamp(effect_timestamp, tz=UTC)
                if start_time is None or dt < start_time:
                    start_time = dt
                if end_time is None or dt > end_time:
                    end_time = dt

            effect = layer.effect
            if isinstance(effect, ToolCallCompleted):
                tool_calls += 1
                tool_duration_ms += effect.duration_ms
            elif isinstance(effect, ToolCallRejected):
                tool_calls_rejected += 1
            elif isinstance(effect, FileRead):
                files_read.add(effect.path)
            elif isinstance(effect, FilePatch):
                files_modified.add(effect.path)
            elif isinstance(effect, FileCreate):
                files_created += 1
                files_modified.add(effect.path)  # Created files are also "modified"
            elif isinstance(effect, FileDelete):
                files_deleted += 1
            elif isinstance(effect, TaskCompleted):
                duration_ms = effect.duration_ms
            elif isinstance(effect, LLMResponseReceived):
                llm_calls += 1
                input_tokens += effect.input_tokens
                output_tokens += effect.output_tokens
                total_tokens += effect.total_tokens
                if effect.cost_usd is not None:
                    cost_usd_sum += effect.cost_usd
                    cost_available = True

        # Return frozen summary (use len() on frozensets for counts)
        return CostSummary(
            tool_calls=tool_calls,
            tool_calls_rejected=tool_calls_rejected,
            files_created=files_created,
            files_deleted=files_deleted,
            duration_ms=duration_ms,
            files_read=frozenset(files_read),
            files_modified=frozenset(files_modified),
            start_time=start_time,
            end_time=end_time,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd_sum if cost_available else None,
            llm_calls=llm_calls,
            tool_duration_ms=tool_duration_ms,
        )


# =============================================================================
# Thinking View
# =============================================================================


class ThinkingView(StreamView):
    """View showing only agent reasoning effects.

    Useful for understanding the agent's thought process - what it considered,
    decided, and communicated.

    Includes:
    - AgentThinking: Internal reasoning and planning
    - AgentMessage: Responses and communications

    Excludes:
    - PromptSent: This is input to the agent, not its reasoning

    For a view that includes prompts, use a custom filter:
        conversation = stream.filter(
            lambda e: isinstance(e, (AgentThinking, AgentMessage, PromptSent))
        )
    """

    def _filter(self, source: Stream | StreamView) -> Iterator[EffectLayer]:
        from shepherd_core.effects import AgentMessage, AgentThinking

        thinking_types = (AgentThinking, AgentMessage)

        for layer in source:
            if isinstance(layer.effect, thinking_types):
                yield layer

    def internal_only(self) -> Iterator[EffectLayer]:
        """Filter to just AgentThinking effects (excludes messages)."""
        from shepherd_core.effects import AgentThinking

        for layer in self:
            if isinstance(layer.effect, AgentThinking):
                yield layer


# =============================================================================
# Causality Tree View
# =============================================================================


class CausalityTreeView(StreamView):
    """View organizing effects by their causality relationships.

    IMPORTANT: Uses tool_call_id-based linking (NOT sequence numbers).

    The `caused_by` field on result effects contains a tool_call_id string
    that links to ToolCallStarted.tool_call_id.

    Tree structure:
    - Root nodes: TaskStarted, AgentThinking, ToolCallStarted (with children), etc.
    - ToolCallStarted nodes contain result effects as children
    - ToolCallCompleted is consumed (pairs with ToolCallStarted)

    Safety:
    - Defensive check for circular references (max depth = 100)
    - Orphaned effects (missing tool_call_id) gracefully become roots
    """

    MAX_TREE_DEPTH = 100  # Defensive limit for circular reference detection

    def _filter(self, source: Stream | StreamView) -> Iterator[EffectLayer]:
        # Pass through; tree structure accessed via as_tree()
        yield from source

    def as_tree(self) -> list[CausalityNode]:
        """Build causality tree from effect stream using tool_call_id linking.

        Returns a list of root CausalityNode objects.

        Note: This method materializes the source once at the start because
        the tree-building algorithm requires multiple passes over the data.
        Validated in spike_view_chaining_and_performance.py.
        """
        from shepherd_core.effects import ToolCallCompleted, ToolCallStarted

        # CRITICAL: Materialize source once to support chained views.
        # Views are single-pass; iterating self._source multiple times
        # would fail if source is a FilterView or other chained view.
        layers = list(self._source)

        # 1. Index tool calls by tool_call_id for O(1) lookup
        tool_call_layers: dict[str, EffectLayer] = {}
        for layer in layers:
            effect = layer.effect
            if isinstance(effect, ToolCallStarted):
                tool_call_layers[effect.tool_call_id] = layer

        # 2. Group result effects by their causing tool_call_id
        results_by_tool_call: dict[str, list[EffectLayer]] = defaultdict(list)
        consumed: set[int] = set()  # Sequences attached as children

        for layer in layers:
            effect = layer.effect
            caused_by = getattr(effect, "caused_by", None)

            if caused_by is not None and caused_by in tool_call_layers:
                results_by_tool_call[caused_by].append(layer)
                consumed.add(layer.sequence)

        # 3. Build nodes for tool calls with their result children
        tool_call_nodes: dict[str, CausalityNode] = {}
        for tool_call_id, tool_layer in tool_call_layers.items():
            result_layers = results_by_tool_call.get(tool_call_id, [])
            children = [CausalityNode(layer=rl) for rl in result_layers]
            tool_call_nodes[tool_call_id] = CausalityNode(layer=tool_layer, children=children)
            # NOTE: Don't add tool_layer.sequence to consumed - tool calls ARE roots

        # 4. Mark ToolCallCompleted as consumed (pairs with ToolCallStarted)
        for layer in layers:
            if isinstance(layer.effect, ToolCallCompleted):
                consumed.add(layer.sequence)

        # 5. Build roots (everything not consumed)
        roots: list[CausalityNode] = []
        for layer in layers:
            if layer.sequence in consumed:
                continue

            effect = layer.effect
            if isinstance(effect, ToolCallStarted):
                # Use the pre-built node with children
                roots.append(tool_call_nodes[effect.tool_call_id])
            else:
                # Regular effect becomes a root with no children
                roots.append(CausalityNode(layer=layer))

        return roots


# =============================================================================
# Profile Data Model
# =============================================================================


@dataclass(frozen=True)
class TimeBreakdown:
    """Two-level decomposition of where wall-clock time was spent.

    Level 1 (exact): total_ms = llm_wall_ms + overhead_ms
    Level 2 (approximate): sub-breakdowns within each bucket.
    """

    total_ms: float | None = None
    llm_api_ms: float = 0.0
    llm_wall_ms: float = 0.0
    tool_execution_ms: float = 0.0
    phase_durations: dict[str, float] = field(default_factory=dict)

    @property
    def overhead_ms(self) -> float | None:
        if self.total_ms is None:
            return None
        return max(0.0, self.total_ms - self.llm_wall_ms)

    @property
    def intra_turn_overhead_ms(self) -> float:
        return max(0.0, self.llm_wall_ms - self.llm_api_ms - self.tool_execution_ms)


@dataclass(frozen=True)
class ModelProfile:
    """Per-model token/cost breakdown."""

    model_id: str = ""
    llm_calls: int = 0
    error_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float | None = None
    duration_api_ms: float = 0.0
    total_turns: int = 0

    @property
    def cache_read_ratio(self) -> float | None:
        if self.input_tokens == 0:
            return None
        return self.cache_read_input_tokens / self.input_tokens


@dataclass(frozen=True)
class ToolProfile:
    """Per-tool usage statistics."""

    tool_name: str = ""
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    rejected_count: int = 0
    total_duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        completions = self.success_count + self.failure_count
        if completions == 0:
            return 1.0
        return self.success_count / completions

    @property
    def avg_duration_ms(self) -> float:
        completions = self.success_count + self.failure_count
        if completions == 0:
            return 0.0
        return self.total_duration_ms / completions


@dataclass(frozen=True)
class StageRecord:
    """Record of a pipeline stage that produced no full subtask profile.

    Only tracks skipped, defaulted, and partial stages — normal stages
    are already represented by their subtask's TaskProfile.
    """

    stage_name: str = ""
    pipeline_task_name: str = ""
    status: str = ""  # "skipped", "defaulted", "partial"
    duration_ms: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class TaskProfile:
    """Per-task instance breakdown, keyed by scope_id."""

    task_name: str = ""
    scope_id: str = ""
    parent_scope_id: str | None = None
    device_name: str | None = None
    stage_name: str | None = None
    status: str = "in_progress"
    cost_summary: CostSummary = field(default_factory=CostSummary)
    time_breakdown: TimeBreakdown = field(default_factory=TimeBreakdown)
    models: tuple[ModelProfile, ...] = ()
    tools: tuple[ToolProfile, ...] = ()
    llm_calls: int = 0
    tool_calls: int = 0
    error_type: str | None = None
    error_phase: str | None = None
    last_tool_name: str | None = None
    tool_calls_completed: int | None = None
    stage_overhead_ms: float | None = None


@dataclass(frozen=True)
class TaskNode:
    """Thin wrapper for hierarchical display of tasks."""

    profile: TaskProfile
    children: tuple[TaskNode, ...] = ()


@dataclass(frozen=True)
class RecoverySummary:
    """Aggregate failure and recovery metrics."""

    execution_failures: int = 0
    recoveries_attempted: int = 0
    failure_types: dict[str, int] = field(default_factory=dict)
    recovery_strategies: dict[str, int] = field(default_factory=dict)
    triggering_tools: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskCacheSummary:
    """Task-level output cache effectiveness."""

    hits: int = 0
    stores: int = 0
    total_stored_bytes: int = 0

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.stores
        if total == 0:
            return None
        return self.hits / total


@dataclass(frozen=True)
class ProfileSummary:
    """Structured profiling summary computed from an effect stream."""

    cost_summary: CostSummary = field(default_factory=CostSummary)
    time_breakdown: TimeBreakdown = field(default_factory=TimeBreakdown)
    tasks: tuple[TaskProfile, ...] = ()
    task_tree: tuple[TaskNode, ...] = ()
    models: tuple[ModelProfile, ...] = ()
    tools: tuple[ToolProfile, ...] = ()
    recovery: RecoverySummary = field(default_factory=RecoverySummary)
    task_cache: TaskCacheSummary = field(default_factory=TaskCacheSummary)
    stages: tuple[StageRecord, ...] = ()

    @property
    def models_by_cost(self) -> tuple[ModelProfile, ...]:
        return tuple(sorted(self.models, key=lambda m: m.cost_usd or 0.0, reverse=True))

    @property
    def models_by_tokens(self) -> tuple[ModelProfile, ...]:
        return tuple(sorted(self.models, key=lambda m: m.total_tokens, reverse=True))

    @property
    def tools_by_calls(self) -> tuple[ToolProfile, ...]:
        return tuple(sorted(self.tools, key=lambda t: t.call_count, reverse=True))

    @property
    def tools_by_duration(self) -> tuple[ToolProfile, ...]:
        return tuple(sorted(self.tools, key=lambda t: t.total_duration_ms, reverse=True))

    @property
    def prompt_cache_read_ratio(self) -> float | None:
        total_input = sum(m.input_tokens for m in self.models)
        if total_input == 0:
            return None
        total_cache_read = sum(m.cache_read_input_tokens for m in self.models)
        return total_cache_read / total_input

    @property
    def tasks_by_device(self) -> dict[str, tuple[TaskProfile, ...]]:
        """Group tasks by the device that executed them."""
        by_device: dict[str, list[TaskProfile]] = {}
        for tp in self.tasks:
            dev = tp.device_name or "local"
            by_device.setdefault(dev, []).append(tp)
        return {k: tuple(v) for k, v in by_device.items()}


# =============================================================================
# Profile View
# =============================================================================


class ProfileView(StreamView):
    """View that computes a structured ProfileSummary from an effect stream.

    Single-pass aggregation over 16 effect types, producing per-model,
    per-tool, per-task, and top-level breakdowns.
    """

    def _filter(self, source: Stream | StreamView) -> Iterator[EffectLayer]:
        yield from source

    def summarize(self) -> ProfileSummary:
        """Compute the full profile summary. Safe to call multiple times."""
        from typing import Any

        try:
            from shepherd_runtime.cache import CacheHit, CacheStored  # type: ignore[import-not-found,unused-ignore]
        except (ImportError, ModuleNotFoundError):
            CacheHit = CacheStored = None  # type: ignore[assignment,misc,unused-ignore]
        from shepherd_core.effects import (
            ExecutionFailed,
            FileCreate,
            FileDelete,
            FilePatch,
            FileRead,
            LifecyclePhaseCompleted,
            LifecyclePhaseFailed,
            LLMResponseReceived,
            RecoveryAttempted,
            StageCompleted,
            StageSkipped,
            TaskCompleted,
            TaskFailed,
            TaskStarted,
            ToolCallCompleted,
            ToolCallRejected,
        )

        # --- Mutable accumulators ---

        # Global cost accumulator (mirrors CostsView exactly)
        g_tool_calls = 0
        g_tool_calls_rejected = 0
        g_files_created = 0
        g_files_deleted = 0
        g_duration_ms: float | None = None
        g_files_read: set[str] = set()
        g_files_modified: set[str] = set()
        g_start_time: datetime | None = None
        g_end_time: datetime | None = None
        g_input_tokens = 0
        g_output_tokens = 0
        g_total_tokens = 0
        g_cost_usd_sum = 0.0
        g_cost_available = False
        g_llm_calls = 0
        g_tool_duration_ms = 0.0

        # Global time accumulator
        g_llm_api_ms = 0.0
        g_llm_wall_ms = 0.0
        g_tool_exec_ms = 0.0
        g_phase_durations: dict[str, float] = {}

        # Global model accumulators keyed by model_id
        model_accs: dict[str, dict[str, Any]] = {}

        # Global tool accumulators keyed by tool_name
        tool_accs: dict[str, dict[str, Any]] = {}

        # Per-task accumulators keyed by scope_id
        task_accs: dict[str, dict[str, Any]] = {}

        # Recovery accumulator
        rec_failures = 0
        rec_recoveries = 0
        rec_failure_types: dict[str, int] = {}
        rec_strategies: dict[str, int] = {}
        rec_triggering_tools: dict[str, int] = {}

        # Task cache accumulator
        tc_hits = 0
        tc_stores = 0
        tc_stored_bytes = 0

        # Stage accumulators (Gaps A + C)
        stage_records: list[dict[str, Any]] = []
        # Keyed by (pipeline_scope_id, stage_name) to avoid cross-pipeline collision
        stage_envelopes: dict[tuple[str | None, str], float] = {}

        def _ensure_model_acc(mid: str) -> dict[str, Any]:
            if mid not in model_accs:
                model_accs[mid] = {
                    "llm_calls": 0,
                    "error_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cost_usd_sum": 0.0,
                    "cost_available": False,
                    "duration_api_ms": 0.0,
                    "total_turns": 0,
                }
            return model_accs[mid]

        def _ensure_tool_acc(tname: str) -> dict[str, Any]:
            if tname not in tool_accs:
                tool_accs[tname] = {
                    "success_count": 0,
                    "failure_count": 0,
                    "rejected_count": 0,
                    "total_duration_ms": 0.0,
                }
            return tool_accs[tname]

        def _ensure_task_model_acc(task: dict[str, Any], mid: str) -> dict[str, Any]:
            models = task["models"]
            if mid not in models:
                models[mid] = {
                    "llm_calls": 0,
                    "error_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cost_usd_sum": 0.0,
                    "cost_available": False,
                    "duration_api_ms": 0.0,
                    "total_turns": 0,
                }
            return models[mid]  # type: ignore[no-any-return]

        def _ensure_task_tool_acc(task: dict[str, Any], tname: str) -> dict[str, Any]:
            tools = task["tools"]
            if tname not in tools:
                tools[tname] = {
                    "success_count": 0,
                    "failure_count": 0,
                    "rejected_count": 0,
                    "total_duration_ms": 0.0,
                }
            return tools[tname]  # type: ignore[no-any-return]

        def _new_task_acc(
            task_name: str,
            scope_id: str,
            parent_scope_id: str | None,
            device_name: str | None = None,
            stage_name: str | None = None,
        ) -> dict[str, Any]:
            return {
                "task_name": task_name,
                "scope_id": scope_id,
                "parent_scope_id": parent_scope_id,
                "device_name": device_name,
                "stage_name": stage_name,
                "status": "in_progress",
                "llm_calls": 0,
                "tool_calls": 0,
                "tool_calls_rejected": 0,
                "files_created": 0,
                "files_deleted": 0,
                "files_read": set(),
                "files_modified": set(),
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd_sum": 0.0,
                "cost_available": False,
                "tool_duration_ms": 0.0,
                "start_time": None,
                "end_time": None,
                "duration_ms": None,
                "llm_api_ms": 0.0,
                "llm_wall_ms": 0.0,
                "tool_exec_ms": 0.0,
                "phase_durations": {},
                "models": {},
                "tools": {},
                "error_type": None,
                "error_phase": None,
                "last_tool_name": None,
                "tool_calls_completed": None,
            }

        def _track_timestamp(
            effect: Effect,
            acc_start_key: str = "start_time",
            acc_end_key: str = "end_time",
            acc: dict[str, Any] | None = None,
        ) -> None:
            ts = getattr(effect, "timestamp", None)
            if ts is None:
                return
            dt = datetime.fromtimestamp(ts, tz=UTC)
            if acc is not None:
                if acc[acc_start_key] is None or dt < acc[acc_start_key]:
                    acc[acc_start_key] = dt
                if acc[acc_end_key] is None or dt > acc[acc_end_key]:
                    acc[acc_end_key] = dt

        # --- Single pass ---
        for layer in self:
            effect = layer.effect
            sid = layer.scope_id
            task_acc = task_accs.get(sid) if sid else None

            # Track global timestamps (same as CostsView)
            effect_timestamp = getattr(effect, "timestamp", None)
            if effect_timestamp is not None:
                dt = datetime.fromtimestamp(effect_timestamp, tz=UTC)
                if g_start_time is None or dt < g_start_time:
                    g_start_time = dt
                if g_end_time is None or dt > g_end_time:
                    g_end_time = dt

            # Track per-task timestamps
            if task_acc is not None:
                _track_timestamp(effect, acc=task_acc)

            # 1. TaskStarted
            if isinstance(effect, TaskStarted):
                task_accs[effect.scope_id] = _new_task_acc(
                    effect.task_name or "",
                    effect.scope_id,
                    effect.parent_scope_id,
                    effect.device_name,
                    effect.stage_name,
                )

            # 2. TaskCompleted
            elif isinstance(effect, TaskCompleted):
                g_duration_ms = effect.duration_ms
                if task_acc is not None:
                    task_acc["status"] = "completed"
                    task_acc["duration_ms"] = effect.duration_ms

            # 3. TaskFailed
            elif isinstance(effect, TaskFailed):
                if task_acc is not None:
                    task_acc["status"] = "failed"
                    task_acc["duration_ms"] = effect.duration_ms
                    task_acc["error_type"] = effect.error_type
                    task_acc["error_phase"] = effect.phase
                    task_acc["last_tool_name"] = effect.last_tool_name
                    task_acc["tool_calls_completed"] = effect.tool_calls_completed

            # 4. LLMResponseReceived
            elif isinstance(effect, LLMResponseReceived):
                g_llm_calls += 1
                g_input_tokens += effect.input_tokens
                g_output_tokens += effect.output_tokens
                g_total_tokens += effect.total_tokens
                if effect.cost_usd is not None:
                    g_cost_usd_sum += effect.cost_usd
                    g_cost_available = True
                g_llm_api_ms += effect.duration_api_ms
                g_llm_wall_ms += effect.duration_ms

                mid = effect.model_id
                macc = _ensure_model_acc(mid)
                macc["llm_calls"] += 1
                macc["input_tokens"] += effect.input_tokens
                macc["output_tokens"] += effect.output_tokens
                macc["total_tokens"] += effect.total_tokens
                macc["cache_creation_input_tokens"] += effect.cache_creation_input_tokens
                macc["cache_read_input_tokens"] += effect.cache_read_input_tokens
                if effect.cost_usd is not None:
                    macc["cost_usd_sum"] += effect.cost_usd
                    macc["cost_available"] = True
                macc["duration_api_ms"] += effect.duration_api_ms
                macc["total_turns"] += effect.num_turns
                if effect.is_error:
                    macc["error_calls"] += 1

                if task_acc is not None:
                    task_acc["llm_calls"] += 1
                    task_acc["input_tokens"] += effect.input_tokens
                    task_acc["output_tokens"] += effect.output_tokens
                    task_acc["total_tokens"] += effect.total_tokens
                    if effect.cost_usd is not None:
                        task_acc["cost_usd_sum"] += effect.cost_usd
                        task_acc["cost_available"] = True
                    task_acc["llm_api_ms"] += effect.duration_api_ms
                    task_acc["llm_wall_ms"] += effect.duration_ms

                    tmacc = _ensure_task_model_acc(task_acc, mid)
                    tmacc["llm_calls"] += 1
                    tmacc["input_tokens"] += effect.input_tokens
                    tmacc["output_tokens"] += effect.output_tokens
                    tmacc["total_tokens"] += effect.total_tokens
                    tmacc["cache_creation_input_tokens"] += effect.cache_creation_input_tokens
                    tmacc["cache_read_input_tokens"] += effect.cache_read_input_tokens
                    if effect.cost_usd is not None:
                        tmacc["cost_usd_sum"] += effect.cost_usd
                        tmacc["cost_available"] = True
                    tmacc["duration_api_ms"] += effect.duration_api_ms
                    tmacc["total_turns"] += effect.num_turns
                    if effect.is_error:
                        tmacc["error_calls"] += 1

            # 5. ToolCallCompleted
            elif isinstance(effect, ToolCallCompleted):
                g_tool_calls += 1
                g_tool_duration_ms += effect.duration_ms
                g_tool_exec_ms += effect.duration_ms

                tacc = _ensure_tool_acc(effect.tool_name)
                if effect.success:
                    tacc["success_count"] += 1
                else:
                    tacc["failure_count"] += 1
                tacc["total_duration_ms"] += effect.duration_ms

                if task_acc is not None:
                    task_acc["tool_calls"] += 1
                    task_acc["tool_duration_ms"] += effect.duration_ms
                    task_acc["tool_exec_ms"] += effect.duration_ms

                    ttacc = _ensure_task_tool_acc(task_acc, effect.tool_name)
                    if effect.success:
                        ttacc["success_count"] += 1
                    else:
                        ttacc["failure_count"] += 1
                    ttacc["total_duration_ms"] += effect.duration_ms

            # 6. ToolCallRejected
            elif isinstance(effect, ToolCallRejected):
                g_tool_calls_rejected += 1
                tacc = _ensure_tool_acc(effect.tool_name)
                tacc["rejected_count"] += 1

                if task_acc is not None:
                    task_acc["tool_calls_rejected"] += 1
                    ttacc = _ensure_task_tool_acc(task_acc, effect.tool_name)
                    ttacc["rejected_count"] += 1

            # 7. FileRead
            elif isinstance(effect, FileRead):
                g_files_read.add(effect.path)
                if task_acc is not None:
                    task_acc["files_read"].add(effect.path)

            # 8. FileCreate
            elif isinstance(effect, FileCreate):
                g_files_created += 1
                g_files_modified.add(effect.path)
                if task_acc is not None:
                    task_acc["files_created"] += 1
                    task_acc["files_modified"].add(effect.path)

            # 9. FilePatch
            elif isinstance(effect, FilePatch):
                g_files_modified.add(effect.path)
                if task_acc is not None:
                    task_acc["files_modified"].add(effect.path)

            # 10. FileDelete
            elif isinstance(effect, FileDelete):
                g_files_deleted += 1
                if task_acc is not None:
                    task_acc["files_deleted"] += 1

            # 11/12. LifecyclePhaseCompleted or LifecyclePhaseFailed
            elif isinstance(effect, (LifecyclePhaseCompleted, LifecyclePhaseFailed)):
                g_phase_durations[effect.phase] = g_phase_durations.get(effect.phase, 0.0) + effect.duration_ms
                if task_acc is not None:
                    pd = task_acc["phase_durations"]
                    pd[effect.phase] = pd.get(effect.phase, 0.0) + effect.duration_ms

            # 13. ExecutionFailed
            elif isinstance(effect, ExecutionFailed):
                rec_failures += 1
                et = effect.error_type
                rec_failure_types[et] = rec_failure_types.get(et, 0) + 1
                if effect.last_tool_name:
                    rec_triggering_tools[effect.last_tool_name] = rec_triggering_tools.get(effect.last_tool_name, 0) + 1

            # 14. RecoveryAttempted
            elif isinstance(effect, RecoveryAttempted):
                rec_recoveries += 1
                rs = effect.recovery_strategy
                rec_strategies[rs] = rec_strategies.get(rs, 0) + 1

            # 15. CacheHit
            elif CacheHit is not None and isinstance(effect, CacheHit):
                tc_hits += 1

            # 16. CacheStored
            elif CacheStored is not None and isinstance(effect, CacheStored):
                tc_stores += 1
                tc_stored_bytes += effect.size_bytes

            # 17. StageSkipped (Gap A: skipped stages invisible)
            elif isinstance(effect, StageSkipped):
                stage_records.append(
                    {
                        "stage_name": effect.stage_name,
                        "pipeline_task_name": effect.task_name or "",
                        "status": "skipped",
                        "duration_ms": None,
                        "reason": effect.reason,
                    }
                )

            # 18. StageCompleted (Gap A: defaulted/partial; Gap C: overhead)
            elif isinstance(effect, StageCompleted):
                if effect.defaulted:
                    stage_records.append(
                        {
                            "stage_name": effect.stage_name,
                            "pipeline_task_name": effect.task_name or "",
                            "status": "defaulted",
                            "duration_ms": effect.duration_ms,
                            "reason": "",
                        }
                    )
                elif effect.partial:
                    stage_records.append(
                        {
                            "stage_name": effect.stage_name,
                            "pipeline_task_name": effect.task_name or "",
                            "status": "partial",
                            "duration_ms": effect.duration_ms,
                            "reason": "",
                        }
                    )
                # Always track envelope for overhead computation (Gap C)
                stage_envelopes[(sid, effect.stage_name)] = effect.duration_ms

        # --- Freeze ---

        def _freeze_model_accs(accs: dict[str, dict[str, Any]]) -> tuple[ModelProfile, ...]:
            result = []
            for mid, m in accs.items():
                result.append(
                    ModelProfile(
                        model_id=mid,
                        llm_calls=m["llm_calls"],
                        error_calls=m["error_calls"],
                        input_tokens=m["input_tokens"],
                        output_tokens=m["output_tokens"],
                        total_tokens=m["total_tokens"],
                        cache_creation_input_tokens=m["cache_creation_input_tokens"],
                        cache_read_input_tokens=m["cache_read_input_tokens"],
                        cost_usd=m["cost_usd_sum"] if m["cost_available"] else None,
                        duration_api_ms=m["duration_api_ms"],
                        total_turns=m["total_turns"],
                    )
                )
            return tuple(result)

        def _freeze_tool_accs(accs: dict[str, dict[str, Any]]) -> tuple[ToolProfile, ...]:
            result = []
            for tname, t in accs.items():
                result.append(
                    ToolProfile(
                        tool_name=tname,
                        call_count=t["success_count"] + t["failure_count"] + t["rejected_count"],
                        success_count=t["success_count"],
                        failure_count=t["failure_count"],
                        rejected_count=t["rejected_count"],
                        total_duration_ms=t["total_duration_ms"],
                    )
                )
            return tuple(result)

        # Freeze global cost summary
        cost_summary = CostSummary(
            tool_calls=g_tool_calls,
            tool_calls_rejected=g_tool_calls_rejected,
            files_created=g_files_created,
            files_deleted=g_files_deleted,
            duration_ms=g_duration_ms,
            files_read=frozenset(g_files_read),
            files_modified=frozenset(g_files_modified),
            start_time=g_start_time,
            end_time=g_end_time,
            input_tokens=g_input_tokens,
            output_tokens=g_output_tokens,
            total_tokens=g_total_tokens,
            cost_usd=g_cost_usd_sum if g_cost_available else None,
            llm_calls=g_llm_calls,
            tool_duration_ms=g_tool_duration_ms,
        )

        time_breakdown = TimeBreakdown(
            total_ms=g_duration_ms,
            llm_api_ms=g_llm_api_ms,
            llm_wall_ms=g_llm_wall_ms,
            tool_execution_ms=g_tool_exec_ms,
            phase_durations=g_phase_durations,
        )

        models = _freeze_model_accs(model_accs)
        tools = _freeze_tool_accs(tool_accs)

        recovery = RecoverySummary(
            execution_failures=rec_failures,
            recoveries_attempted=rec_recoveries,
            failure_types=rec_failure_types,
            recovery_strategies=rec_strategies,
            triggering_tools=rec_triggering_tools,
        )

        task_cache = TaskCacheSummary(
            hits=tc_hits,
            stores=tc_stores,
            total_stored_bytes=tc_stored_bytes,
        )

        # Compute stage overhead (Gap C): for each task with a stage_name,
        # look up the stage envelope and subtract the task duration.
        for ta in task_accs.values():
            sn = ta.get("stage_name")
            if sn:
                envelope_key = (ta["parent_scope_id"], sn)
                envelope_ms = stage_envelopes.get(envelope_key)
                if envelope_ms is not None and ta["duration_ms"] is not None:
                    ta["stage_overhead_ms"] = max(0.0, envelope_ms - ta["duration_ms"])

        # Freeze per-task profiles
        task_profiles: list[TaskProfile] = []
        for ta in task_accs.values():
            tp = TaskProfile(
                task_name=ta["task_name"],
                scope_id=ta["scope_id"],
                parent_scope_id=ta["parent_scope_id"],
                device_name=ta["device_name"],
                stage_name=ta["stage_name"],
                status=ta["status"],
                cost_summary=CostSummary(
                    tool_calls=ta["tool_calls"],
                    tool_calls_rejected=ta["tool_calls_rejected"],
                    files_created=ta["files_created"],
                    files_deleted=ta["files_deleted"],
                    duration_ms=ta["duration_ms"],
                    files_read=frozenset(ta["files_read"]),
                    files_modified=frozenset(ta["files_modified"]),
                    start_time=ta["start_time"],
                    end_time=ta["end_time"],
                    input_tokens=ta["input_tokens"],
                    output_tokens=ta["output_tokens"],
                    total_tokens=ta["total_tokens"],
                    cost_usd=ta["cost_usd_sum"] if ta["cost_available"] else None,
                    llm_calls=ta["llm_calls"],
                    tool_duration_ms=ta["tool_duration_ms"],
                ),
                time_breakdown=TimeBreakdown(
                    total_ms=ta["duration_ms"],
                    llm_api_ms=ta["llm_api_ms"],
                    llm_wall_ms=ta["llm_wall_ms"],
                    tool_execution_ms=ta["tool_exec_ms"],
                    phase_durations=ta["phase_durations"],
                ),
                models=_freeze_model_accs(ta["models"]),
                tools=_freeze_tool_accs(ta["tools"]),
                llm_calls=ta["llm_calls"],
                tool_calls=ta["tool_calls"],
                error_type=ta["error_type"],
                error_phase=ta["error_phase"],
                last_tool_name=ta["last_tool_name"],
                tool_calls_completed=ta["tool_calls_completed"],
                stage_overhead_ms=ta.get("stage_overhead_ms"),
            )
            task_profiles.append(tp)

        tasks_tuple = tuple(task_profiles)

        # Build task tree
        profiles_by_sid: dict[str, TaskProfile] = {tp.scope_id: tp for tp in task_profiles}
        children_map: dict[str, list[TaskProfile]] = defaultdict(list)
        roots: list[TaskProfile] = []
        for tp in task_profiles:
            if tp.parent_scope_id and tp.parent_scope_id in profiles_by_sid:
                children_map[tp.parent_scope_id].append(tp)
            else:
                roots.append(tp)

        def _build_node(tp: TaskProfile) -> TaskNode:
            kids = children_map.get(tp.scope_id, [])
            return TaskNode(profile=tp, children=tuple(_build_node(k) for k in kids))

        task_tree = tuple(_build_node(r) for r in roots)

        # Freeze stage records (Gap A)
        frozen_stages = tuple(
            StageRecord(
                stage_name=sr["stage_name"],
                pipeline_task_name=sr["pipeline_task_name"],
                status=sr["status"],
                duration_ms=sr["duration_ms"],
                reason=sr["reason"],
            )
            for sr in stage_records
        )

        return ProfileSummary(
            cost_summary=cost_summary,
            time_breakdown=time_breakdown,
            tasks=tasks_tuple,
            task_tree=task_tree,
            models=models,
            tools=tools,
            recovery=recovery,
            task_cache=task_cache,
            stages=frozen_stages,
        )


__all__ = [
    "CausalityNode",
    "CausalityTreeView",
    # Data classes
    "CostSummary",
    "CostsView",
    # Views
    "IntentsView",
    # Profile data model
    "ModelProfile",
    "OutcomesView",
    "ProfileSummary",
    "ProfileView",
    "RecoverySummary",
    "StageRecord",
    # Base
    "StreamView",
    "TaskCacheSummary",
    "TaskNode",
    "TaskProfile",
    "ThinkingView",
    "TimeBreakdown",
    "ToolProfile",
]
