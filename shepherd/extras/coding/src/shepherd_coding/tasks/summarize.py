"""Summarize task — holistic judgment from a list of findings.

Function-form (DECISIONS D5 / Tranche 7): the previous class-form
``@task class Summarize(BaseModel)`` is replaced with the function-form
``@task async def summarize(...) -> SummaryResult`` shape per CONTRACTS A4.

Produces a narrative summary, quality score, and verdict from findings
aggregated across multiple sources (LLM analyzers, programmatic tools,
human reviewers). Enables the decomposed pipeline path:
  AnalyzeCode x N -> ValidateFindings -> summarize
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from shepherd_runtime.nucleus import deliver, task
from shepherd_runtime.task.markers import InputMarker

_GUIDANCE = """\
You are producing a holistic assessment of code change quality based on
analysis findings.

You will receive:
1. A list of findings from automated analyzers and/or tool checks
2. A file change summary showing what files were modified
3. Optional PR context (title, author, labels)
4. Optional verification results (build/test outcomes)

Your job:
- Weigh the findings by severity: blockers and errors are critical,
  warnings are important, suggestions and nits are advisory.
- Assess the overall change quality.
- Produce a concise 2-3 sentence summary.
- Assign a quality score from 0.0 to 1.0.
- Choose a verdict: APPROVE, REQUEST_CHANGES, or COMMENT.

Guidelines:
- APPROVE: No blockers or errors, at most minor warnings. Score >= 0.7.
- REQUEST_CHANGES: Has blockers or multiple errors. Score < 0.5.
- COMMENT: Has warnings but no blockers. 0.5 <= score < 0.7.
- Empty findings with passing verification = APPROVE with score ~0.9.
- Do NOT invent new findings.
"""


@dataclass(frozen=True)
class SummaryResult:
    """Holistic assessment of code change quality."""

    summary: str = ""
    score: float = 0.0
    verdict: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"] = "COMMENT"


@task(guidance=_GUIDANCE)
async def summarize(
    findings_text: Annotated[
        str,
        InputMarker(description="Formatted findings from analyzers/tools"),
    ] = "",
    file_change_summary: Annotated[
        str,
        InputMarker(description="File list with change status"),
    ] = "",
    pr_context: Annotated[
        str | None,
        InputMarker(description="Formatted PR metadata"),
    ] = None,
    verification_results: Annotated[
        str | None,
        InputMarker(description="Build/test outcomes"),
    ] = None,
) -> SummaryResult:
    """Produce a holistic assessment of code change quality."""
    return await deliver(
        SummaryResult,
        goal=(
            "Weigh the findings, assess overall quality, and produce a "
            "concise 2-3 sentence summary, a quality score, and a "
            "verdict (APPROVE / REQUEST_CHANGES / COMMENT)."
        ),
        evidence=[
            f"findings_text={findings_text}",
            f"file_change_summary={file_change_summary}",
            f"pr_context={pr_context}",
            f"verification_results={verification_results}",
        ],
    )


__all__ = ["SummaryResult", "summarize"]
