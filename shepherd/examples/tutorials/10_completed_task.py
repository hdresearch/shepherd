"""Example 10: CompletedTask — Execution-Aware Meta-Tasks.

Pass completed task instances to meta-tasks. The LLM sees not just source
code but also the input values, output values, and full effect stream from
each execution.

Key concepts:
1. ``Input(CompletedTask)`` passes a completed instance with its execution trace
2. ``.task_ref`` retrieves the underlying task class (TaskRef) from an instance
3. ``OptimizeFromEffects`` accepts ``list[CompletedTask]`` directly
4. ``.with_view()`` projects effects through a view (thinking, intents, outcomes, etc.)
5. Source deduplication: when multiple executions share a class, source is shown once

Prerequisites:
    Tutorial 01 (Simple Tasks) and Tutorial 09 (Meta-Tasks / TaskRef).

Run with:
    uv run python shepherd/examples/tutorials/10_completed_task.py

See Also:
    design/higher-order-tasks/meta-tasks/theoretical-foundations/
"""

from __future__ import annotations

from typing import Annotated

import shepherd
from shepherd import ClaudeProvider, CompletedTask, Input, Output, task
from shepherd_transform.meta import OptimizeFromEffects
from shepherd_transform.source import extract_task_source
from pydantic import BaseModel, Field


def section(title: str) -> None:
    """Print a section header for tutorial output."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# =============================================================================
# Configure
# =============================================================================

shepherd.configure(provider=ClaudeProvider(name="default"))


# =============================================================================
# A Simple Target Task
# =============================================================================


@task(cacheable=False)
class CalculateDiscount(BaseModel):
    """Calculate the final price after applying a discount."""

    original_price: Input(float)
    discount_percent: Input(float)
    final_price: Output(float)


# =============================================================================
# Part 1: What a Completed Instance Carries
# =============================================================================

section("Part 1: What a Completed Instance Carries")

# Execute the task — instantiation triggers execution (see Tutorial 01)
result1 = CalculateDiscount(original_price=100.0, discount_percent=20.0)
result2 = CalculateDiscount(original_price=50.0, discount_percent=110.0)

# A completed instance has inputs, outputs, and the effect stream
print(f"result1: price={result1.original_price}, discount={result1.discount_percent}%")
print(f"  -> final_price = {result1.final_price}")
print(f"  -> effects: {len(result1.effects)}")

print(f"\nresult2: price={result2.original_price}, discount={result2.discount_percent}%")
print(f"  -> final_price = {result2.final_price}")
print(f"  -> effects: {len(result2.effects)}")

# .task_ref gives back the task class — same as what Input(TaskRef) accepts
print(f"\nresult1.task_ref = {result1.task_ref.__name__}")
print(f"result1.task_ref is CalculateDiscount: {result1.task_ref is CalculateDiscount}")
print(f"extract_task_source works: {'class CalculateDiscount' in extract_task_source(result1.task_ref)}")


# =============================================================================
# Part 2: Peeking at the Effect Stream
# =============================================================================

section("Part 2: The Effect Stream")

# This is what the LLM sees when a CompletedTask is serialized.
# The effect stream is formatted as a structured timeline.
print("Effect stream for result1 (compact view):")
print("-" * 40)
print(result1.effects.to_compact())

# TaskRef shows source only; CompletedTask shows source + inputs + outputs + effects.
# This is the key difference.
print("\n[TaskRef shows the LLM only the source code.]")
print("[CompletedTask shows the LLM the source + what actually happened.]")


# =============================================================================
# Part 3: Projecting Effects with .with_view()
# =============================================================================

section("Part 3: Projecting Effects with .with_view()")

# The raw effect stream includes lifecycle bookkeeping (phase start/stop,
# cache operations) that's noise for a meta-task. The views layer provides
# focused projections:
#
#   stream.thinking()  -> just agent reasoning
#   stream.intents()   -> just tool calls
#   stream.outcomes()  -> just world interactions (files, task outcomes)
#   stream.costs()     -> resource consumption metrics
#
# .with_view() returns a copy with effects projected through a view.
# The original instance is never modified (functional style).
#
# Available named views:
#   "thinking"  -> agent reasoning (AgentThinking, AgentMessage)
#   "intents"   -> tool calls (ToolCallStarted, ToolCallCompleted)
#   "outcomes"  -> world interactions (TaskCompleted, file ops)
#   "costs"     -> resource consumption metrics
#
# Multiple names are unioned. You can also use include=/exclude= for
# fine-grained type-level filtering.

for label, result in [("result1", result1), ("result2", result2)]:
    print(f"{label}: {len(result.effects)} total effects")
    print(f"  thinking: {len(list(result.effects.thinking()))}")
    print(f"  intents:  {len(list(result.effects.intents()))}")
    print(f"  outcomes: {len(list(result.effects.outcomes()))}")

# Union of views — "give me all the signal, none of the lifecycle noise"
r1_projected = result1.with_view("thinking", "intents", "outcomes")
r2_projected = result2.with_view("thinking", "intents", "outcomes")

print(f"\nFull stream: {len(result1.effects)} effects")
print(f"Signal only: {len(r1_projected.effects)} effects")
print(f"Original unchanged: {len(result1.effects)} effects")

# The projected copy still has the same inputs, outputs, and task_ref
print(f"r1_projected.original_price = {r1_projected.original_price}")
print(f"r1_projected.final_price    = {r1_projected.final_price}")
print(f"r1_projected.task_ref       = {r1_projected.task_ref.__name__}")


# =============================================================================
# Part 4: OptimizeFromEffects with CompletedTask
# =============================================================================

section("Part 4: OptimizeFromEffects")

# Pass completed instances directly — no manual stream collection needed.
# Use .with_view() to control what the optimizer LLM sees.
optimized = OptimizeFromEffects(
    executions=[r1_projected, r2_projected],
    feedback="Should handle discount_percent > 100 gracefully",
    optimization_goals=["reliability", "clarity"],
)

print("Optimizations applied:")
for change in optimized.changes_made:
    print(f"  - {change}")
print(f"\nExpected improvement: {optimized.expected_improvement}")
print(f"\nOptimized class: {optimized.optimized.__name__}")
print(optimized.optimized_source)


# =============================================================================
# Part 5: Custom Meta-Task with Input(CompletedTask)
# =============================================================================

section("Part 5: Custom Meta-Task")


@task
class DiagnoseFailure(BaseModel):
    """Analyze a completed task execution to diagnose unexpected output.

    Examines the source code, input/output values, and the effect stream
    to explain why the task produced the output it did.
    """

    execution: Input(CompletedTask)
    expected_output: Annotated[
        Input(str),
        Field(description="What the user expected the output to be"),
    ]
    diagnosis: Output(str)
    root_cause: Output(str)
    suggestion: Output(str)


# Diagnose the edge-case execution
diagnosis = DiagnoseFailure(
    execution=r2_projected,
    expected_output="An error or clamped value, not a negative or nonsensical price",
)

print(f"Diagnosis: {diagnosis.diagnosis}")
print(f"Root cause: {diagnosis.root_cause}")
print(f"Suggestion: {diagnosis.suggestion}")


# =============================================================================
# Part 6: Verification
# =============================================================================

section("Part 6: Verification")

# verify_transformation compares the optimized task against the original
# using behavioral grounding. Pass explicit test cases to keep it fast —
# without them, generate_all() produces ~18 cases x 2 executions each.
#
# Note: we verify on normal inputs only. The edge case (discount > 100%)
# is intentionally changed — that's what we asked the optimizer to fix.
result = optimized.verify_transformation(
    CalculateDiscount,
    test_cases=[
        {"original_price": 100.0, "discount_percent": 20.0},
        {"original_price": 50.0, "discount_percent": 0.0},
        {"original_price": 200.0, "discount_percent": 50.0},
    ],
)
print(f"Verification passed: {result.passed}")
print(f"Test cases run: {result.test_count}")


# =============================================================================
# Summary
# =============================================================================

section("Summary")

print("CompletedTask workflow:")
print("  Input(CompletedTask)       -> source + inputs + outputs + effects in prompt")
print("  Input(list[CompletedTask]) -> multiple executions, source shown once per class")
print("  .task_ref                  -> the underlying task class (same as TaskRef)")
print("  .effects                   -> the execution's effect stream")
print("  .with_view(fn)             -> copy with projected effects (functional, non-mutating)")
print()
print("CompletedTask is Input-only. To return a transformed task, use Output(TaskRef).")

# =============================================================================
# What's Next
# =============================================================================
# This tutorial covered CompletedTask — giving meta-tasks access to execution
# data (inputs, outputs, effects), not just source code.
#
# Key type relationships:
#   TaskRef       ~ TaskSpec       (the definition)
#   CompletedTask ~ CompletedTask  (the execution: spec + trace)
#   .task_ref     ~ CompletedTask -> TaskRef accessor
#   .effects      ~ the execution trace (Stream)
#
# See also:
#   - Tutorial 09: Meta-Tasks (TaskRef, CritiqueTask, TransformTask)
#   - design/higher-order-tasks/meta-tasks/theoretical-foundations/
