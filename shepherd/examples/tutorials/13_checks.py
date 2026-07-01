"""Example 13: Declarative Checks.

Validate inputs before execution and outputs after — without writing
boilerplate.  Checks are the task author's way to declare "this must be
true" so consumers get clear errors instead of wasted LLM calls or
hallucinated output.

Key concepts:
1. Check() marker — attach predicates to Input() and Output() fields
2. Builtin factories — FileExists, NonEmpty, InRange, Matches, MaxLength
3. Preconditions — checks on inputs run *before* execution
4. Postconditions — checks on outputs run *after* execution
5. Fail-fast semantics — CheckFailedError with clear, formatted messages
6. Fork/merge isolation — failed checks discard all effects cleanly

Run with:
    uv run python shepherd/examples/tutorials/13_checks.py
"""

import asyncio
import contextlib
from typing import Annotated

from shepherd import (
    Check,
    CheckFailedError,
    FileExists,
    Input,
    InRange,
    Matches,
    MaxLength,
    NonEmpty,
    Output,
    Scope,
    task,
)
from shepherd_tests import MockProvider
from pydantic import BaseModel

# =============================================================================
# Helper — configure scope with MockProvider + canned responses
# =============================================================================


def make_scope(*responses: dict) -> Scope:
    """Return a root Scope wired to a MockProvider with the given responses."""
    provider = MockProvider(mock_responses=[{"structured": r} for r in responses])
    scope = Scope(root=True)
    scope.register_provider("default", provider, default=True)
    return scope


# =============================================================================
# Example 1: Preconditions — catch bad inputs before calling the LLM
# =============================================================================
# FileExists() prevents a wasted LLM call when the user passes a path
# that doesn't exist.  The task never executes; the error message tells
# the consumer exactly what went wrong.


@task
class SummarizeFile(BaseModel):
    """Summarize the contents of a file."""

    path: Annotated[Input(str), FileExists()]
    summary: Output(str)


print("=== Example 1: Precondition — FileExists ===\n")

scope = make_scope({"summary": "Great content."})
with scope:
    try:
        SummarizeFile(path="/tmp/nonexistent_file.txt")
    except CheckFailedError as e:
        print(f"Caught: {e}")
        print(f"  Phase : {e.phase}")  # "precondition"
        print(f"  Field : {e.field_name}")  # "path"
        print(f"  Value : {e.value}")
print()


# =============================================================================
# Example 2: NonEmpty guards both sides
# =============================================================================
# NonEmpty() on an Input rejects blanks before the LLM is ever called.
# NonEmpty() on an Output catches empty/hallucinated responses after.


@task
class GenerateTagline(BaseModel):
    """Generate a catchy marketing tagline for a product."""

    product: Annotated[Input(str), NonEmpty()]
    tagline: Annotated[Output(str), NonEmpty(), MaxLength(100)]


print("=== Example 2: NonEmpty + MaxLength ===\n")

# 2a — precondition: blank product
scope = make_scope({"tagline": "Hydrate smarter."})
with scope:
    try:
        GenerateTagline(product="   ")
    except CheckFailedError as e:
        print(f"Empty input caught: {e.phase} — {e.field_name}")

# 2b — happy path with valid input
scope = make_scope({"tagline": "Hydrate smarter."})
with scope:
    result = GenerateTagline(product="smart water bottle")
    print(f"Tagline: {result.tagline}")
    print(f"Length : {len(result.tagline)} chars (max 100)")

# 2c — postcondition: LLM returns empty string
scope = make_scope({"tagline": ""})
with scope:
    try:
        GenerateTagline(product="smart water bottle")
    except CheckFailedError as e:
        print(f"Empty output caught: {e.phase} — {e.field_name}")
print()


# =============================================================================
# Example 3: Numeric range checks
# =============================================================================
# InRange() is perfect for scores, ratings, and probabilities.
# The bounds are inclusive and either side is optional.


@task
class ScoreRelevance(BaseModel):
    """Score how relevant a document is to a query."""

    query: Annotated[Input(str), NonEmpty()]
    document: Annotated[Input(str), NonEmpty()]
    score: Annotated[Output(float), InRange(0.0, 1.0)]


print("=== Example 3: InRange postcondition ===\n")

# 3a — valid score
scope = make_scope({"score": 0.87})
with scope:
    result = ScoreRelevance(
        query="climate change mitigation",
        document="Recent policy proposals aim to reduce carbon emissions by 50% by 2030.",
    )
    print(f"Relevance score: {result.score}")

# 3b — LLM returns out-of-range value
scope = make_scope({"score": 2.5})
with scope:
    try:
        ScoreRelevance(query="test", document="test doc")
    except CheckFailedError as e:
        print(f"Out of range: {e}")
print()


# =============================================================================
# Example 4: Pattern matching
# =============================================================================
# Matches() checks that a string matches a regex pattern.
# Useful for validating structured output formats.


@task
class ExtractVersion(BaseModel):
    """Extract the semantic version string from release notes."""

    notes: Input(str)
    version: Annotated[Output(str), Matches(r"^\d+\.\d+\.\d+$")]


print("=== Example 4: Matches postcondition ===\n")

# 4a — correct format
scope = make_scope({"version": "2.4.1"})
with scope:
    result = ExtractVersion(notes="Release v2.4.1 — fixes critical auth bug.")
    print(f"Extracted version: {result.version}")

# 4b — LLM includes the "v" prefix
scope = make_scope({"version": "v2.4.1"})
with scope:
    try:
        ExtractVersion(notes="Release v2.4.1 — fixes critical auth bug.")
    except CheckFailedError as e:
        print(f"Bad format: {e}")
print()


# =============================================================================
# Example 5: Multiple checks on one field
# =============================================================================
# Stack checks in Annotated[] — they run in order, first failure stops.


@task
class GenerateSlug(BaseModel):
    """Generate a URL slug for an article title."""

    title: Annotated[Input(str), NonEmpty()]
    slug: Annotated[
        Output(str),
        NonEmpty(),
        MaxLength(60),
        Matches(r"^[a-z0-9]+(-[a-z0-9]+)*$", message="Slug must be lowercase-hyphenated: got {value!r}"),
    ]


print("=== Example 5: Multiple stacked checks ===\n")

# 5a — passes all three checks
scope = make_scope({"slug": "10-tips-for-better-python-performance"})
with scope:
    result = GenerateSlug(title="10 Tips for Better Python Performance")
    print(f"Slug: {result.slug}")

# 5b — uppercase fails the Matches check (third in the stack)
scope = make_scope({"slug": "10-Tips-For-Better"})
with scope:
    try:
        GenerateSlug(title="10 Tips for Better Python Performance")
    except CheckFailedError as e:
        print(f"Stacked check failed: {e}")
print()


# =============================================================================
# Example 6: Custom predicates
# =============================================================================
# Check() accepts any callable.  Write domain-specific validation
# without subclassing anything.


def is_valid_email(value: str) -> bool:
    """Basic email format check."""
    return "@" in value and "." in value.rsplit("@", maxsplit=1)[-1]


@task
class ExtractContactEmail(BaseModel):
    """Extract the primary contact email from a company page."""

    page_text: Annotated[Input(str), NonEmpty()]
    email: Annotated[
        Output(str),
        Check(is_valid_email, message="Invalid email format: {value!r}"),
    ]


print("=== Example 6: Custom predicate ===\n")

scope = make_scope({"email": "support@example.com"})
with scope:
    result = ExtractContactEmail(page_text="Contact us at support@example.com or call 555-0100.")
    print(f"Email: {result.email}")

scope = make_scope({"email": "not-an-email"})
with scope:
    try:
        ExtractContactEmail(page_text="Contact us at the front desk.")
    except CheckFailedError as e:
        print(f"Bad email: {e}")
print()


# =============================================================================
# Example 7: Checks with custom execute()
# =============================================================================
# Postcondition checks work with custom execute() methods too.
# The checks run *after* execute() returns, validating whatever
# the method wrote to the output fields.


@task
class ComputeStats(BaseModel):
    """Compute statistics for a list of numbers."""

    values: Input(list[float])
    mean: Annotated[Output(float), InRange(min_val=-1e9, max_val=1e9)]
    count: Annotated[Output(int), InRange(min_val=1)]

    def execute(self) -> None:
        self.mean = sum(self.values) / len(self.values)
        self.count = len(self.values)


print("=== Example 7: Checks with custom execute() ===\n")

scope = make_scope()  # no LLM call needed — custom execute
with scope:
    result = ComputeStats(values=[3.5, 7.2, 1.8, 9.4, 5.1])
    print(f"Mean  : {result.mean:.2f}")
    print(f"Count : {result.count}")

# Postcondition catches an empty list (count < 1)
scope = make_scope()
with scope:
    try:
        ComputeStats(values=[])
    except (CheckFailedError, ZeroDivisionError) as e:
        print(f"Bad input caught: {type(e).__name__}: {e}")
print()


# =============================================================================
# Example 8: Async path — checks work identically with arun()
# =============================================================================


@task
class AsyncAnalyze(BaseModel):
    """Analyze sentiment asynchronously."""

    text: Annotated[Input(str), NonEmpty()]
    sentiment: Annotated[Output(str), NonEmpty(), Matches(r"^(positive|negative|neutral)$")]


async def demo_async():
    """Show that checks work the same way through arun()."""
    print("=== Example 8: Async path ===\n")

    # 8a — happy path
    scope = make_scope({"sentiment": "positive"})
    async with scope:
        result = await AsyncAnalyze.arun(scope=scope, text="I love how clean this API is!")
        print(f"Sentiment: {result.sentiment}")

    # 8b — precondition failure in async
    scope = make_scope({"sentiment": "positive"})
    async with scope:
        try:
            await AsyncAnalyze.arun(scope=scope, text="")
        except CheckFailedError as e:
            print(f"Async precondition: {e.phase} — {e.field_name}")

    # 8c — postcondition failure in async
    scope = make_scope({"sentiment": "kinda_happy"})
    async with scope:
        try:
            await AsyncAnalyze.arun(scope=scope, text="I love this!")
        except CheckFailedError as e:
            print(f"Async postcondition: {e.phase} — {e.field_name}")

    print()


asyncio.run(demo_async())


# =============================================================================
# Example 9: Isolation — failed checks leave no trace
# =============================================================================
# When a check fails, the framework discards the entire fork.
# The parent scope sees no effects from the aborted execution.


print("=== Example 9: Fork/merge isolation ===\n")

scope = make_scope({"tagline": ""})  # LLM returns empty → postcondition fails
with scope:
    before = len(scope.effects)
    with contextlib.suppress(CheckFailedError):
        GenerateTagline(product="smart bottle")
    after = len(scope.effects)
    print(f"Effects before: {before}")
    print(f"Effects after : {after}")
    print("Failed task left zero effects in parent scope.")


# =============================================================================
# What's Next
# =============================================================================
# Checks make packaged tasks self-documenting and self-validating.
# Consumers get clear errors like "File does not exist: /bad/path"
# instead of burning an LLM call or receiving hallucinated output.
#
# Builtin checks cover the most common patterns:
#   FileExists()  — path must exist on disk
#   NonEmpty()    — rejects None, empty strings/whitespace, empty collections
#   InRange()     — inclusive numeric bounds (either side optional)
#   Matches()     — regex via re.search
#   MaxLength()   — len(value) <= limit
#
# For anything else, pass a callable to Check():
#   Check(my_predicate, message="Explain {value} for {field}")
#
# In production, swap MockProvider for ClaudeProvider:
#   import shepherd
#   from shepherd import ClaudeProvider
#   shepherd.configure(provider=ClaudeProvider(name="default"))
#
# See also:
#   - Tutorial 01: Simple tasks (Input/Output basics)
#   - Tutorial 03: Multi-step tasks with @step
#   - Tutorial 08: Advanced patterns and combinators
