"""Example 02: Contexts and Scopes.

Contexts are stateful resources shared across tasks. This tutorial covers:
1. Context() marker for declaring context dependencies
2. SessionState for conversation continuity
3. Two binding styles: shepherd.bind() and fluent syntax
4. Nested scopes with scope.new()
5. Effect access patterns and filtering (preview)

Run with:
    uv run python shepherd/examples/tutorials/02_contexts.py
"""

import shepherd
from shepherd import ClaudeProvider, Context, Input, Output, SessionState, VerboseConfig, scope, task
from pydantic import BaseModel

# =============================================================================
# Tasks
# =============================================================================


@task
class KnockKnockJokeSetup(BaseModel):
    """Given a topic, return a response to 'Who's there?'.

    Think through the possible punchlines, but don't choose one yet.

    Example:
    Input: "programming"
    Output: "Computer" <- a later step will choose the response to 'Computer who?'
    """

    topic: Input(str)
    session: Context(SessionState)
    whos_there: Output(str)


@task
class KnockKnockJokePunchline(BaseModel):
    """Return the punchline to '<output> who?'."""

    # Passing SessionState allows us to maintain conversation context
    session: Context(SessionState)
    punchline: Output(str)


# =============================================================================
# Provider configuration
# =============================================================================

shepherd.configure(
    provider=ClaudeProvider(
        name="joker",
        model="claude-sonnet-4-20250514",
        default_permission_mode="default",
        max_turns=10,
        verbose=VerboseConfig(enabled=True, show_thinking=True),
    )
)

# =============================================================================
# Example 1: Binding Contexts
# =============================================================================

print("=== Example 1: Binding Contexts ===\n")

# Two ways to bind a context:
#
# 1. Module-level helper (recommended for global scope):
#    session = shepherd.bind("session", SessionState())
#
# 2. Fluent syntax (for explicit scope control):
#    SessionState().bind(scope)
#
# Both return a ContextRef that auto-updates as effects are applied.

# Using shepherd.bind() - simpler for global scope
session = shepherd.bind("session", SessionState())

joke = KnockKnockJokeSetup(topic="cats")
punchline = KnockKnockJokePunchline()

print("Topic: cats")
print(f"Who's there: {joke.whos_there}")
print(f"Punchline: {punchline.punchline}")

# Show message history after first joke (global scope)
print("\n--- Message History (Global Scope) ---")
for msg in scope.get_messages():
    role = msg["role"]
    content = msg["content"][:80] + "..." if len(msg["content"]) > 80 else msg["content"]
    print(f"  [{role}]: {content}")

# =============================================================================
# Example 2: Run in nested scope using scope.new()
# =============================================================================

print("\n=== Example 2: Nested Scope (using scope.new()) ===\n")

# scope.new() creates a new scope that auto-nests within the current scope
# Inside the with block, `scope` refers to the inner scope
with scope.new():
    # This scope is automatically a child of the global scope
    # Bind a new session that shadows the global one
    SessionState().bind(scope)

    joke = KnockKnockJokeSetup(topic="dogs")
    punchline = KnockKnockJokePunchline()

    print("Topic: dogs")
    print(f"Who's there: {joke.whos_there}")
    print(f"Punchline: {punchline.punchline}")

    # Effects in this scope
    print(f"\nNested scope effects: {len(scope.effects)}")

    # Show message history within nested scope (only this scope's messages)
    print("\n--- Message History (Nested Scope Only) ---")
    for msg in scope.get_messages():
        role = msg["role"]
        content = msg["content"][:80] + "..." if len(msg["content"]) > 80 else msg["content"]
        print(f"  [{role}]: {content}")

# Back to global scope - effects propagate up
print(f"\nGlobal effects (includes nested): {len(shepherd.effects)}")

# Stack is popped: global scope only sees original cat joke messages
print("\n--- Message History (Global Scope - Original Messages) ---")
for msg in scope.get_messages():
    role = msg["role"]
    task_name = msg.get("task", "")
    content = msg["content"][:60] + "..." if len(msg["content"]) > 60 else msg["content"]
    print(f"  [{role}] ({task_name}): {content}")

# =============================================================================
# Effect Access Patterns
# =============================================================================
# Three ways to access effects (they're related by containment):
#
#   shepherd.effects   → Global scope's stream (all effects)
#   scope.effects     → A specific scope's stream
#   result.effects    → A task's stream (its child scope)
#
# Containment: result.effects ⊆ scope.effects ⊆ shepherd.effects
#
# Example:
#   joke.effects      → Effects from the KnockKnockJokeSetup task only
#   scope.effects     → Effects from the current scope (may include nested)
#   shepherd.effects   → All effects from all scopes

print("\n--- Effect Access Patterns ---")
print(f"joke.effects (task):     {len(joke.effects)} effects")
print(f"scope.effects (current): {len(scope.effects)} effects")
print(f"shepherd.effects (all):   {len(shepherd.effects)} effects")


# =============================================================================
# Example 3: Effect Filtering (Preview)
# =============================================================================
# Every task creates its own child scope. Scopes provide filtered views:
#   .direct()      → Only this scope's effects (excludes child tasks)
#   .summarized()  → Just task boundaries (TaskStarted/Completed)
#   .by_depth(n)   → Limit how deep into child tasks to look
#
# See Tutorial 06 for comprehensive debugging and inspection techniques.

print("\n=== Example 3: Effect Filtering (Preview) ===\n")

# Quick demonstration of filtering methods
with scope.new() as demo_scope:
    SessionState().bind(demo_scope)
    demo_joke = KnockKnockJokeSetup(topic="music")

    print("Topic: music")
    print(f"Who's there: {demo_joke.whos_there}")

    # Filtering in action
    all_effects = len(demo_scope.effects)
    direct_only = len(demo_scope.effects.direct())
    summaries = demo_scope.effects.summarized()

    print(f"\nAll effects: {all_effects}")
    print(f"Direct only: {direct_only} (excludes child task effects)")
    print(f"Summarized: {len(summaries)} (just TaskStarted/TaskCompleted)")

print("\n--- Summary ---")
print("Effect filtering methods:")
print("  .direct()      → Exclude child scope effects")
print("  .summarized()  → Just task boundaries")
print("  .by_depth(n)   → Limit nesting depth")
print("\nSee Tutorial 06 for comprehensive debugging techniques.")

# =============================================================================
# Debugging Tips
# =============================================================================
# If something goes wrong:
#   print(shepherd.debug_summary())  # Execution timeline
#   print(session.debug_info())     # Session state (if using SessionState)
#   shepherd.reset()                 # Start fresh (clears all state)
#
# See Tutorial 06 and shepherd/docs/guides/debugging.md for comprehensive troubleshooting.
