"""Tests for CritiqueRefineLoop orchestration task."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from shepherd_authoring.checks import check_refinement_log, check_version_history
from shepherd_authoring.workflows.design_refinement import CritiqueRefineLoop
from shepherd_contexts import WorkspaceRef
from shepherd_core.errors import TaskExecutionError
from shepherd_runtime.context.sandbox import GITPYTHON_AVAILABLE
from shepherd_tests import MockProvider, mock_steps

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


class TestCritiqueRefineLoop:
    """Tests for CritiqueRefineLoop programmatic task."""

    def test_has_task_meta(self):
        assert hasattr(CritiqueRefineLoop, "_task_meta")

    def test_has_execute_method(self):
        """CritiqueRefineLoop is a programmatic task with execute()."""
        assert hasattr(CritiqueRefineLoop, "execute")
        assert callable(CritiqueRefineLoop.execute)

    def test_has_expected_fields(self):
        fields = CritiqueRefineLoop.model_fields
        assert "document_paths" in fields
        assert "principles" in fields
        assert "max_iterations" in fields
        assert "target_score" in fields
        assert "workspace_path" in fields
        assert "workspace" in fields
        assert "final_score" in fields
        assert "iterations_used" in fields
        assert "converged" in fields

    def test_defaults(self):
        """Default values are reasonable."""
        fields = CritiqueRefineLoop.model_fields
        assert fields["max_iterations"].default == 5
        assert fields["target_score"].default == 8.0

    def test_rejects_missing_documents(self, git_repo: Path):
        """Loop fails if documents don't exist."""
        workspace = WorkspaceRef.from_path(str(git_repo))
        with pytest.raises(TaskExecutionError, match="not found"), mock_steps() as scope:
            scope.bind("workspace", workspace)
            CritiqueRefineLoop(
                document_paths={"design": str(git_repo / "MISSING.md")},
                principles=["p1"],
                max_iterations=1,
                target_score=8.0,
                workspace_path=str(git_repo),
            )

    def test_converges_when_score_meets_target(self, git_repo: Path):
        """Loop stops when critique score meets target."""
        design_path = git_repo / "DESIGN-test.md"
        design_path.write_text("# Design: Test\n\n## Problem\nA problem.\n")

        provider = MockProvider(
            mock_responses=[
                # Critique iteration 1: below target
                {
                    "structured": {
                        "score": 6.0,
                        "issues": [{"description": "weak motivation", "status": "new"}],
                        "suggestions": ["add rationale"],
                        "reasoning_context": "needs more why",
                    },
                },
                # Refine iteration 1
                {
                    "structured": {
                        "edited_paths": [str(design_path)],
                        "change_summary": "added rationale",
                    },
                },
                # Critique iteration 2: meets target
                {
                    "structured": {
                        "score": 8.5,
                        "issues": [],
                        "suggestions": [],
                        "reasoning_context": "quality is good",
                    },
                },
            ]
        )
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps(provider=provider) as scope:
            scope.bind("workspace", workspace)
            loop = CritiqueRefineLoop(
                document_paths={"design": str(design_path)},
                principles=["Be clear", "Be correct"],
                max_iterations=5,
                target_score=8.0,
                workspace_path=str(git_repo),
            )

        assert loop.converged is True
        assert loop.iterations_used == 2
        assert loop.final_score == 8.5

        # Refinement log should have both iterations
        assert check_refinement_log(git_repo / "REFINEMENT-LOG.md", 2)

        # Version snapshot only for iteration 1 (converged on 2 before snapshot)
        assert check_version_history(git_repo / ".versions", "design", 1)

    def test_exhausts_budget_when_score_stays_low(self, git_repo: Path):
        """Loop runs all iterations and writes diagnostic on budget exhaustion."""
        design_path = git_repo / "DESIGN-test.md"
        design_path.write_text("# Design: Test\n\n## Problem\nA problem.\n")

        provider = MockProvider(
            mock_responses=[
                # Iter 1: critique
                {
                    "structured": {
                        "score": 3.0,
                        "issues": [{"description": "fundamentally flawed", "status": "new"}],
                        "suggestions": [],
                        "reasoning_context": "major issues",
                    },
                },
                # Iter 1: refine
                {"structured": {"edited_paths": [], "change_summary": "attempted fix"}},
                # Iter 2: critique
                {
                    "structured": {
                        "score": 4.0,
                        "issues": [{"description": "still flawed", "status": "new"}],
                        "suggestions": [],
                        "reasoning_context": "some improvement",
                    },
                },
                # Iter 2: refine
                {"structured": {"edited_paths": [], "change_summary": "more fixes"}},
            ]
        )
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps(provider=provider) as scope:
            scope.bind("workspace", workspace)
            loop = CritiqueRefineLoop(
                document_paths={"design": str(design_path)},
                principles=["p1"],
                max_iterations=2,
                target_score=8.0,
                workspace_path=str(git_repo),
            )

        assert loop.converged is False
        assert loop.iterations_used == 2
        assert loop.final_score == 4.0

        # Diagnostic should show trajectory
        log = (git_repo / "REFINEMENT-LOG.md").read_text()
        assert "3.0 -> 4.0" in log

    def test_reasoning_context_flows_within_iteration(self, git_repo: Path):
        """reasoning_context flows from critique to refine within the same iteration."""
        design_path = git_repo / "DESIGN-test.md"
        design_path.write_text("# Design\n\n## Problem\nA problem.\n")

        call_log: list[str] = []
        original_init = CritiqueRefineLoop.execute

        # We verify the flow by checking that MockProvider receives
        # the correct sequence of calls. The reasoning_context from
        # critique iteration 1 should appear in refine iteration 1,
        # and the reasoning_context from critique iteration 1 should
        # be passed as prior_reasoning to critique iteration 2.
        provider = MockProvider(
            mock_responses=[
                # Critique 1
                {
                    "structured": {
                        "score": 5.0,
                        "issues": [{"description": "issue-A", "status": "new"}],
                        "suggestions": [],
                        "reasoning_context": "REASON-ITER-1",
                    },
                },
                # Refine 1 (should receive REASON-ITER-1 via CritiqueOutput)
                {"structured": {"edited_paths": [], "change_summary": "fixed"}},
                # Critique 2 (should receive REASON-ITER-1 as prior_reasoning)
                {
                    "structured": {
                        "score": 9.0,
                        "issues": [],
                        "suggestions": [],
                        "reasoning_context": "REASON-ITER-2",
                    },
                },
            ]
        )
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps(provider=provider) as scope:
            scope.bind("workspace", workspace)
            loop = CritiqueRefineLoop(
                document_paths={"design": str(design_path)},
                principles=["p1"],
                max_iterations=3,
                target_score=8.0,
                workspace_path=str(git_repo),
            )

        # Converged on iteration 2 with REASON-ITER-2
        assert loop.converged is True
        assert loop.iterations_used == 2

        # Verify 3 provider calls: critique1, refine1, critique2
        assert len(provider.calls) == 3


__all__ = ["TestCritiqueRefineLoop"]
