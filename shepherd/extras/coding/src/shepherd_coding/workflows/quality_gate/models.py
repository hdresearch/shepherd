"""Quality gate models — re-exported from shepherd_coding.models.

All model definitions now live in shepherd_coding.models to avoid
circular imports between tasks and workflow modules.
"""

from shepherd_coding.models import (
    FixRecord,
    Issue,
    IssueCategory,
    IssueSeverity,
    IssueSource,
    IssueVerdict,
    QualityGateVerdict,
    ToolRunResult,
    ValidationResult,
    ValidationVerdict,
    net_progress,
)

__all__ = [
    "FixRecord",
    "Issue",
    "IssueCategory",
    "IssueSeverity",
    "IssueSource",
    "IssueVerdict",
    "QualityGateVerdict",
    "ToolRunResult",
    "ValidationResult",
    "ValidationVerdict",
    "net_progress",
]
