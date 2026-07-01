"""Simple task examples that don't require fixtures.

This example shows the core syntax and execution flow without
any workspace or fixture dependencies. Good for quick testing.

Usage:
    uv run python shepherd/examples/scenarios/simple_tasks.py
    uv run python shepherd/examples/scenarios/simple_tasks.py --mock  # Run without API calls
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

# Add Shepherd project root to path for example helper imports
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import shepherd
from shepherd import ClaudeProvider, Input, Output, Scope, task
from shepherd_tests import MockProvider
from pydantic import BaseModel, Field

from examples.utils import print_header, print_section

# =============================================================================
# Simple Tasks (no tools needed)
# =============================================================================


@task
class TellJoke(BaseModel):
    """Tell a joke about a given topic."""

    topic: Annotated[Input(str), Field(description="Topic of the joke")]
    joke: Annotated[Output(str), Field(description="The joke")]


@task
class RateJoke(BaseModel):
    """Rate how funny a joke is on a scale of 0-10."""

    joke: Annotated[Input(str), Field(description="The joke to rate")]
    rating: Annotated[
        Output(int),
        Field(description="Rating from 0-10, where 10 is the funniest", ge=0, le=10),
    ]
    explanation: Annotated[
        Output(str),
        Field(description="Brief explanation of the rating"),
    ]


@task
class TranslateText(BaseModel):
    """Translate text from one language to another."""

    text: Annotated[Input(str), Field(description="Text to translate")]
    source_language: Annotated[Input(str), Field(description="Source language")]
    target_language: Annotated[Input(str), Field(description="Target language")]
    translation: Annotated[Output(str), Field(description="Translated text")]


@task(guidance="Be concise and technical. Use bullet points.")
class ExplainConcept(BaseModel):
    """Explain a technical concept in simple terms."""

    concept: Annotated[Input(str), Field(description="The concept to explain")]
    audience: Annotated[
        Input(str),
        Field(default="software engineer", description="Target audience"),
    ]
    explanation: Annotated[Output(str), Field(description="Clear explanation")]
    key_points: Annotated[
        Output(list[str]),
        Field(description="3-5 key takeaways"),
    ]


# =============================================================================
# Demo
# =============================================================================


def main() -> int:
    """Run simple task examples."""
    print_header("Shepherd Framework - Simple Tasks Demo")

    # Check for mock mode
    mock_mode = "--mock" in sys.argv
    if mock_mode:
        print("\n[Running in mock mode - no API calls]")
        return run_with_mock()

    # Configure the provider
    shepherd.configure(provider=ClaudeProvider(name="default"))
    return run_examples()


def run_with_mock() -> int:
    """Run examples in mock mode using MockProvider."""
    with Scope(root=True) as scope:
        scope.register_provider("default", MockProvider(name="mock"), default=True)
        return run_examples()


def run_examples() -> int:
    """Run the actual examples."""
    # Example 1: Tell a joke
    print_section("Example 1: Tell a Joke")
    joke_result = TellJoke(topic="programming")
    print("\nTopic: programming")
    print(f"Joke: {joke_result.joke}")
    print(f"\nEffects: {len(shepherd.effects)} total")
    for layer in list(shepherd.effects)[:10]:
        print(f"  [{layer.sequence}] {type(layer.effect).__name__}")

    # Example 2: Rate the joke
    print_section("Example 2: Rate the Joke")
    rating_result = RateJoke(joke=joke_result.joke)
    print(f"\nRating: {rating_result.rating}/10")
    print(f"Explanation: {rating_result.explanation}")

    # Example 3: Chained tasks
    print_section("Example 3: Chained Tasks")

    translation = TranslateText(
        text="Hello, World!",
        source_language="English",
        target_language="Japanese",
    )
    print("\nOriginal: Hello, World!")
    print(f"Japanese: {translation.translation}")

    explanation = ExplainConcept(
        concept="effect systems in functional programming",
        audience="Python developer",
    )
    print("\nConcept: effect systems in functional programming")
    print(f"Explanation: {explanation.explanation[:200]}...")
    print("\nKey points:")
    for point in explanation.key_points:
        print(f"  - {point}")

    print_section("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
