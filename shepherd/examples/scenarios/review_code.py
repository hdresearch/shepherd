"""Code review scenario with structured findings.

This scenario is intended to demonstrate:
1. Structured outputs (ReviewFinding model)
2. Read-only workspace analysis using git diff
3. Effect stream inspection

Usage:
    uv run python shepherd/examples/scenarios/review_code.py
"""

from __future__ import annotations

import atexit
import sys
from pathlib import Path
from typing import Annotated, Literal

# Add Shepherd project root to path for example helper imports
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from examples.utils import (
    cleanup_workspace,
    generate_scenario_workspace,
    print_example_outcome,
    print_header,
    print_section,
    require_gitpython,
)
from pydantic import BaseModel, Field

import shepherd
from shepherd import (
    ClaudeProvider,
    Context,
    Input,
    Output,
    ToolCallCompleted,
    VerboseConfig,
    WorkspaceRef,
    task,
)

# =============================================================================
# Structured Output Models
# =============================================================================


class ReviewFinding(BaseModel):
    """A single finding from a code review."""

    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str  # e.g., "bug", "style", "performance", "security"
    file_path: str
    line_number: int | None = None
    description: str
    suggestion: str | None = None


# =============================================================================
# Task Definition
# =============================================================================


@task(guidance="You are a thorough code reviewer. Focus on bugs, security, and maintainability.")
class ReviewCode(BaseModel):
    """Review code changes and provide structured feedback.

    Analyze the code changes for bugs, security issues, style problems,
    and maintainability concerns. Provide specific, actionable feedback.
    """

    review_scope: Annotated[
        Input(str),
        Field(description="Description of what to review (e.g., 'Review changes on branch X')"),
    ]
    review_focus: Annotated[
        Input(str | None),
        Field(default=None, description="Optional focus area (e.g., 'security', 'performance')"),
    ]
    workspace: Context(WorkspaceRef)  # Read-only access for git operations

    summary: Annotated[
        Output(str),
        Field(description="High-level summary of the review"),
    ]
    findings: Annotated[
        Output(list[dict]),  # Would be list[ReviewFinding] but keeping simple for JSON schema
        Field(description="List of specific findings with severity, category, and suggestions"),
    ]
    approval_recommendation: Annotated[
        Output(Literal["approve", "request_changes", "comment"]),
        Field(description="Overall recommendation for the changes"),
    ]


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    """Run the code review and print results."""
    print_header("Shepherd Framework - Code Review Scenario")

    if not require_gitpython("review_code.py"):
        return 1

    # Generate workspace from fixture
    print("\nGenerating workspace from fixture...")
    workspace_path = generate_scenario_workspace("rich-cli/code_review")
    atexit.register(lambda: cleanup_workspace(workspace_path))
    print(f"Workspace: {workspace_path}")

    # Configure provider
    shepherd.configure(
        provider=ClaudeProvider(
            name="default",
            model="claude-sonnet-4-20250514",
            default_permission_mode="acceptEdits",
            verbose=VerboseConfig(enabled=True),
        )
    )

    # Bind workspace (read-only for review)
    workspace = shepherd.bind(
        "workspace",
        WorkspaceRef.readonly(str(workspace_path)),
    )

    # Run the review
    print_section("Running Code Review")
    print("Branch to review: review/add-quiet-mode")

    result = ReviewCode(
        review_scope=(
            "Review the changes on the 'review/add-quiet-mode' branch compared to 'main'. "
            "Use 'git diff main...review/add-quiet-mode' to see the changes."
        ),
        review_focus=None,
    )

    summary = result.summary
    findings = result.findings or []
    recommendation = result.approval_recommendation
    recommendation_display = recommendation.upper() if recommendation is not None else "(none returned)"

    # Print results
    print_section("Review Results")
    print(f"\nSummary:\n{summary or '(no summary returned)'}")
    print(f"\nRecommendation: {recommendation_display}")
    print(f"\nFindings ({len(findings)} issues):\n")

    for i, finding in enumerate(findings, 1):
        severity = finding.get("severity", "unknown")
        category = finding.get("category", "unknown")
        file_path = finding.get("file_path", "unknown")
        line_number = finding.get("line_number")
        description = finding.get("description", "")
        suggestion = finding.get("suggestion")

        print(f"{i}. [{severity.upper()}] {category}")
        print(f"   File: {file_path}", end="")
        if line_number:
            print(f":{line_number}")
        else:
            print()
        print(f"   {description}")
        if suggestion:
            print(f"   Suggestion: {suggestion}")
        print()

    if not findings:
        print("(no structured findings returned)\n")

    # Show effect stream
    print_section("Effect Stream")
    print(f"Total effects: {len(shepherd.effects)}")

    tool_calls = list(shepherd.effects.query(ToolCallCompleted))
    print(f"Tool calls: {len(tool_calls)}")
    for tc in tool_calls[:10]:
        print(f"  - {tc.effect.tool_name}")
    if len(tool_calls) > 10:
        print(f"  ... ({len(tool_calls) - 10} more)")

    # Verify no modifications (read-only review)
    print_section("Workspace State")
    print(f"Patches: {len(workspace.pending_patches)} (expected: 0 for read-only review)")

    review_demonstrated = bool(findings) or (bool(summary) and bool(tool_calls))
    print_example_outcome(
        "demonstrated" if review_demonstrated else "not_demonstrated",
        (
            "The provider completed and returned review artifacts for this scenario."
            if review_demonstrated
            else "The provider completed, but this run did not return a meaningful review result."
        ),
        [
            ("Summary", bool(summary), "returned" if summary else "none returned"),
            ("Findings", bool(findings), f"{len(findings)} issues reported"),
            (
                "Recommendation",
                recommendation is not None,
                recommendation.upper() if recommendation is not None else "none returned",
            ),
            ("Tool calls", bool(tool_calls), f"{len(tool_calls)} recorded"),
        ],
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
