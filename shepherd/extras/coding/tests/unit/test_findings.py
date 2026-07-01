"""Tests for the unified CodeFinding model and conversion functions.

Validates that CodeFinding satisfies all field access patterns used by
both the PR review pipeline (formatter.py) and the quality gate pipeline
(pipeline.py), and that conversion from ReviewFinding and Issue is lossless.
"""

from __future__ import annotations

import pytest
from shepherd_coding.findings import (
    CATEGORY_PRIORITY,
    CodeFinding,
    Confidence,
    Severity,
    Source,
    UnifiedFixRecord,
    format_findings_for_llm,
    issue_to_code_finding,
    review_finding_to_code_finding,
)
from shepherd_coding.models import ReviewFinding
from shepherd_coding.workflows.quality_gate.models import (
    Issue,
    IssueCategory,
    IssueSeverity,
    IssueSource,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_review_finding() -> ReviewFinding:
    return ReviewFinding(
        severity="blocker",
        category="security",
        file_path="src/auth.py",
        line_start=42,
        line_end=50,
        title="SQL injection in login",
        body="The query uses f-string interpolation.",
        confidence="high",
    )


@pytest.fixture
def sample_issue() -> Issue:
    return Issue(
        category=IssueCategory.TYPE_ERROR,
        description="Argument type mismatch on line 15",
        file_path="src/core.py",
        line_range=(15, 15),
        severity=IssueSeverity.ERROR,
        source=IssueSource.PROGRAMMATIC,
        evidence="error: Argument 1 to 'foo' has incompatible type 'str'",
        suggested_fix_approach="Cast to int before passing",
    )


# ── ReviewFinding → CodeFinding ────────────────────────────────────────────


class TestReviewFindingConversion:
    def test_basic_fields(self, sample_review_finding: ReviewFinding) -> None:
        cf = review_finding_to_code_finding(sample_review_finding)
        assert cf.category == "security"
        assert cf.severity == Severity.BLOCKER
        assert cf.file_path == "src/auth.py"
        assert cf.title == "SQL injection in login"
        assert cf.description == "The query uses f-string interpolation."
        assert cf.confidence == Confidence.HIGH
        assert cf.source == Source.LLM

    def test_line_range(self, sample_review_finding: ReviewFinding) -> None:
        cf = review_finding_to_code_finding(sample_review_finding)
        assert cf.line_range == (42, 50)
        assert cf.line_start == 42
        assert cf.line_end == 50

    def test_single_line(self) -> None:
        rf = ReviewFinding(
            severity="nit",
            category="maintainability",
            file_path="foo.py",
            line_start=10,
            line_end=None,
            title="Rename",
            body="Consider renaming.",
        )
        cf = review_finding_to_code_finding(rf)
        assert cf.line_range == (10, 10)
        assert cf.line_start == 10
        assert cf.line_end is None

    def test_all_severities(self) -> None:
        for sev in ["blocker", "warning", "suggestion", "nit"]:
            rf = ReviewFinding(
                severity=sev,
                category="correctness",
                file_path="x.py",
                line_start=1,
                title="t",
                body="b",
            )
            cf = review_finding_to_code_finding(rf)
            assert cf.severity.value == sev


# ── Issue → CodeFinding ────────────────────────────────────────────────────


class TestIssueConversion:
    def test_basic_fields(self, sample_issue: Issue) -> None:
        cf = issue_to_code_finding(sample_issue)
        assert cf.category == "type_error"
        assert cf.severity == Severity.ERROR
        assert cf.file_path == "src/core.py"
        assert cf.line_range == (15, 15)
        assert cf.evidence == "error: Argument 1 to 'foo' has incompatible type 'str'"
        assert cf.suggested_fix == "Cast to int before passing"

    def test_source_preserved(self, sample_issue: Issue) -> None:
        cf = issue_to_code_finding(sample_issue)
        assert cf.source == Source.PROGRAMMATIC

        llm_issue = sample_issue.model_copy(update={"source": IssueSource.LLM})
        cf2 = issue_to_code_finding(llm_issue)
        assert cf2.source == Source.LLM

    def test_all_categories(self) -> None:
        for cat in IssueCategory:
            issue = Issue(category=cat, description="test")
            cf = issue_to_code_finding(issue)
            assert cf.category == cat.value

    def test_identity_key(self, sample_issue: Issue) -> None:
        cf = issue_to_code_finding(sample_issue)
        assert cf.identity_key() == ("src/core.py", "type_error")
        # Compare with original (IssueCategory inherits from str)
        assert cf.identity_key()[1] == sample_issue.identity_key()[1].value

    def test_overlaps(self, sample_issue: Issue) -> None:
        cf1 = issue_to_code_finding(sample_issue)
        nearby = sample_issue.model_copy(update={"line_range": (18, 18)})
        cf2 = issue_to_code_finding(nearby)
        assert cf1.overlaps(cf2)
        assert cf2.overlaps(cf1)

        far = sample_issue.model_copy(update={"line_range": (100, 100)})
        cf3 = issue_to_code_finding(far)
        assert not cf1.overlaps(cf3)

    def test_model_copy(self, sample_issue: Issue) -> None:
        cf = issue_to_code_finding(sample_issue)
        updated = cf.model_copy(update={"suggested_fix": "Use int(x)"})
        assert updated.suggested_fix == "Use int(x)"
        assert updated.category == "type_error"


# ── CodeFinding field access patterns ───────────────────────────────────────


class TestFormatterPatterns:
    """Access patterns from workflows/formatter.py."""

    def test_severity_order_lookup(self) -> None:
        severity_order = {"blocker": 0, "error": 1, "warning": 2, "suggestion": 3, "nit": 4}
        cf = CodeFinding(category="security", severity=Severity.BLOCKER, description="x")
        assert severity_order.get(cf.severity, 99) == 0

    def test_confidence_sort_key(self) -> None:
        cf_high = CodeFinding(category="x", severity=Severity.WARNING, confidence=Confidence.HIGH, description="y")
        cf_low = CodeFinding(category="x", severity=Severity.WARNING, confidence=Confidence.LOW, description="y")
        assert (cf_high.confidence != "high") is False
        assert (cf_low.confidence != "high") is True

    def test_line_display(self) -> None:
        cf = CodeFinding(
            category="x",
            severity=Severity.WARNING,
            file_path="src/app.py",
            line_range=(10, 20),
            description="y",
        )
        loc = f"{cf.file_path}:{cf.line_start}"
        if cf.line_end and cf.line_end != cf.line_start:
            loc += f"-{cf.line_end}"
        assert loc == "src/app.py:10-20"

    def test_title_and_body(self) -> None:
        cf = CodeFinding(
            category="x",
            severity=Severity.WARNING,
            title="Short",
            description="Line one\nLine two",
        )
        assert cf.title == "Short"
        assert cf.body == "Line one\nLine two"
        assert cf.body.split("\n") == ["Line one", "Line two"]


class TestPipelinePatterns:
    """Access patterns from quality_gate/pipeline.py."""

    def test_category_priority_lookup(self) -> None:
        cf = CodeFinding(category="type_error", severity=Severity.ERROR, description="x")
        assert CATEGORY_PRIORITY.get(cf.category, 99) == 0

    def test_category_priority_sort(self) -> None:
        findings = [
            CodeFinding(category="coverage_gap", severity=Severity.WARNING, description="a"),
            CodeFinding(category="type_error", severity=Severity.ERROR, description="b"),
            CodeFinding(category="correctness", severity=Severity.ERROR, description="c"),
        ]
        sorted_f = sorted(findings, key=lambda f: CATEGORY_PRIORITY.get(f.category, 99))
        assert [f.category for f in sorted_f] == ["type_error", "correctness", "coverage_gap"]

    def test_severity_value_access(self) -> None:
        cf = CodeFinding(category="x", severity=Severity.ERROR, description="y")
        assert cf.severity.value == "error"

    def test_source_comparison(self) -> None:
        cf = CodeFinding(category="x", severity=Severity.ERROR, source=Source.LLM, description="y")
        assert cf.source == Source.LLM
        assert cf.source == "llm"

    def test_source_filtering(self) -> None:
        findings = [
            CodeFinding(category="x", severity=Severity.ERROR, source=Source.PROGRAMMATIC, description="a"),
            CodeFinding(category="x", severity=Severity.WARNING, source=Source.LLM, description="b"),
            CodeFinding(category="x", severity=Severity.WARNING, source=Source.LLM, description="c"),
        ]
        llm = [f for f in findings if f.source == Source.LLM]
        assert len(llm) == 2

    def test_line_range_tuple_access(self) -> None:
        cf = CodeFinding(category="x", severity=Severity.ERROR, line_range=(10, 20), description="y")
        assert cf.line_range[0] == 10
        assert cf.line_range[1] == 20
        assert cf.line_range != (0, 0)

    def test_novel_category_gets_default_priority(self) -> None:
        cf = CodeFinding(category="injection", severity=Severity.BLOCKER, description="x")
        assert CATEGORY_PRIORITY.get(cf.category, 99) == 99


# ── UnifiedFixRecord ────────────────────────────────────────────────────────


class TestUnifiedFixRecord:
    def test_field_access(self) -> None:
        cf = CodeFinding(
            category="type_error",
            severity=Severity.ERROR,
            file_path="src/core.py",
            description="Type mismatch",
        )
        fix = UnifiedFixRecord(finding=cf, verified=True)
        assert fix.finding.file_path == "src/core.py"
        assert fix.finding.description == "Type mismatch"
        assert fix.verified


# ── format_findings_for_llm ─────────────────────────────────────────────────


class TestFormatFindingsForLLM:
    def test_basic_format(self) -> None:
        findings = [
            CodeFinding(
                category="correctness",
                severity=Severity.ERROR,
                file_path="src/api.py",
                line_range=(10, 15),
                description="Null check missing",
                evidence="user can be None",
                hypothesis="Crash on None input",
                suggested_fix="Add None guard",
            ),
        ]
        text = format_findings_for_llm(findings)
        assert "[correctness]" in text
        assert "Null check missing" in text
        assert "src/api.py:10-15" in text
        assert "user can be None" in text
        assert "Crash on None input" in text
        assert "Add None guard" in text

    def test_no_location_for_zero_range(self) -> None:
        findings = [
            CodeFinding(category="doc_gap", severity=Severity.WARNING, description="Missing docs"),
        ]
        text = format_findings_for_llm(findings)
        assert "Location:" not in text

    def test_multiple_findings(self) -> None:
        findings = [
            CodeFinding(category="a", severity=Severity.ERROR, description="first"),
            CodeFinding(category="b", severity=Severity.WARNING, description="second"),
        ]
        text = format_findings_for_llm(findings)
        assert "1. [a]" in text
        assert "2. [b]" in text


# ── Serialization ───────────────────────────────────────────────────────────


class TestSerialization:
    def test_json_round_trip(self) -> None:
        cf = CodeFinding(
            category="type_error",
            severity=Severity.ERROR,
            file_path="x.py",
            line_range=(1, 5),
            title="Error",
            description="Detail",
            source=Source.PROGRAMMATIC,
            confidence=Confidence.HIGH,
        )
        data = cf.model_dump()
        restored = CodeFinding(**data)
        assert restored == cf

    def test_enum_serialization(self) -> None:
        cf = CodeFinding(category="x", severity=Severity.BLOCKER, description="y")
        data = cf.model_dump()
        assert data["severity"] == "blocker"
        assert data["source"] == "programmatic"
        assert data["confidence"] == "medium"
