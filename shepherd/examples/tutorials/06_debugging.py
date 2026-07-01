"""Example 06: Debugging.

Stuck on an error? You're in the right place. This tutorial covers how to
diagnose problems in Shepherd, regardless of which tutorial you came from.

Quick fixes for common errors:
- "No scope available" → Call shepherd.configure(provider=...) first
- "No binding found"   → Call shepherd.bind("name", context) first
- Task hangs/slow      → Enable VerboseConfig to see what's happening

Tools covered:
1. shepherd.debug_summary() - Execution timeline
2. session.debug_info() - Session state diagnostics
3. Effect filtering - .direct(), .summarized(), .by_depth()
4. VerboseConfig - Real-time visibility

Run with:
    uv run python shepherd/examples/tutorials/06_debugging.py
"""

import shepherd
from shepherd import (
    ClaudeProvider,
    Context,
    Input,
    Output,
    SessionState,
    TaskCompleted,
    ToolCallCompleted,
    VerboseConfig,
    scope,
    task,
)
from pydantic import BaseModel

# =============================================================================
# Tasks for demonstration
# =============================================================================


@task
class SimpleTask(BaseModel):
    """A simple task that might fail."""

    question: Input(str)
    session: Context(SessionState)
    answer: Output(str)


# =============================================================================
# Example 1: Using debug_summary()
# =============================================================================

print("=" * 60)
print("Example 1: Using debug_summary()")
print("=" * 60)

# Configure with verbose mode for visibility
provider = ClaudeProvider(
    name="default",
    verbose=VerboseConfig(
        enabled=True,
        show_prompts=True,  # Show prompts sent to LLM
        auto_debug_on_failure=True,  # Auto-print debug_summary on failure
    ),
)
shepherd.configure(provider=provider)

# Bind a session
session = scope.bind("session", SessionState())

# Run a task
result = SimpleTask(question="What is 2+2?")
print(f"Answer: {result.answer}")

# Inspect the execution timeline
print("\n--- debug_summary() output ---")
print(shepherd.debug_summary())

# =============================================================================
# Example 2: Session Debug Info
# =============================================================================

print("\n" + "=" * 60)
print("Example 2: Session Debug Info")
print("=" * 60)

# After running tasks, check session state
# (session ContextRef auto-updates with session_id from execution)
print("\n--- session.debug_info() ---")
print(session.debug_info())

# =============================================================================
# Example 3: Querying the Effect Stream
# =============================================================================

print("\n" + "=" * 60)
print("Example 3: Querying Effects")
print("=" * 60)

# Find all completed tasks
completed = list(shepherd.effects.query(TaskCompleted))
print(f"\nCompleted tasks: {len(completed)}")
for layer in completed:
    print(f"  - {layer.effect.task_name}")

# Find all tool calls
tools = list(shepherd.effects.query(ToolCallCompleted))
print(f"\nTool calls: {len(tools)}")
for layer in tools[:5]:  # Show first 5
    print(f"  - {layer.effect.tool_name}")

# =============================================================================
# Example 4: Handling Errors with Suggestions
# =============================================================================

print("\n" + "=" * 60)
print("Example 4: Error Handling Pattern")
print("=" * 60)

print("""
When a task fails, use this pattern:

    try:
        result = MyTask(...)
    except ShepherdError as e:
        print(e)                        # Includes suggestions
        print(e.debug_hint)             # Points to debugging tools
        print(shepherd.debug_summary())  # Execution timeline

        # Access suggestions programmatically
        if hasattr(e, 'suggestions'):
            for suggestion in e.suggestions:
                print(f"  - {suggestion}")
""")

# =============================================================================
# Example 5: VerboseConfig Options
# =============================================================================

print("\n" + "=" * 60)
print("Example 5: VerboseConfig Options")
print("=" * 60)

print("""
VerboseConfig controls what's shown during execution:

    VerboseConfig(
        enabled=True,              # Master switch
        show_prompts=True,         # Show prompts sent to LLM
        show_tool_calls=True,      # Show tool invocations
        show_tool_results=False,   # Tool results (can be verbose)
        show_thinking=True,        # Show agent thinking (dimmed)
        show_task_lifecycle=True,  # TaskStarted/Completed effects
        auto_debug_on_failure=True # Auto-print debug_summary on failure
    )

For full debugging visibility:
    VerboseConfig(enabled=True, show_prompts=True, show_tool_results=True)
""")

# =============================================================================
# Example 6: Common Issues and Fixes
# =============================================================================

print("\n" + "=" * 60)
print("Example 6: Common Issues and Fixes")
print("=" * 60)

print("""
Common issues and their fixes:

1. Task fails after previous task succeeded
   Fix: shepherd.reset()  # Start fresh session

2. "Command failed with exit code 1"
   Fix: Check shepherd.debug_summary() for last tool call
        Check error.stderr if available

3. Session/continuation errors
   Fix: session.debug_info()  # Check transcript size
        Consider session.fork() for fresh context

4. "Context not bound" / ContextResolutionError
   Fix: scope.bind("name", context) before task execution
        Check scope.bindings.keys() for bound contexts

See shepherd/docs/guides/debugging.md for the full debugging guide.
""")

# =============================================================================
# Example 7: Effect Filtering
# =============================================================================

print("\n" + "=" * 60)
print("Example 7: Effect Filtering")
print("=" * 60)

print("""
When debugging complex tasks, filter the effect stream to find what matters:

  scope.effects.direct()      # Only this scope's own effects
  scope.effects.summarized()  # Just TaskStarted/TaskCompleted boundaries
  scope.effects.by_depth(n)   # Limit how deep into child tasks to look
  scope.effects.query(Type)   # Filter by effect type

Example usage:
""")

# Demonstrate filtering on the current scope
all_effects = len(scope.effects)
direct = len(scope.effects.direct())
summaries = scope.effects.summarized()

print(f"  All effects:    {all_effects}")
print(f"  Direct only:    {direct} (excludes child task effects)")
print(f"  Summarized:     {len(summaries)} (task boundaries only)")

# Query by type
tool_calls = list(scope.effects.query(ToolCallCompleted))
completions = list(scope.effects.query(TaskCompleted))
print(f"  ToolCallCompleted: {len(tool_calls)}")
print(f"  TaskCompleted:     {len(completions)}")

# =============================================================================
# Summary
# =============================================================================

print("=" * 60)
print("Summary: Debugging Workflow")
print("=" * 60)

print("""
When a task fails, follow this sequence:

1. Read the error message
   └─ Check .suggestions for actionable fixes

2. Check execution timeline
   └─ shepherd.debug_summary()
   └─ Look for: last tool call, phase where error occurred

3. Check session state (if using sessions)
   └─ session.debug_info()
   └─ Look for: transcript size warnings

4. Try with fresh session
   └─ shepherd.reset()
   └─ Re-run the task

5. Enable verbose mode for detailed output
   └─ VerboseConfig(show_prompts=True, show_tool_results=True)
""")
