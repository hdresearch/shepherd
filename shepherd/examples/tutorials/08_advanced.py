"""Example 08: Advanced - Functional Style.

This tutorial covers the functional combinator layer for power users who need
custom combinators, complex composition, or direct async control.

Key concepts:
1. task_fn() - Adapt @task classes to combinator-compatible callables
2. Custom combinators - Create domain-specific workflow patterns
3. Async workflows - Explicit scope management with asyncio

When to use functional combinators:
- You need a combinator that doesn't exist (custom workflow patterns)
- You're building library/framework code
- You need explicit async control for integration with async systems
- You want maximum composability and testability

Most users should start with direct tasks and fluent Pipeline composition
(Tutorials 01-07). Functional combinators are the escape hatch.

Run with:
    uv run python shepherd/examples/tutorials/08_advanced.py

See Also:
    shepherd/docs/guides/functional-style.md - Comprehensive functional style guide
"""

import asyncio

import shepherd
from shepherd import (
    ClaudeProvider,
    Input,
    Output,
    Rejected,
    VerboseConfig,
    get_global_scope,
    retry,
    task,
    task_fn,
)
from pydantic import BaseModel

# =============================================================================
# Configuration
# =============================================================================

shepherd.configure(
    provider=ClaudeProvider(
        name="default",
        verbose=VerboseConfig(enabled=True),
    )
)

# =============================================================================
# Tasks for demonstration
# =============================================================================


@task
class AnalyzeText(BaseModel):
    """Analyze text and return a summary."""

    text: Input(str)
    summary: Output(str)
    sentiment: Output(str)


@task
class TranslateText(BaseModel):
    """Translate text to a target language."""

    text: Input(str)
    target_language: Input(str)
    translation: Output(str)


# =============================================================================
# Example 1: task_fn() Adaptation
# =============================================================================

print("=" * 60)
print("Example 1: task_fn() Adaptation")
print("=" * 60)

print("""
task_fn() adapts a @task class to a combinator-compatible callable:

    # The combinator signature: (inputs: dict, scope: Scope) -> Awaitable[T]
    AnalyzeFn = task_fn(AnalyzeText)

    # Now use directly with combinators
    ReliableAnalyze = retry(AnalyzeFn, max_attempts=3)
    GatedAnalyze = gate(AnalyzeFn, lambda r, e: "positive" in r.sentiment)

Note: Combinators auto-adapt @task classes, so this is equivalent:
    ReliableAnalyze = retry(AnalyzeText, max_attempts=3)

Use task_fn() explicitly when you need:
- A reusable variable with type hints
- Direct functional-style execution: await AnalyzeFn({...}, scope)
""")

# Demonstrate auto-adaptation (most common)
ReliableAnalyze = retry(AnalyzeText, max_attempts=2)

# Run via the functional interface: (inputs, scope) -> Awaitable[T]
scope = get_global_scope()
result = asyncio.run(ReliableAnalyze({"text": "Shepherd makes AI agents easy to build!"}, scope))

print(f"Summary: {result.summary}")
print(f"Sentiment: {result.sentiment}")

# For simpler cases, Pipeline wraps this pattern:
#   Pipeline(AnalyzeText).retry(2).run(text="...")  # Same result, sync-first

# =============================================================================
# Example 2: Custom Combinator
# =============================================================================

print("\n" + "=" * 60)
print("Example 2: Custom Combinator")
print("=" * 60)

print("""
Custom combinators let you create domain-specific workflow patterns.
The pattern: wrap a task, return a new async callable with enhanced behavior.

Example: A combinator that limits effect count before committing.
""")


def limit_effects(task_callable, max_effects: int):
    """Only commit if task produces fewer than max_effects effects.

    This demonstrates the fork/merge/discard pattern for effect isolation.
    """

    async def limited_task(inputs, scope):
        # Fork: create isolated child scope
        child = scope.fork()

        # Execute task in child (effects captured there)
        result = await task_callable(inputs, child)

        # Gate decision based on effect count
        effect_count = len(child.effects)
        if effect_count <= max_effects:
            # Merge: commit child effects to parent
            scope.merge(child)
            return result
        # Discard: abandon child effects
        child.discard()
        return Rejected(
            result,
            child.effects,
            f"Too many effects: {effect_count} > {max_effects}",
        )

    return limited_task


# Use the custom combinator
LimitedAnalyze = limit_effects(task_fn(AnalyzeText), max_effects=50)

# Run via the functional interface
scope = get_global_scope()
result = asyncio.run(LimitedAnalyze({"text": "Hello, functional world!"}, scope))

if isinstance(result, Rejected):
    print(f"Rejected: {result.reason}")
    print(f"Effects (not committed): {len(result.effects)}")
else:
    print(f"Accepted! Summary: {result.summary}")

# =============================================================================
# Example 3: Async Workflow with Explicit Scope
# =============================================================================

print("\n" + "=" * 60)
print("Example 3: Async Workflow with Explicit Scope")
print("=" * 60)

print("""
For complex async integrations, use explicit scope management.
This gives full control over effect flow and parallel execution.
""")


async def translation_workflow():
    """Translate text to multiple languages in sequence."""
    scope = get_global_scope()

    # Adapt task for functional use
    translate = task_fn(TranslateText)

    original = "The quick brown fox jumps over the lazy dog."
    languages = ["Spanish", "French"]
    translations = {}

    for lang in languages:
        result = await translate(
            {"text": original, "target_language": lang},
            scope,
        )
        translations[lang] = result.translation
        print(f"  {lang}: {result.translation[:50]}...")

    return translations


print("\nRunning async workflow...")
translations = asyncio.run(translation_workflow())
print(f"\nTranslated to {len(translations)} languages")

# =============================================================================
# Summary
# =============================================================================

print("\n" + "=" * 60)
print("Summary: When to Use Functional Combinators")
print("=" * 60)

print("""
Functional combinators are for:

  1. Custom combinators - Create domain-specific patterns like:
     - with_approval() - Human-in-the-loop approval
     - limit_effects() - Gate on effect count
     - with_timeout() - Custom timeout handling

  2. Complex composition - Combine multiple tasks:
     - sequence(A, B, C) - Chain tasks
     - parallel(A, B) - Run concurrently
     - branch(cond, if_true, if_false) - Conditional

  3. Async integration - When you need asyncio control:
     - Integration with async web frameworks
     - Parallel external API calls
     - Custom event loops

Most users should stay with direct instantiation and Pipeline.run().
Drop to functional combinators when you need their power.

See shepherd/docs/guides/functional-style.md for comprehensive coverage.
""")

print(f"Total effects: {len(shepherd.effects)}")

# =============================================================================
# Debugging Tips
# =============================================================================
# If something goes wrong:
#   print(shepherd.debug_summary())  # Execution timeline
#   Inspect scope.effects after each step
#   Check child.effects before merge/discard
#
# See Tutorial 06 and shepherd/docs/guides/debugging.md for comprehensive troubleshooting.
