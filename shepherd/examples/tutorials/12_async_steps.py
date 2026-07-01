"""Example 12: Async Steps.

This tutorial covers:
1. Async step execution — await @step methods from async execute()
2. Parallel steps — fan out multiple LLM calls with asyncio.gather()
3. Async inline steps — await self.step[T](...) syntax

Run with:
    uv run python shepherd/examples/tutorials/12_async_steps.py
"""

import asyncio
from typing import Literal

import shepherd
from shepherd import ClaudeProvider, Input, Output, StepCompleted, StepStarted, step, task
from pydantic import BaseModel

shepherd.configure(provider=ClaudeProvider(name="default"))


# =============================================================================
# Example 1: Basic async steps
# =============================================================================


@task
class AsyncAnalyze(BaseModel):
    """Analyze text using async steps — each step awaits the LLM directly."""

    text: Input(str)
    language: Output(str)
    summary: Output(str)

    @step
    def detect_language(self, text: str) -> str:
        """What language is this text written in? Return just the language name."""

    @step
    def summarize(self, text: str) -> str:
        """Summarize this text in one sentence."""

    async def execute(self):
        self.language = await self.detect_language(self.text)
        self.summary = await self.summarize(self.text)


# =============================================================================
# Example 2: Parallel steps with asyncio.gather
# =============================================================================


@task
class ParallelAnalyze(BaseModel):
    """Run multiple analyses concurrently — no thread overhead."""

    text: Input(str)
    sentiment: Output(Literal["positive", "negative", "neutral"])
    category: Output(Literal["news", "opinion", "technical", "other"])
    keywords: Output(list[str])

    @step
    def detect_sentiment(self, text: str) -> Literal["positive", "negative", "neutral"]:
        """What is the sentiment of this text?"""

    @step
    def categorize(self, text: str) -> Literal["news", "opinion", "technical", "other"]:
        """Categorize this text."""

    @step
    def extract_keywords(self, text: str) -> list[str]:
        """Extract up to 5 keywords from this text."""

    async def execute(self):
        # All three LLM calls run concurrently on the event loop
        self.sentiment, self.category, self.keywords = await asyncio.gather(
            self.detect_sentiment(self.text),
            self.categorize(self.text),
            self.extract_keywords(self.text),
        )


# =============================================================================
# Example 3: Async inline steps
# =============================================================================


@task
class AsyncInline(BaseModel):
    """Inline steps also work with await."""

    text: Input(str)
    sentiment: Output(Literal["positive", "negative", "neutral"])
    confidence: Output(Literal["low", "medium", "high"])

    async def execute(self):
        self.sentiment = await self.step[Literal["positive", "negative", "neutral"]](
            "What is the sentiment of: {text}",
            text=self.text,
        )
        self.confidence = await self.step[Literal["low", "medium", "high"]](
            "How confident are you in '{sentiment}' for: {text}",
            text=self.text,
            sentiment=self.sentiment,
        )


async def main():  # noqa: D103
    print("=== Example 1: Basic async steps ===\n")
    result = await AsyncAnalyze.arun(text="Bonjour le monde!")
    print(f"Language: {result.language}")
    print(f"Summary: {result.summary}")

    print("\n=== Example 2: Parallel steps ===\n")
    result = await ParallelAnalyze.arun(
        text="The new Python 3.12 release includes significant performance improvements."
    )
    print(f"Sentiment: {result.sentiment}")
    print(f"Category: {result.category}")
    print(f"Keywords: {result.keywords}")

    print("\n=== Example 3: Async inline steps ===\n")
    result = await AsyncInline.arun(text="This product is okay, nothing special.")
    print(f"Sentiment: {result.sentiment}")
    print(f"Confidence: {result.confidence}")

    # Effects
    print("\n=== Effects ===")
    print(f"Total effects: {len(shepherd.effects)}")
    step_starts = list(shepherd.effects.query(StepStarted))
    step_completes = list(shepherd.effects.query(StepCompleted))
    print(f"Steps started: {len(step_starts)}")
    print(f"Steps completed: {len(step_completes)}")


asyncio.run(main())
