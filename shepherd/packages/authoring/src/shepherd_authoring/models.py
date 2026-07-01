"""Shared models for the design-refinement pipeline."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class IssueStatus(str, Enum):
    """Triage status for a critique issue across iterations."""

    NEW = "new"
    UNCHANGED = "unchanged"
    PARTIALLY_RESOLVED = "partially_resolved"
    RESOLVED = "resolved"


class CritiqueIssue(BaseModel):
    """A single critique issue with triage status for cross-iteration tracking."""

    description: str = Field(description="What the problem is")
    status: IssueStatus = Field(
        default=IssueStatus.NEW,
        description="new = first time raised; unchanged/partially_resolved/resolved = triage of a prior issue",
    )


class CritiqueOutput(BaseModel):
    """Structured output from CritiqueDocuments, passed to RefineDocuments."""

    score: float = Field(description="Holistic quality score, 1-10")
    issues: list[CritiqueIssue] = Field(default_factory=list, description="Tracked issues with status")
    suggestions: list[str] = Field(default_factory=list, description="Non-blocking improvements")
    reasoning_context: str = Field(default="", description="Chain-of-thought for refiner")

    @property
    def issue_strings(self) -> list[str]:
        """Flat issue descriptions for backward-compatible consumers."""
        return [i.description for i in self.issues]

    @property
    def unresolved(self) -> list[CritiqueIssue]:
        """Issues that are not fully resolved."""
        return [i for i in self.issues if i.status != IssueStatus.RESOLVED]

    @property
    def blocking(self) -> list[CritiqueIssue]:
        """Issues that are new or unchanged — the refiner should prioritize these."""
        return [i for i in self.issues if i.status in (IssueStatus.NEW, IssueStatus.UNCHANGED)]

    def __str__(self) -> str:
        """JSON serialization for LLM prompt clarity."""
        return self.model_dump_json(indent=2)


__all__ = ["CritiqueIssue", "CritiqueOutput", "IssueStatus"]
