"""Coding domain tasks — leaf work units for PR review and quality gate.

Mid-migration state: this package contains both function-form
tasks (the long-term shape per CONTRACTS A4 / DECISIONS D5) and
class-form tasks that remain coupled to the class-form workflow
pipelines in ``shepherd_coding/workflows/``. The remaining workflow-coupled
class-form wrappers migrate alongside their workflow pipelines in the
follow-on lane.

Function-form tasks:
- summarize / SummaryResult: holistic quality assessment
- validate_issue / ValidationResult: confirm or drop a flagged issue
- generate_fix / FixResult: produce a fix for a confirmed issue
- critique_fix / CritiqueResult: critique a proposed fix
- configure_pr_review: infer PR review config (returns PRReviewConfig)
- configure_quality_gate / QualityGateConfigResult: infer quality gate config
- generate_pr_description / PRDescriptionResult: generate PR title/body
- run_linter: run ruff check as a function-form programmatic leaf

Class-form tasks (deferred to Tranche 8+, currently consumed by
``workflows/pr_review/pipeline.py`` and
``workflows/quality_gate/pipeline.py``):
- FetchPR, CheckoutPR: programmatic PR data and worktree management
- Triage, TriagePR: PR/branch categorization and prioritization
- Review, ReviewPR: AI-powered code review
- RunLinter, RunFormatter, RunTypeChecker, RunTests: workflow compatibility tool runners
- AnalyzeCode: LLM-powered analysis by category
- ValidateIssues: aggregate issue validation
- GenerateFixes: aggregate fix generation
- GeneratePRDescription: workflow compatibility PR description generation
"""

from .analyze_code import AnalyzeCode
from .checkout_pr import CheckoutPR
from .configure_pr_review import configure_pr_review
from .configure_quality_gate import (
    QualityGateConfigResult,
    configure_quality_gate,
)
from .critique_fix import CritiqueResult, critique_fix
from .fetch_pr import FetchPR
from .generate_fix import FixResult, generate_fix
from .generate_fixes import GenerateFixes
from .generate_pr_description import (
    GeneratePRDescription,
    PRDescriptionResult,
    generate_pr_description,
)
from .review import Review
from .review_pr import ReviewPR
from .run_formatter import RunFormatter
from .run_linter import RunLinter, run_linter
from .run_tests import RunTests
from .run_type_checker import RunTypeChecker
from .summarize import SummaryResult, summarize
from .triage import Triage
from .triage_pr import TriagePR
from .validate_issue import ValidationResult, validate_issue
from .validate_issues import ValidateIssues

# Alias: design renames ValidateIssues -> ValidateFindings
ValidateFindings = ValidateIssues

__all__ = [
    "AnalyzeCode",
    "CheckoutPR",
    "CritiqueResult",
    "FetchPR",
    "FixResult",
    "GenerateFixes",
    "GeneratePRDescription",
    "PRDescriptionResult",
    "QualityGateConfigResult",
    "Review",
    "ReviewPR",
    "RunFormatter",
    "RunLinter",
    "RunTests",
    "RunTypeChecker",
    "SummaryResult",
    "Triage",
    "TriagePR",
    "ValidateFindings",
    "ValidateIssues",
    "ValidationResult",
    "configure_pr_review",
    "configure_quality_gate",
    "critique_fix",
    "generate_fix",
    "generate_pr_description",
    "run_linter",
    "summarize",
    "validate_issue",
]
