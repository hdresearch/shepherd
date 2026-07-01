"""GitHub data models.

Pydantic models representing GitHub entities like pull requests,
issues, commits, and reviews. These are data contracts - they carry
no behavior and are used across utilities and tasks.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PRAuthor(BaseModel):
    """Author information for a PR, commit, or review."""

    login: str
    name: str | None = None


class PRLabel(BaseModel):
    """A label attached to a PR."""

    name: str
    color: str | None = None
    description: str | None = None


class PRFile(BaseModel):
    """A file changed in a PR."""

    path: str
    additions: int
    deletions: int
    patch: str | None = Field(default=None, description="Unified diff for this file. None for binary files.")
    status: str = Field(default="modified", description="added, modified, removed, renamed, changed, copied")
    previous_path: str | None = Field(default=None, description="For renames, the old path")


class PRCommit(BaseModel):
    """A commit in a PR."""

    oid: str = Field(description="Git SHA")
    message_headline: str = Field(alias="messageHeadline")
    authored_date: str = Field(alias="authoredDate")
    authors: list[PRAuthor]

    model_config = {"populate_by_name": True}


class PRReview(BaseModel):
    """A review on a PR."""

    author: PRAuthor
    state: str = Field(description="APPROVED, CHANGES_REQUESTED, COMMENTED, PENDING, DISMISSED")
    body: str


class PRDetails(BaseModel):
    """Complete details of a GitHub pull request."""

    number: int
    title: str
    body: str
    author: PRAuthor
    state: str = Field(description="OPEN, CLOSED, MERGED")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    url: str
    base_ref_name: str = Field(alias="baseRefName")
    head_ref_name: str = Field(alias="headRefName")
    additions: int
    deletions: int
    changed_files: int = Field(alias="changedFiles")
    labels: list[PRLabel]
    files: list[PRFile]
    commits: list[PRCommit]
    reviews: list[PRReview]
    review_decision: str | None = Field(
        alias="reviewDecision",
        default=None,
        description="APPROVED, CHANGES_REQUESTED, or None",
    )
    head_sha: str = Field(default="", description="SHA of the PR's head commit")
    clone_url: str = Field(default="", description="HTTPS clone URL for the repository")

    model_config = {"populate_by_name": True}


class ReviewFinding(BaseModel):
    """A single finding from a code review.

    Structured representation of a review comment with severity,
    category, and location information. Used as the output type
    for ReviewPR and consumed by the terminal formatter.
    """

    severity: Literal["blocker", "warning", "suggestion", "nit"]
    category: Literal["correctness", "security", "performance", "maintainability", "testing"]
    file_path: str = Field(description="Path relative to repo root")
    line_start: int = Field(description="First relevant line")
    line_end: int | None = Field(default=None, description="Last relevant line (None for single-line)")
    title: str = Field(description="One-line summary")
    body: str = Field(description="Detailed explanation and suggested fix")
    confidence: Literal["high", "medium", "low"] = "medium"


# =========================================================================
# Quality gate models
# =========================================================================


class IssueCategory(str, Enum):
    """Category of a quality issue."""

    TYPE_ERROR = "type_error"
    TEST_FAILURE = "test_failure"
    DOC_GAP = "doc_gap"
    CORRECTNESS = "correctness"
    CONSISTENCY = "consistency"
    COVERAGE_GAP = "coverage_gap"


class IssueSeverity(str, Enum):
    """Severity of a quality issue."""

    ERROR = "error"
    WARNING = "warning"


class IssueSource(str, Enum):
    """Source that identified the issue."""

    PROGRAMMATIC = "programmatic"
    LLM = "llm"


class Issue(BaseModel):
    """A quality issue identified by a diagnostic analyzer."""

    category: IssueCategory
    description: str
    hypothesis: str = ""
    file_path: str = ""
    line_range: tuple[int, int] = (0, 0)
    severity: IssueSeverity = IssueSeverity.ERROR
    evidence: str = ""
    source: IssueSource = IssueSource.PROGRAMMATIC
    suggested_fix_approach: str = ""

    def identity_key(self) -> tuple[str, IssueCategory]:
        return (self.file_path, self.category)

    def overlaps(self, other: Issue, tolerance: int = 5) -> bool:
        if self.identity_key() != other.identity_key():
            return False
        if not self.file_path:
            return False
        a_start, a_end = self.line_range
        b_start, b_end = other.line_range
        return a_start <= b_end + tolerance and b_start <= a_end + tolerance


class ValidationVerdict(str, Enum):
    """Verdict from validating an LLM-sourced issue."""

    CONFIRMED = "confirmed"
    DROPPED = "dropped"
    INCONCLUSIVE = "inconclusive"


class IssueVerdict(BaseModel):
    """Verdict for a single issue within a batch validation."""

    issue_number: int = Field(description="1-based index of the issue in the batch")
    verdict: ValidationVerdict = Field(description="confirmed, dropped, or inconclusive")
    explanation: str = ""
    suggested_fix_approach: str = ""


class ValidationResult(BaseModel):
    """Result of validating an LLM-sourced issue."""

    verdict: ValidationVerdict
    explanation: str = ""
    suggested_fix_approach: str = ""


class FixRecord(BaseModel):
    """Record of an automated fix."""

    issue: Issue | None = Field(default=None, deprecated="Use finding instead")
    finding: CodeFinding | None = None
    verified: bool = False


class ToolRunResult(BaseModel):
    """Result from a tool runner task."""

    tool: str
    passed: bool
    issues: list[Issue] = Field(default_factory=list, deprecated="Use findings instead")
    findings: list[CodeFinding] = Field(default_factory=list)
    raw_output: str = ""
    skipped: bool = False
    skip_reason: str = ""


# Resolve CodeFinding forward reference
from shepherd_coding.findings import CodeFinding  # noqa: TC001

FixRecord.model_rebuild()
ToolRunResult.model_rebuild()


QualityGateVerdict = Literal["ready", "needs_human_review"]


def net_progress(before_count: int, after_count: int) -> int:
    """Net issue change. Negative means progress."""
    return after_count - before_count


__all__ = [
    "FixRecord",
    "Issue",
    "IssueCategory",
    "IssueSeverity",
    "IssueSource",
    "IssueVerdict",
    "PRAuthor",
    "PRCommit",
    "PRDetails",
    "PRFile",
    "PRLabel",
    "PRReview",
    "QualityGateVerdict",
    "ReviewFinding",
    "ToolRunResult",
    "ValidationResult",
    "ValidationVerdict",
    "net_progress",
]
