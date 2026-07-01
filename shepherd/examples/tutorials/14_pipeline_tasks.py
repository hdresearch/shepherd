"""Example 14: Pipeline Tasks.

Multi-step workflows as @task classes with run_stage orchestration.
Demonstrates data threading between stages, error policies, conditional
execution, inter-stage scope bindings, and stage-level observability.

Key concepts:
1. run_stage — execute named subtasks with retry, timeout, and error policies
2. OnError — declarative error handling (fatal, skip, default, continue_with)
3. Stage effects — StageStarted/StageCompleted for pipeline observability
4. self.scope — bind contexts between stages for downstream resolution
5. self.stages — post-execution introspection of per-stage results
6. Mixed execution — programmatic stages alongside LLM-powered stages

The pipeline processes a document through four stages:
  Parse (programmatic) -> Classify (LLM) -> Summarize (programmatic) -> Publish (programmatic)

Prerequisites:
- ANTHROPIC_API_KEY in environment or .env file

Run with:
    uv run python shepherd/examples/tutorials/14_pipeline_tasks.py
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel

# Add repository root to path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

load_dotenv(_repo_root / ".env")
load_dotenv()

import shepherd
from shepherd import (
    Input,
    OnError,
    Output,
    StageCompleted,
    StageSkipped,
    StageStarted,
    task,
)

if os.environ.get("ANTHROPIC_API_KEY"):
    from shepherd import ClaudeProvider, VerboseConfig

    shepherd.configure(
        provider=ClaudeProvider(
            name="default",
            model="claude-haiku-4-5",
            verbose=VerboseConfig(enabled=True),
        )
    )
    _USE_LLM = True
    print("Using Claude Haiku for classification stage.\n")
else:
    from shepherd_tests import MockProvider

    shepherd.configure(
        provider=MockProvider(
            mock_responses=[
                {"structured": {"category": "report", "priority": "high"}},
                {"structured": {"category": "other", "priority": "low"}},
            ]
        )
    )
    _USE_LLM = False
    print("No ANTHROPIC_API_KEY — using MockProvider for classification.\n")


# =============================================================================
# Stage Tasks
# =============================================================================


@task
class ParseDocument(BaseModel):
    """Extract metadata from a raw document."""

    raw_text: Input(str)
    word_count: Output(int) = None
    first_paragraph: Output(str) = None
    has_title: Output(bool) = None

    def execute(self) -> None:
        lines = self.raw_text.strip().split("\n")
        words = self.raw_text.split()
        self.word_count = len(words)
        self.first_paragraph = lines[0].strip() if lines else ""
        self.has_title = bool(lines and lines[0].strip() and len(lines[0]) < 100)


@task
class ClassifyDocument(BaseModel):
    """Classify a document based on its content.

    You are given the first paragraph of a document and its word count.
    Classify it into exactly one category and assign a priority.

    Respond with:
    - category: one of "article", "report", "memo", "other"
    - priority: one of "high", "medium", "low"
    """

    first_paragraph: Input(str)
    word_count: Input(int)
    category: Output(Literal["article", "report", "memo", "other"]) = None
    priority: Output(Literal["high", "medium", "low"]) = None


@task
class SummarizeDocument(BaseModel):
    """Generate a summary from parsed metadata and classification."""

    first_paragraph: Input(str)
    word_count: Input(int)
    category: Input(str)
    summary: Output(str) = None

    def execute(self) -> None:
        self.summary = f"[{self.category.upper()}] ({self.word_count} words) {self.first_paragraph[:80]}..."


@task
class PublishDocument(BaseModel):
    """Format and publish the final output."""

    summary: Input(str)
    category: Input(str)
    priority: Input(str)
    formatted: Output(str) = None
    published: Output(bool) = None

    def execute(self) -> None:
        self.formatted = (
            f"{'=' * 50}\n"
            f"  Category: {self.category}\n"
            f"  Priority: {self.priority}\n"
            f"  Summary:  {self.summary}\n"
            f"{'=' * 50}"
        )
        self.published = True


# =============================================================================
# Pipeline Task
# =============================================================================


@task
class DocumentPipeline(BaseModel):
    """Process a document through parse, classify, summarize, and publish.

    Demonstrates:
    - Data threading: each stage receives outputs from prior stages
    - Error policies: classify falls back to defaults if the LLM fails;
      publish uses continue_with so failures don't lose the summary
    - Conditional execution: summarize is skipped for very short documents
    - Stage effects: StageStarted/StageCompleted emitted for each stage
    """

    raw_text: Input(str)
    min_words_for_summary: Input(int) = 20
    final_output: Output(str) = None
    category: Output(str) = None
    was_summarized: Output(bool) = None

    async def execute(self) -> None:
        # Stage 1: Parse (programmatic — no LLM needed)
        parsed = await self.run_stage(
            "parse",
            ParseDocument,
            raw_text=self.raw_text,
        )

        # Stage 2: Classify (LLM-powered — uses the provider)
        # Falls back to defaults if the LLM call fails
        classified = await self.run_stage(
            "classify",
            ClassifyDocument,
            first_paragraph=parsed.first_paragraph,
            word_count=parsed.word_count,
            retry=1,
            on_error=OnError.default(category="other", priority="medium"),
        )

        # Stage 3: Summarize (programmatic — conditional on word count)
        if parsed.word_count >= self.min_words_for_summary:
            summarized = await self.run_stage(
                "summarize",
                SummarizeDocument,
                first_paragraph=parsed.first_paragraph,
                word_count=parsed.word_count,
                category=classified.category,
            )
            summary_text = summarized.summary
            self.was_summarized = True
        else:
            self.scope.emit(
                StageSkipped(
                    stage_name="summarize",
                    task_name=self._task_name,
                    reason="Document too short",
                )
            )
            summary_text = parsed.first_paragraph
            self.was_summarized = False

        # Stage 4: Publish (programmatic — continues even if it fails)
        published = await self.run_stage(
            "publish",
            PublishDocument,
            summary=summary_text,
            category=classified.category,
            priority=classified.priority,
            on_error=OnError.continue_with(formatted="[publish failed]", published=False),
        )

        self.final_output = published.formatted
        self.category = classified.category


# =============================================================================
# Run the Pipeline
# =============================================================================


SAMPLE_DOCUMENT = """\
Quarterly Engineering Report: Q1 2026

The engineering team shipped 14 features across three product lines during Q1.
Infrastructure reliability improved from 99.2% to 99.8% uptime, driven by the
migration to the new container orchestration platform. The team grew from 12
to 15 engineers with three new hires starting in February.

Key highlights include the launch of the real-time analytics dashboard, which
reduced median query latency from 340ms to 45ms, and the completion of the
SOC 2 Type II audit with zero critical findings.
"""


async def main() -> None:  # noqa: D103
    # =========================================================================
    # Section 1: Run the pipeline
    # =========================================================================
    print(f"\n{'=' * 60}")
    print("  Section 1: Document Processing Pipeline")
    print(f"{'=' * 60}\n")

    result = await DocumentPipeline.arun(
        raw_text=SAMPLE_DOCUMENT,
        min_words_for_summary=20,
    )

    print(f"Category:     {result.category}")
    print(f"Summarized:   {result.was_summarized}")
    print(f"\n{result.final_output}")

    # =========================================================================
    # Section 2: Inspect stage results via self.stages
    # =========================================================================
    print(f"\n{'=' * 60}")
    print("  Section 2: Stage Results (self.stages)")
    print(f"{'=' * 60}\n")

    for name, stage_result in result.stages.items():
        if stage_result is None:
            print(f"  {name}: (skipped)")
        else:
            outputs = {
                k: getattr(stage_result, k, None)
                for k in ("word_count", "category", "priority", "summary", "published")
                if hasattr(stage_result, k) and getattr(stage_result, k) is not None
            }
            print(f"  {name}: {outputs}")

    # =========================================================================
    # Section 3: Inspect the effect stream
    # =========================================================================
    print(f"\n{'=' * 60}")
    print("  Section 3: Stage Effects (observability)")
    print(f"{'=' * 60}\n")

    for layer in result.effects.layers:
        eff = layer.effect
        if isinstance(eff, StageStarted):
            print(f"  -> StageStarted({eff.stage_name!r})")
        elif isinstance(eff, StageCompleted):
            flags = []
            if eff.defaulted:
                flags.append("defaulted")
            if eff.partial:
                flags.append("partial")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            print(f"  <- StageCompleted({eff.stage_name!r}, {eff.duration_ms:.0f}ms{flag_str})")
        elif isinstance(eff, StageSkipped):
            print(f"  -- StageSkipped({eff.stage_name!r}, reason={eff.reason!r})")

    # =========================================================================
    # Section 4: Conditional skip — short document
    # =========================================================================
    print(f"\n{'=' * 60}")
    print("  Section 4: Short Document (summarize skipped)")
    print(f"{'=' * 60}\n")

    short_result = await DocumentPipeline.arun(
        raw_text="Quick status update: all systems operational.",
        min_words_for_summary=20,
    )

    print(f"Category:     {short_result.category}")
    print(f"Summarized:   {short_result.was_summarized}")
    print(f"\n{short_result.final_output}")

    print("\nStage effects:")
    for layer in short_result.effects.layers:
        eff = layer.effect
        if isinstance(eff, (StageStarted, StageCompleted, StageSkipped)):
            if isinstance(eff, StageStarted):
                print(f"  -> {eff.stage_name}")
            elif isinstance(eff, StageCompleted):
                print(f"  <- {eff.stage_name} ({eff.duration_ms:.0f}ms)")
            elif isinstance(eff, StageSkipped):
                print(f"  -- {eff.stage_name} (skipped: {eff.reason})")


if __name__ == "__main__":
    asyncio.run(main())
