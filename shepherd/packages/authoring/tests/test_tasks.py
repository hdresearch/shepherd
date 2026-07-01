"""Tests for L1 tasks: ExtractPrinciples, DraftSpikePlan, CritiqueDocuments, RefineDocuments."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from shepherd_authoring.checks import (
    check_document_structure,
    check_file_exists,
    check_refinement_log,
    check_version_history,
)
from shepherd_authoring.models import CritiqueOutput
from shepherd_authoring.tasks import (
    CritiqueDocuments,
    DraftSpikePlan,
    ExtractPrinciples,
    RefineDocuments,
)
from shepherd_contexts import WorkspaceRef
from shepherd_runtime.context.sandbox import GITPYTHON_AVAILABLE
from shepherd_tests import mock_steps

pytestmark = pytest.mark.skipif(not GITPYTHON_AVAILABLE, reason="GitPython not installed")


@pytest.fixture
def git_repo(tmp_path):
    """Initialize a minimal git repo in tmp_path and return the path."""
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True, capture_output=True)
    (tmp_path / ".gitkeep").write_text("")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    return tmp_path


if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# Check predicate tests
# =============================================================================


class TestCheckPredicates:
    """Unit tests for filesystem check predicates."""

    def test_check_file_exists_true(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text("content")
        assert check_file_exists(f) is True

    def test_check_file_exists_false_missing(self, tmp_path: Path):
        assert check_file_exists(tmp_path / "nope.md") is False

    def test_check_file_exists_false_empty(self, tmp_path: Path):
        f = tmp_path / "empty.md"
        f.write_text("")
        assert check_file_exists(f) is False

    def test_check_document_structure_all_present(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\n## Problem\ntext\n\n## Solution\ntext\n")
        assert check_document_structure(f, ["Problem", "Solution"]) is True

    def test_check_document_structure_missing_section(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\n## Problem\ntext\n")
        assert check_document_structure(f, ["Problem", "Solution"]) is False

    def test_check_document_structure_missing_file(self, tmp_path: Path):
        assert check_document_structure(tmp_path / "nope.md", ["Problem"]) is False

    def test_check_refinement_log(self, tmp_path: Path):
        log = tmp_path / "REFINEMENT-LOG.md"
        log.write_text("# Refinement Log\n\n## Iteration 1\n**Score**: 6.0\n\n## Iteration 2\n**Score**: 7.5\n")
        assert check_refinement_log(log, 2) is True
        assert check_refinement_log(log, 3) is False

    def test_check_refinement_log_missing(self, tmp_path: Path):
        assert check_refinement_log(tmp_path / "nope.md", 1) is False

    def test_check_version_history(self, tmp_path: Path):
        versions = tmp_path / ".versions"
        versions.mkdir()
        (versions / "design.v1.md").write_text("v1")
        (versions / "design.v2.md").write_text("v2")
        assert check_version_history(versions, "design", 2) is True
        assert check_version_history(versions, "design", 3) is False

    def test_check_version_history_missing_dir(self, tmp_path: Path):
        assert check_version_history(tmp_path / "nope", "design", 1) is False


# =============================================================================
# CritiqueOutput model tests
# =============================================================================


class TestCritiqueOutput:
    """Unit tests for CritiqueOutput model."""

    def test_defaults(self):
        c = CritiqueOutput(score=7.5)
        assert c.score == 7.5
        assert c.issues == []
        assert c.suggestions == []
        assert c.reasoning_context == ""

    def test_full_construction(self):
        from shepherd_authoring.models import CritiqueIssue

        c = CritiqueOutput(
            score=6.0,
            issues=[CritiqueIssue(description="missing risks")],
            suggestions=["add examples"],
            reasoning_context="The design lacks risk analysis.",
        )
        assert c.score == 6.0
        assert len(c.issues) == 1
        assert c.issues[0].description == "missing risks"
        assert len(c.suggestions) == 1
        assert c.issue_strings == ["missing risks"]


# =============================================================================
# L1 task structure tests
# =============================================================================


class TestExtractPrinciples:
    """Tests for ExtractPrinciples task."""

    def test_has_task_meta(self):
        assert hasattr(ExtractPrinciples, "_task_meta")

    def test_has_expected_fields(self):
        fields = ExtractPrinciples.model_fields
        assert "design_document_path" in fields
        assert "workspace" in fields
        assert "principles_path" in fields
        assert "principles" in fields

    def test_is_llm_task(self):
        """ExtractPrinciples has no execute() — it's an LLM task."""
        assert not hasattr(ExtractPrinciples, "execute") or not callable(getattr(ExtractPrinciples, "execute", None))

    def test_instantiation_under_mock(self, git_repo: Path):
        """Task can be instantiated with mock_steps."""
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps() as scope:
            scope.bind("workspace", workspace)
            t = ExtractPrinciples(design_document_path="/tmp/test.md")
            # Mock provider returns default values
            assert isinstance(t.principles_path, (str, type(None)))


class TestDraftSpikePlan:
    """Tests for DraftSpikePlan task."""

    def test_has_task_meta(self):
        assert hasattr(DraftSpikePlan, "_task_meta")

    def test_has_expected_fields(self):
        fields = DraftSpikePlan.model_fields
        assert "design_document_path" in fields
        assert "principles" in fields
        assert "workspace" in fields
        assert "spike_plan_path" in fields

    def test_instantiation_under_mock(self, git_repo: Path):
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps() as scope:
            scope.bind("workspace", workspace)
            t = DraftSpikePlan(
                design_document_path="/tmp/test.md",
                principles=["principle 1"],
            )
            assert isinstance(t.spike_plan_path, (str, type(None)))


class TestCritiqueDocuments:
    """Tests for CritiqueDocuments task."""

    def test_has_task_meta(self):
        assert hasattr(CritiqueDocuments, "_task_meta")

    def test_has_expected_fields(self):
        fields = CritiqueDocuments.model_fields
        assert "document_paths" in fields
        assert "principles" in fields
        assert "prior_reasoning" in fields
        assert "workspace" in fields
        assert "score" in fields
        assert "issues" in fields
        assert "suggestions" in fields
        assert "reasoning_context" in fields

    def test_instantiation_under_mock(self, git_repo: Path):
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps() as scope:
            scope.bind("workspace", workspace)
            t = CritiqueDocuments(
                document_paths={"design": "/tmp/design.md"},
                principles=["principle 1"],
            )
            assert isinstance(t.score, (float, int, type(None)))

    def test_prior_reasoning_optional(self, git_repo: Path):
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps() as scope:
            scope.bind("workspace", workspace)
            t = CritiqueDocuments(
                document_paths={"design": "/tmp/design.md"},
                principles=["principle 1"],
                prior_reasoning="some prior context",
            )
            assert isinstance(t.score, (float, int, type(None)))


class TestRefineDocuments:
    """Tests for RefineDocuments task."""

    def test_has_task_meta(self):
        assert hasattr(RefineDocuments, "_task_meta")

    def test_has_expected_fields(self):
        fields = RefineDocuments.model_fields
        assert "document_paths" in fields
        assert "critique" in fields
        assert "principles" in fields
        assert "workspace" in fields
        assert "edited_paths" in fields
        assert "change_summary" in fields

    def test_instantiation_under_mock(self, git_repo: Path):
        from shepherd_authoring.models import CritiqueIssue

        workspace = WorkspaceRef.from_path(str(git_repo))
        critique = CritiqueOutput(
            score=5.0,
            issues=[CritiqueIssue(description="missing section")],
            suggestions=["add examples"],
            reasoning_context="needs work",
        )
        with mock_steps() as scope:
            scope.bind("workspace", workspace)
            t = RefineDocuments(
                document_paths={"design": "/tmp/design.md"},
                critique=critique,
                principles=["principle 1"],
            )
            assert isinstance(t.change_summary, (str, type(None)))


__all__ = [
    "TestCheckPredicates",
    "TestCritiqueDocuments",
    "TestCritiqueOutput",
    "TestDraftSpikePlan",
    "TestExtractPrinciples",
    "TestRefineDocuments",
]
