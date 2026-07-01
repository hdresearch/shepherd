"""Unified finding model for code analysis pipelines.

CodeFinding replaces both ReviewFinding (PR review) and Issue (quality gate)
with a single model that captures the superset of both schemas. Conversion
functions allow incremental migration — existing tasks can produce their
original types and convert at pipeline boundaries.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ── Enums ───────────────────────────────────────────────────────────────────


class Severity(str, Enum):
    """Unified severity covering both review and tool-output levels."""

    BLOCKER = "blocker"
    ERROR = "error"
    WARNING = "warning"
    SUGGESTION = "suggestion"
    NIT = "nit"


class Source(str, Enum):
    """Origin of the finding."""

    PROGRAMMATIC = "programmatic"
    LLM = "llm"
    HUMAN = "human"


class Confidence(str, Enum):
    """Confidence level for LLM-generated findings."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ── CodeFinding ─────────────────────────────────────────────────────────────


class CodeFinding(BaseModel):
    """Unified representation of a code quality finding.

    Replaces both ``ReviewFinding`` (PR review pipeline) and ``Issue``
    (quality gate pipeline). Supports all field access patterns used by
    both pipelines:

    * ``category`` — extensible string (no closed enum).
    * ``severity`` — ``Severity`` enum with ``.value`` for display.
    * ``line_range`` — ``tuple[int, int]`` for quality gate consumers.
    * ``line_start`` / ``line_end`` — properties for formatter/review consumers.
    * ``identity_key()`` / ``overlaps()`` — dedup methods for quality gate.
    * ``title`` / ``body`` — display properties for the review formatter.
    """

    category: str = Field(description="Finding category (e.g. 'correctness', 'type_error')")
    severity: Severity = Field(default=Severity.WARNING)
    file_path: str = Field(default="")
    line_range: tuple[int, int] = Field(default=(0, 0))
    title: str = Field(default="")
    description: str = Field(default="")
    source: Source = Field(default=Source.PROGRAMMATIC)
    confidence: Confidence = Field(default=Confidence.MEDIUM)
    evidence: str = Field(default="")
    hypothesis: str = Field(default="")
    suggested_fix: str = Field(default="")

    # ── ReviewFinding-compatible properties ──

    @property
    def line_start(self) -> int:
        """First relevant line (from ``line_range[0]``)."""
        return self.line_range[0]

    @property
    def line_end(self) -> int | None:
        """Last relevant line, or ``None`` for single-line findings."""
        end = self.line_range[1]
        return end if end != self.line_range[0] else None

    @property
    def body(self) -> str:
        """Detailed explanation (alias for ``description``)."""
        return self.description

    # ── Issue-compatible methods ──

    def identity_key(self) -> tuple[str, str]:
        """Deduplication key: ``(file_path, category)``.

        Returns ``tuple[str, str]`` (not ``tuple[str, IssueCategory]``).
        This is compatible because ``IssueCategory`` inherits from ``str``.
        """
        return (self.file_path, self.category)

    def overlaps(self, other: CodeFinding, tolerance: int = 5) -> bool:
        """Check whether two findings overlap in the same file region."""
        if self.identity_key() != other.identity_key():
            return False
        if not self.file_path:
            return False
        a_start, a_end = self.line_range
        b_start, b_end = other.line_range
        return a_start <= b_end + tolerance and b_start <= a_end + tolerance


# ── Conversion functions ────────────────────────────────────────────────────

_REVIEW_SEVERITY_MAP = {
    "blocker": Severity.BLOCKER,
    "warning": Severity.WARNING,
    "suggestion": Severity.SUGGESTION,
    "nit": Severity.NIT,
}


def review_finding_to_code_finding(rf: object) -> CodeFinding:
    """Convert a ``ReviewFinding`` to ``CodeFinding``.

    Accepts ``object`` to avoid a circular import — the caller is
    responsible for passing a valid ``ReviewFinding`` instance.
    """
    severity = _REVIEW_SEVERITY_MAP.get(getattr(rf, "severity", "warning"), Severity.WARNING)
    line_start = getattr(rf, "line_start", 0)
    line_end = getattr(rf, "line_end", None) or line_start
    return CodeFinding(
        category=getattr(rf, "category", ""),
        severity=severity,
        file_path=getattr(rf, "file_path", ""),
        line_range=(line_start, line_end),
        title=getattr(rf, "title", ""),
        description=getattr(rf, "body", ""),
        source=Source.LLM,
        confidence=Confidence(getattr(rf, "confidence", "medium")),
    )


def issue_to_code_finding(issue: object) -> CodeFinding:
    """Convert a quality gate ``Issue`` to ``CodeFinding``.

    Accepts ``object`` to avoid a circular import — the caller is
    responsible for passing a valid ``Issue`` instance.
    """
    from shepherd_coding.models import IssueSeverity, IssueSource

    severity = {
        IssueSeverity.ERROR: Severity.ERROR,
        IssueSeverity.WARNING: Severity.WARNING,
    }.get(getattr(issue, "severity", None), Severity.WARNING)
    source = {
        IssueSource.PROGRAMMATIC: Source.PROGRAMMATIC,
        IssueSource.LLM: Source.LLM,
    }.get(getattr(issue, "source", None), Source.PROGRAMMATIC)
    desc = getattr(issue, "description", "")

    category = getattr(issue, "category", "")
    if hasattr(category, "value"):
        category = category.value

    return CodeFinding(
        category=category,
        severity=severity,
        file_path=getattr(issue, "file_path", ""),
        line_range=getattr(issue, "line_range", (0, 0)),
        title=desc[:80] if desc else "",
        description=desc,
        source=source,
        evidence=getattr(issue, "evidence", ""),
        hypothesis=getattr(issue, "hypothesis", ""),
        suggested_fix=getattr(issue, "suggested_fix_approach", ""),
    )


# ── FixRecord replacement ──────────────────────────────────────────────────


class UnifiedFixRecord(BaseModel):
    """Record of an automated fix, using ``CodeFinding``."""

    finding: CodeFinding
    verified: bool = False


# ── Formatting utility ──────────────────────────────────────────────────────


def format_findings_for_llm(findings: list[CodeFinding]) -> str:
    """Format a batch of findings as a numbered list for LLM consumption.

    Adapted from ``quality_gate.pipeline._format_issues_for_llm`` to
    operate on ``CodeFinding`` with string categories.
    """
    lines = []
    for idx, f in enumerate(findings, start=1):
        parts = [f"{idx}. [{f.category}] {f.description}"]
        if f.file_path:
            loc = f.file_path
            if f.line_range != (0, 0):
                loc += f":{f.line_range[0]}-{f.line_range[1]}"
            parts.append(f"   Location: {loc}")
        if f.evidence:
            parts.append(f"   Evidence: {f.evidence}")
        if f.hypothesis:
            parts.append(f"   Hypothesis: {f.hypothesis}")
        if f.suggested_fix:
            parts.append(f"   Suggested fix: {f.suggested_fix}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


# ── Category priority ──────────────────────────────────────────────────────

CATEGORY_PRIORITY: dict[str, int] = {
    "type_error": 0,
    "test_failure": 1,
    "correctness": 2,
    "consistency": 3,
    "doc_gap": 4,
    "coverage_gap": 5,
}


__all__ = [
    "CATEGORY_PRIORITY",
    "CodeFinding",
    "Confidence",
    "Severity",
    "Source",
    "UnifiedFixRecord",
    "format_findings_for_llm",
    "issue_to_code_finding",
    "review_finding_to_code_finding",
]
