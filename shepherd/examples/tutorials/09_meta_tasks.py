"""Example 09: Meta-Tasks.

Tasks that operate on other tasks. The LLM reads task source code and can
explain, critique, or transform the target task.

Key concepts:
1. ``Input(TaskRef)`` passes a task class by serializing its source code
2. ``Output(TaskRef)`` returns raw Python source that is auto-reconstructed
3. Built-in meta-tasks can critique and transform tasks
4. Reconstructed tasks can be passed directly into later meta-tasks

Run with:
    uv run python shepherd/examples/tutorials/09_meta_tasks.py

See Also:
    design/higher-order-tasks/04-DESIGN-task-as-source.md
"""

from __future__ import annotations

import shepherd
from shepherd import ClaudeProvider, Input, Output, TaskRef, task
from shepherd_transform.meta import CritiqueTask, TransformTask
from shepherd_transform.source import extract_task_source
from pydantic import BaseModel


def section(title: str) -> None:
    """Print a section header."""
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


@task
class CalculateDiscount(BaseModel):
    """Calculate the final price after applying a discount."""

    original_price: Input(float)
    discount_percent: Input(float)
    final_price: Output(float)


# =============================================================================
# Part 1: Custom Meta-Task with Input(TaskRef)
# =============================================================================

section("Part 1: Input(TaskRef)")


@task
class ExplainTask(BaseModel):
    """Explain what a task does in plain English."""

    target: Input(TaskRef)
    explanation: Output(str)


explanation = ExplainTask(target=CalculateDiscount)

print("Task Explanation:")
print("-" * 40)
print(explanation.explanation)


# =============================================================================
# Part 2: Source Extraction
# =============================================================================

section("Part 2: Source Extraction")

source = extract_task_source(CalculateDiscount)

print("Extracted source code:")
print("-" * 40)
print(source)

print("\nThis is the source TaskRef inputs expose to the model.")


# =============================================================================
# Part 3: Built-In CritiqueTask
# =============================================================================

section("Part 3: Built-In CritiqueTask")

critique = CritiqueTask(
    target=CalculateDiscount,
    criteria=["clarity", "documentation", "type_safety"],
)

print("Severity:", critique.severity)
print("\nCritique:")
print("-" * 40)
print(critique.critique)

print("\nSuggestions:")
for suggestion in critique.suggestions:
    print(f"- {suggestion}")


# =============================================================================
# Part 4: Built-In TransformTask
# =============================================================================

section("Part 4: Built-In TransformTask")

transformation = TransformTask(
    target=CalculateDiscount,
    instruction=(
        "Add a discount_amount output field and improve the docstring so the task is clearer about the formula it uses."
    ),
)

print("Transformation explanation:")
print("-" * 40)
print(transformation.explanation)

print("\nTransformed source:")
print("-" * 40)
print(transformation.transformed_source)

print("\nAuto-reconstructed class:", transformation.transformed.__name__)
print(
    "Source round-trip preserved:",
    extract_task_source(transformation.transformed) == transformation.transformed_source,
)


# =============================================================================
# Part 5: Chaining Meta-Tasks
# =============================================================================

section("Part 5: Chaining Meta-Tasks")

follow_up = CritiqueTask(
    target=transformation.transformed,
    criteria=["clarity", "completeness"],
)

print("The transformed task can be passed directly into another meta-task.")
print("\nFollow-up critique:")
print("-" * 40)
print(follow_up.critique)


# =============================================================================
# Summary
# =============================================================================

section("Summary")

print("TaskRef workflow:")
print("- Input(TaskRef): task class -> source code in the prompt")
print("- Output(TaskRef): source code -> reconstructed task class")
print("- Chaining: reconstructed tasks can be fed into later meta-tasks directly")
print("\nNext: Tutorial 10 covers CompletedTask for execution-aware meta-tasks.")
