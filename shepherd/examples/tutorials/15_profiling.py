"""Example 15: Execution Profiling.

This tutorial covers the profiler — a single-pass aggregation engine that
turns the effect stream into structured cost, timing, and call-tree data.

Topics covered:
1. Automatic profiling via VerboseConfig(show_profile=True)
2. Programmatic access to ProfileSummary
3. Time breakdown: LLM vs framework overhead
4. Per-model and per-tool statistics
5. Call-tree reconstruction across child/fork boundaries
6. Cost tracking and prompt cache metrics
7. format_profile() rendering options

Run with:
    uv run python shepherd/examples/tutorials/15_profiling.py
"""

import shepherd
from shepherd import (
    ClaudeProvider,
    Input,
    Output,
    VerboseConfig,
    step,
    task,
)
from shepherd_core.effects import (
    LLMResponseReceived,
    format_profile,
)
from pydantic import BaseModel

# =============================================================================
# Example 1: Automatic Profile via VerboseConfig
# =============================================================================
# The simplest way to see a profile: set show_profile=True on VerboseConfig.
# The profiler dashboard renders automatically at the end of task execution.

print("=" * 60)
print("Example 1: Automatic Profile (VerboseConfig)")
print("=" * 60)

shepherd.configure(
    provider=ClaudeProvider(
        name="default",
        verbose=VerboseConfig(
            enabled=True,
            show_profile=True,  # ← This is the new flag
        ),
    )
)


# Define a task with a multi-step pipeline
@task
class AnalyzeAndTranslate(BaseModel):
    """Analyze text structure, then translate and summarize."""

    text: Input(str)

    @step
    def detect_language(self, text: str) -> str:
        """What language is this text? Return just the language name."""

    @step
    def translate(self, text: str, source_language: str) -> str:
        """Translate this text to English."""

    @step
    def summarize(self, english_text: str) -> str:
        """Summarize this text in one sentence."""

    language: Output(str)
    translation: Output(str)
    summary: Output(str)

    def execute(self):
        self.language = self.detect_language(self.text)
        self.translation = self.translate(self.text, self.language)
        self.summary = self.summarize(self.translation)


result = AnalyzeAndTranslate(
    text="La beauté de la programmation réside dans sa capacité à transformer "
    "des idées abstraites en solutions concrètes qui changent le monde."
)

print(f"\nLanguage: {result.language}")
print(f"Translation: {result.translation}")
print(f"Summary: {result.summary}")

# The profile dashboard was already printed by VerboseConfig.
# Below we show how to access the same data programmatically.


# =============================================================================
# Example 2: Programmatic ProfileSummary Access
# =============================================================================
# Access the profile from the global effect stream or from a task's own stream.

print("\n" + "=" * 60)
print("Example 2: Programmatic Access")
print("=" * 60)

# Method 1: From the global effect stream
summary = shepherd.effects.profile().summarize()

# Method 2: From a specific task's effect stream
task_summary = result.effects.profile().summarize()

# Both give you a frozen ProfileSummary dataclass
print(f"\nProfileSummary type: {type(summary).__name__}")
print(f"  Immutable (frozen): {summary.__dataclass_params__.frozen}")


# =============================================================================
# Example 3: Cost Summary
# =============================================================================
# CostSummary aggregates token counts, cost, tool calls, and file operations.

print("\n" + "=" * 60)
print("Example 3: Cost Summary")
print("=" * 60)

cs = summary.cost_summary

print("\nToken usage:")
print(f"  Input tokens:   {cs.input_tokens:,}")
print(f"  Output tokens:  {cs.output_tokens:,}")
print(f"  Total tokens:   {cs.total_tokens:,}")

if cs.cost_usd is not None:
    print(f"  Cost:           ${cs.cost_usd:.4f}")

print("\nActivity:")
print(f"  LLM calls:      {cs.llm_calls}")
print(f"  Tool calls:     {cs.tool_calls} ({cs.tool_calls_rejected} rejected)")
print(f"  Files read:     {len(cs.files_read)}")
print(f"  Files modified: {len(cs.files_modified)}")
print(f"  Files created:  {cs.files_created}")


# =============================================================================
# Example 4: Time Breakdown
# =============================================================================
# TimeBreakdown provides a two-level decomposition of where time was spent.
#
# Level 1 (exact):  total_ms = llm_wall_ms + overhead_ms
# Level 2 (approx): llm_wall_ms = llm_api_ms + tool_execution_ms + turn_overhead

print("\n" + "=" * 60)
print("Example 4: Time Breakdown")
print("=" * 60)

tb = summary.time_breakdown

if tb.total_ms is not None:
    print(f"\nTotal duration:          {tb.total_ms:,.0f}ms ({tb.total_ms / 1000:.2f}s)")
    print(f"  LLM wall-clock:        {tb.llm_wall_ms:,.0f}ms")
    print(f"    ├─ API wait:         {tb.llm_api_ms:,.0f}ms")
    print(f"    ├─ Tool execution:   {tb.tool_execution_ms:,.0f}ms")
    print(f"    └─ Turn overhead:    {tb.intra_turn_overhead_ms:,.0f}ms")
    if tb.overhead_ms is not None:
        print(f"  Framework overhead:    {tb.overhead_ms:,.0f}ms")

    if tb.phase_durations:
        print("\n  Lifecycle phase durations:")
        for phase, dur in sorted(tb.phase_durations.items(), key=lambda x: x[1], reverse=True):
            print(f"    {phase:<20s} {dur:,.0f}ms")
else:
    print("\n  (No timing data — task may not have completed)")


# =============================================================================
# Example 5: Per-Model Statistics
# =============================================================================
# When using multiple models (or even one), ModelProfile gives per-model detail.

print("\n" + "=" * 60)
print("Example 5: Per-Model Statistics")
print("=" * 60)

for model in summary.models:
    print(f"\nModel: {model.model_id}")
    print(f"  LLM calls:     {model.llm_calls} ({model.error_calls} errors)")
    print(f"  Tokens:         {model.input_tokens:,} in / {model.output_tokens:,} out")
    if model.cost_usd is not None:
        print(f"  Cost:           ${model.cost_usd:.4f}")
    if model.total_turns:
        avg_turns = model.total_turns / max(model.llm_calls, 1)
        print(f"  Shepherd turns:  {model.total_turns} total (avg {avg_turns:.1f} per call)")
    if model.cache_read_input_tokens:
        ratio = model.cache_read_ratio
        ratio_str = f" ({ratio:.0%})" if ratio is not None else ""
        print(f"  Cache read:     {model.cache_read_input_tokens:,} tokens{ratio_str}")
    if model.cache_creation_input_tokens:
        print(f"  Cache created:  {model.cache_creation_input_tokens:,} tokens")

# Sorted accessors for comparisons
if len(summary.models) > 1:
    most_expensive = summary.models_by_cost[0]
    most_tokens = summary.models_by_tokens[0]
    print(f"\n  Most expensive model: {most_expensive.model_id}")
    print(f"  Most tokens used:     {most_tokens.model_id}")


# =============================================================================
# Example 6: Per-Tool Statistics
# =============================================================================
# ToolProfile tracks call counts, success rates, and avg duration per tool.

print("\n" + "=" * 60)
print("Example 6: Per-Tool Statistics")
print("=" * 60)

if summary.tools:
    print(f"\n{'Tool':<20s} {'Calls':>5s}  {'Success':>7s}  {'Rejected':>8s}  {'Avg ms':>6s}")
    print("-" * 55)
    for tool in summary.tools_by_calls:
        print(
            f"{tool.tool_name:<20s} {tool.call_count:>5d}  {tool.success_rate:>6.1%}  "
            f"{tool.rejected_count:>8d}  {tool.avg_duration_ms:>5.0f}ms"
        )
else:
    print("\n  (No tool calls in this execution)")


# =============================================================================
# Example 7: Task Call Tree
# =============================================================================
# When tasks spawn child tasks (via child scopes or fork), the profiler
# reconstructs the full call tree using scope_id / parent_scope_id pairs.
# Fork boundaries are resolved transparently via _origin_id tracking.

print("\n" + "=" * 60)
print("Example 7: Task Call Tree")
print("=" * 60)


@task
class Subtask(BaseModel):
    """A leaf task."""

    question: Input(str)
    answer: Output(str)


@task
class Orchestrator(BaseModel):
    """A parent task that spawns children."""

    topic: Input(str)
    final_answer: Output(str)

    def execute(self):
        r1 = Subtask(question=f"What is {self.topic}?")
        r2 = Subtask(question=f"Give one fun fact about {self.topic}.")
        self.final_answer = f"{r1.answer}\nFun fact: {r2.answer}"


# Reset scope so the new task gets a clean effect stream
shepherd.reset()
shepherd.configure(provider=ClaudeProvider(name="default"))

result = Orchestrator(topic="photosynthesis")
print(f"\nAnswer:\n{result.final_answer}")

# Inspect the call tree
summary = shepherd.effects.profile().summarize()

print(f"\nTask tree ({len(summary.tasks)} tasks):")
for task_profile in summary.tasks:
    indent = "  " if task_profile.parent_scope_id is None else "    "
    dur = f"{task_profile.cost_summary.duration_ms / 1000:.2f}s" if task_profile.cost_summary.duration_ms else "?"
    print(f"{indent}{task_profile.task_name}  {dur}  {task_profile.status}")

# The hierarchical tree is also available pre-built:
if summary.task_tree:
    print("\nHierarchical tree (rendered):")
    print(format_profile(summary, show_tools=False, show_bar_chart=False, show_turn_detail=False))


# =============================================================================
# Example 8: Querying Raw LLM Metadata
# =============================================================================
# LLMResponseReceived effects carry per-invocation metadata directly.
# This is the raw data that the profiler aggregates.

print("\n" + "=" * 60)
print("Example 8: Raw LLM Response Metadata")
print("=" * 60)

llm_responses = list(shepherd.effects.query(LLMResponseReceived))
print(f"\nLLM invocations: {len(llm_responses)}")

for i, layer in enumerate(llm_responses, 1):
    e = layer.effect
    cost_str = f"${e.cost_usd:.4f}" if e.cost_usd is not None else "N/A"
    print(
        f"  {i}. model={e.model_id}  "
        f"tokens={e.total_tokens:,}  "
        f"cost={cost_str}  "
        f"turns={e.num_turns}  "
        f"api={e.duration_api_ms:.0f}ms  "
        f"wall={e.duration_ms:.0f}ms"
    )


# =============================================================================
# Example 9: format_profile() Rendering Options
# =============================================================================
# format_profile() converts a ProfileSummary into a compact terminal dashboard.
# You can toggle sections via keyword arguments.

print("\n" + "=" * 60)
print("Example 9: format_profile() Options")
print("=" * 60)

# Full dashboard (default)
full = format_profile(summary)
print(f"\nFull dashboard ({len(full.splitlines())} lines):")
print(full)

# Minimal: just header + time breakdown, no tree or tools
print("\n--- Minimal view (no tree, no tools, no bar chart) ---")
print(
    format_profile(
        summary,
        show_tree=False,
        show_tools=False,
        show_bar_chart=False,
        show_phase_detail=False,
        show_turn_detail=False,
    )
)


# =============================================================================
# Example 10: Per-Task Cost Isolation
# =============================================================================
# Each TaskProfile contains its own cost_summary and time_breakdown,
# letting you attribute cost and time to individual subtasks.

print("\n" + "=" * 60)
print("Example 10: Per-Task Cost Isolation")
print("=" * 60)

for tp in summary.tasks:
    tc = tp.cost_summary
    cost_str = f"${tc.cost_usd:.4f}" if tc.cost_usd is not None else "N/A"
    dur_str = f"{tc.duration_ms / 1000:.2f}s" if tc.duration_ms else "N/A"
    print(
        f"\n  {tp.task_name} ({tp.status})"
        f"\n    Duration: {dur_str}    Cost: {cost_str}"
        f"\n    Tokens: {tc.input_tokens:,} in / {tc.output_tokens:,} out"
        f"\n    LLM calls: {tp.llm_calls}   Tool calls: {tp.tool_calls}"
    )

    # Per-task model breakdown
    for m in tp.models:
        print(f"    Model: {m.model_id}  tokens={m.total_tokens:,}")

    # Per-task tool breakdown
    for t in tp.tools:
        print(f"    Tool: {t.tool_name}  calls={t.call_count}  avg={t.avg_duration_ms:.0f}ms")


# =============================================================================
# Summary
# =============================================================================

print("\n" + "=" * 60)
print("Summary: Profiling Quick Reference")
print("=" * 60)

print("""
Automatic profiling:
    VerboseConfig(enabled=True, show_profile=True)

Programmatic access:
    summary = shepherd.effects.profile().summarize()
    summary = result.effects.profile().summarize()

Key data:
    summary.cost_summary       → tokens, cost, tool/file counts
    summary.time_breakdown     → LLM vs framework time, phase durations
    summary.models             → per-model token/cost breakdown
    summary.tools              → per-tool success rate, avg duration
    summary.tasks              → per-task isolation (cost, time, models, tools)
    summary.task_tree          → hierarchical TaskNode tree
    summary.recovery           → failure/recovery metrics

Rendering:
    from shepherd_core.effects import format_profile
    print(format_profile(summary))
    print(format_profile(summary, show_tree=False, show_tools=False))

Raw metadata:
    for layer in shepherd.effects.query(LLMResponseReceived):
        print(layer.effect.cost_usd, layer.effect.duration_ms)

Sorted accessors:
    summary.models_by_cost     → most expensive model first
    summary.models_by_tokens   → most tokens first
    summary.tools_by_calls     → most called tool first
    summary.tools_by_duration  → slowest tool first
    summary.prompt_cache_read_ratio → overall cache effectiveness
""")
