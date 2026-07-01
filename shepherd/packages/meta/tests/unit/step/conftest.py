"""Shared fixtures and helpers for step tests."""

from enum import Enum

from pydantic import BaseModel


class Severity(str, Enum):
    """Test enum for step return types."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnalysisResult(BaseModel):
    """Test Pydantic model for step return types."""

    summary: str
    score: int
    tags: list[str] = []
