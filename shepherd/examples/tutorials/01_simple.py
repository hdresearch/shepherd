"""Example 01: Simple Task Execution.

Define tasks with @task, run them, inspect outputs and effects.

Key concepts:
1. @task decorator with Input() and Output() markers
2. Direct instantiation executes the task (sync-first API)
3. Two-channel model: result.field for outputs, result.effects for effects

Run with:
    uv run python shepherd/examples/tutorials/01_simple.py
"""

from typing import Annotated

import shepherd
from shepherd import ClaudeProvider, Input, Output, TaskCompleted, TaskStarted, task
from pydantic import BaseModel, Field

# =============================================================================
# Configure
# =============================================================================

shepherd.configure(provider=ClaudeProvider(name="default"))

# =============================================================================
# Define Tasks
# =============================================================================


@task
class TellJoke(BaseModel):
    """Tell a joke about a given topic."""

    topic: Input(str)
    joke: Output(str)


@task
class RateJoke(BaseModel):
    """Rate how funny a joke is."""

    joke: Annotated[Input(str), Field(description="The joke to rate")]
    rating: Annotated[Output(int), Field(ge=0, le=10)]


# =============================================================================
# Execute
# =============================================================================

# Run a task — instantiation triggers execution
joke = TellJoke(topic="programming")

# Channel 1: Outputs (access via attributes)
print(f"Joke: {joke.joke}")

# Channel 2: Effects (access via .effects)
print(f"Effects from this task: {len(joke.effects)}")

# Chain tasks — pass output from one task to another
rating = RateJoke(joke=joke.joke)
print(f"Rating: {rating.rating}/10")

# =============================================================================
# Global Effect Stream
# =============================================================================

# All effects are captured in shepherd.effects
print(f"\nTotal effects: {len(shepherd.effects)}")

# Query by effect type
starts = list(shepherd.effects.query(TaskStarted))
completions = list(shepherd.effects.query(TaskCompleted))
print(f"Tasks started: {len(starts)}, completed: {len(completions)}")

# =============================================================================
# What's Next
# =============================================================================
# This tutorial covered Input() and Output() — the basic boundary markers.
#
# Shepherd has two more markers for advanced use cases:
#   - Context(): Stateful resources shared across tasks (Tutorial 02)
#   - Artifact(): File outputs captured from task execution (Tutorial 05)
#
# See also:
#   - Tutorial 02: Contexts and scopes
#   - Tutorial 04: Workspaces and file effects
#   - Tutorial 07: Devices and fluent Pipeline composition
#
# Debugging:
#   print(shepherd.debug_summary())  # Execution timeline
#   See shepherd/docs/guides/debugging.md for the full guide
