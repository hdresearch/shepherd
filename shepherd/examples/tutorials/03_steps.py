"""Example 03: Multi-Step Tasks with @step.

This tutorial covers:
1. Structured outputs with nested Pydantic models (warm-up)
2. @step decorator for LLM-powered methods
3. Step chaining: output of one step becomes input of next
4. Inline step syntax and step options

Run with:
    uv run python shepherd/examples/tutorials/03_steps.py
"""

from typing import Literal

import shepherd
from shepherd import ClaudeProvider, Input, Output, StepCompleted, StepStarted, step, task
from pydantic import BaseModel

shepherd.configure(provider=ClaudeProvider(name="default"))

# =============================================================================
# Warm-up: Structured Outputs
# =============================================================================
# Before diving into @step, let's see how @task handles complex output types.
# This works with any @task, not just multi-step ones.


class KnockKnockJoke(BaseModel):
    """A single knock-knock joke."""

    whos_there: str
    punchline: str


@task
class TellJokes(BaseModel):
    """Tell knock-knock jokes about a topic."""

    topic: Input(str)
    jokes: Output(list[KnockKnockJoke])  # Nested model in a list


print("=== Warm-up: Structured Outputs ===\n")

result = TellJokes(topic="programming")
for i, joke in enumerate(result.jokes, 1):
    print(f"{i}. Knock knock! / {joke.whos_there} / {joke.punchline}")

print(f"\nEffects: {len(result.effects)}")

# =============================================================================
# Example 1: Decorator-based steps
# =============================================================================


@task
class AnalyzeText(BaseModel):
    """Analyze text by detecting its language, translating, and summarizing."""

    text: Input(str)

    # Each @step is a separate LLM call
    # The docstring is the prompt, return type is the expected output
    @step
    def detect_language(self, text: str) -> str:
        """What language is this text written in? Return just the language name."""

    @step
    def translate_to_english(self, text: str, source_language: str) -> str:
        """Translate this text from the source language to English."""

    @step
    def summarize(self, text: str) -> str:
        """Summarize this text in one sentence."""

    # Outputs populated by execute()
    language: Output(str)
    translation: Output(str)
    summary: Output(str)

    def execute(self):
        # Steps execute eagerly and return typed values
        self.language = self.detect_language(self.text)
        self.translation = self.translate_to_english(self.text, self.language)
        self.summary = self.summarize(self.translation)


@task
class QuickAnalysis(BaseModel):
    """Analyze text using inline steps."""

    text: Input(str)
    sentiment: Output(Literal["positive", "negative", "neutral"])
    confidence: Output(Literal["low", "medium", "high"])

    def execute(self):
        # self.step[T](prompt, **kwargs) for one-off steps
        self.sentiment = self.step[Literal["positive", "negative", "neutral"]](
            "What is the sentiment of this text: {text}",
            text=self.text,
        )
        self.confidence = self.step[Literal["low", "medium", "high"]](
            "How confident are you in the sentiment '{sentiment}' for: {text}",
            text=self.text,
            sentiment=self.sentiment,
        )


@task
class RobustAnalysis(BaseModel):
    """Demonstrates advanced step configuration."""

    text: Input(str)
    category: Output(str)
    keywords: Output(list[str])

    # Retry on transient failures with exponential backoff
    @step(retries=2, retry_delay=0.5)
    def categorize(self, text: str) -> Literal["news", "opinion", "technical", "other"]:
        """Categorize this text into one of the categories."""

    # Custom timeout for potentially slow operations
    @step(timeout=60)
    def extract_keywords(self, text: str) -> list[str]:
        """Extract up to 5 keywords from this text."""

    def execute(self):
        self.category = self.categorize(self.text)
        self.keywords = self.extract_keywords(self.text)


# =============================================================================
# Example 1: Decorator-based steps
# =============================================================================

print("\n=== Example 1: Decorator-based steps ===\n")

result = AnalyzeText(text="Bonjour le monde! Comment ca va aujourd'hui?")

print(f"Language: {result.language}")
print(f"Translation: {result.translation}")
print(f"Summary: {result.summary}")

# =============================================================================
# Example 2: Inline step syntax
# =============================================================================

print("\n=== Example 2: Inline step syntax ===\n")

result = QuickAnalysis(text="This product is okay, nothing special.")

print(f"Sentiment: {result.sentiment}")
print(f"Confidence: {result.confidence}")

# =============================================================================
# Example 3: Advanced step options
# =============================================================================

print("\n=== Example 3: Advanced step options ===\n")

result = RobustAnalysis(
    text="The new Python 3.12 release includes significant performance improvements "
    "through the implementation of a specialized adaptive interpreter."
)

print(f"Category: {result.category}")
print(f"Keywords: {result.keywords}")

# =============================================================================
# Effects captured
# =============================================================================

print("\n=== Effects ===")
print(f"Total effects: {len(shepherd.effects)}")

# Count step effects
step_starts = list(shepherd.effects.query(StepStarted))
step_completes = list(shepherd.effects.query(StepCompleted))
print(f"Steps started: {len(step_starts)}")
print(f"Steps completed: {len(step_completes)}")

# =============================================================================
# Debugging Tips
# =============================================================================
# If something goes wrong:
#   print(shepherd.debug_summary())  # Shows step-by-step execution
#   Each step emits StepStarted/StepCompleted/StepFailed effects
#
# See Tutorial 06 and shepherd/docs/guides/debugging.md for comprehensive troubleshooting.
