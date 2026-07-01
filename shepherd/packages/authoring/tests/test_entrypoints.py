"""Tests for PlanDesignRefinement and RunDesignRefinement entrypoint tasks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from shepherd_authoring.workflows.design_refinement import PlanDesignRefinement, RunDesignRefinement
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


class TestPlanDesignRefinement:
    """Tests for PlanDesignRefinement programmatic task."""

    def test_has_task_meta(self):
        assert hasattr(PlanDesignRefinement, "_task_meta")

    def test_has_execute_method(self):
        assert hasattr(PlanDesignRefinement, "execute")
        assert callable(PlanDesignRefinement.execute)

    def test_has_expected_fields(self):
        fields = PlanDesignRefinement.model_fields
        assert "design_document_path" in fields
        assert "max_iterations" in fields
        assert "target_score" in fields
        assert "workspace" in fields
        assert "plan_path" in fields
        assert "principles_path" in fields
        assert "spike_plan_path" in fields
        assert "output_dir" in fields

    def test_defaults(self):
        fields = PlanDesignRefinement.model_fields
        assert fields["max_iterations"].default == 5
        assert fields["target_score"].default == 8.0

    def test_rejects_missing_design_doc(self, git_repo: Path):
        """PlanDesignRefinement fails if design document doesn't exist."""
        workspace = WorkspaceRef.from_path(str(git_repo))
        with pytest.raises(TaskExecutionError, match="Design document not found"), mock_steps() as scope:
            scope.bind("workspace", workspace)
            PlanDesignRefinement(
                design_document_path=str(git_repo / "MISSING.md"),
            )

    def test_plan_creates_output_dir_and_working_copies(self, git_repo: Path):
        """PlanDesignRefinement creates .refinement/{name}/ with document copies."""
        design_path = git_repo / "DESIGN-test.md"
        design_path.write_text("# Design: Test\n\n## Problem\nA problem.\n")

        out_dir = git_repo / ".refinement" / "test"
        out_dir.mkdir(parents=True, exist_ok=True)
        spikes_path = str(out_dir / "SPIKES.md")

        # Pre-create files that the LLM tasks would write (mock doesn't do I/O)
        (out_dir / "PRINCIPLES.md").write_text("1. Keep interfaces minimal\n")
        Path(spikes_path).write_text("# Spikes\n\n## Spike 1\nValidate.\n")

        provider = MockProvider(
            mock_responses=[
                {
                    "structured": {
                        "principles_path": str(out_dir / "PRINCIPLES.md"),
                        "principles": [
                            "Keep interfaces minimal",
                            "Prefer composition over inheritance",
                            "Every mutation must be reversible",
                        ],
                    },
                },
                {
                    "structured": {
                        "spike_plan_path": spikes_path,
                    },
                },
            ]
        )
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps(provider=provider) as scope:
            scope.bind("workspace", workspace)
            plan = PlanDesignRefinement(
                design_document_path=str(design_path),
                max_iterations=3,
                target_score=7.5,
            )

        # Output dir created under .refinement/
        assert plan.output_dir == str(out_dir)
        assert out_dir.is_dir()

        # Working copy of design doc in documents/
        assert (out_dir / "documents" / "design.md").exists()
        assert (out_dir / "documents" / "design.md").read_text() == design_path.read_text()

        # Plan YAML in output dir
        plan_data = yaml.safe_load(Path(plan.plan_path).read_text())
        assert plan_data["principles"] == [
            "Keep interfaces minimal",
            "Prefer composition over inheritance",
            "Every mutation must be reversible",
        ]
        assert plan_data["max_iterations"] == 3
        assert plan_data["target_score"] == 7.5

        # document_paths point to working copies
        assert plan_data["document_paths"]["design"] == str(out_dir / "documents" / "design.md")

        # Original design doc unchanged
        assert design_path.read_text() == "# Design: Test\n\n## Problem\nA problem.\n"


class TestRunDesignRefinement:
    """Tests for RunDesignRefinement programmatic task."""

    def test_has_task_meta(self):
        assert hasattr(RunDesignRefinement, "_task_meta")

    def test_has_execute_method(self):
        assert hasattr(RunDesignRefinement, "execute")
        assert callable(RunDesignRefinement.execute)

    def test_has_expected_fields(self):
        fields = RunDesignRefinement.model_fields
        assert "plan_path" in fields
        assert "workspace" in fields
        assert "final_score" in fields
        assert "converged" in fields
        assert "iterations_used" in fields
        assert "log_path" in fields
        assert "output_dir" in fields

    def test_rejects_missing_plan(self, git_repo: Path):
        """RunDesignRefinement fails if plan file doesn't exist."""
        workspace = WorkspaceRef.from_path(str(git_repo))
        with pytest.raises(TaskExecutionError, match="Plan file not found"), mock_steps() as scope:
            scope.bind("workspace", workspace)
            RunDesignRefinement(plan_path=str(git_repo / "MISSING.yaml"))

    def test_rejects_plan_without_principles(self, git_repo: Path):
        """RunDesignRefinement fails if plan has no principles."""
        plan_path = git_repo / "REFINEMENT-PLAN.yaml"
        plan_path.write_text(
            yaml.dump(
                {
                    "document_paths": {"design": str(git_repo / "DESIGN.md")},
                    "max_iterations": 2,
                    "target_score": 8.0,
                }
            )
        )
        workspace = WorkspaceRef.from_path(str(git_repo))
        with pytest.raises(TaskExecutionError, match="No principles found"), mock_steps() as scope:
            scope.bind("workspace", workspace)
            RunDesignRefinement(plan_path=str(plan_path))


class TestEndToEnd:
    """End-to-end test: PlanDesignRefinement -> RunDesignRefinement."""

    def test_plan_then_run_with_configured_provider(self, git_repo: Path):
        """Full pipeline: Plan creates output dir, Run operates on working copies."""
        design_path = git_repo / "DESIGN-test.md"
        design_path.write_text("# Design: Test\n\n## Problem\nA problem.\n")

        out_dir = git_repo / ".refinement" / "test"
        spikes_path = str(out_dir / "SPIKES.md")

        # Pre-create files that the LLM tasks would write (mock doesn't do I/O)
        out_dir.mkdir(parents=True, exist_ok=True)
        Path(spikes_path).write_text("# Spikes\n\n## Spike 1\nValidate.\n")
        (out_dir / "PRINCIPLES.md").write_text("1. Keep it simple\n2. Test everything\n")

        provider = MockProvider(
            mock_responses=[
                # 1. ExtractPrinciples
                {
                    "structured": {
                        "principles_path": str(out_dir / "PRINCIPLES.md"),
                        "principles": ["Keep it simple", "Test everything"],
                    },
                },
                # 2. DraftSpikePlan
                {
                    "structured": {
                        "spike_plan_path": spikes_path,
                    },
                },
                # 3. CritiqueDocuments iteration 1 — below threshold
                {
                    "structured": {
                        "score": 6.5,
                        "issues": [{"description": "Missing risk analysis", "status": "new"}],
                        "suggestions": ["Add examples"],
                        "reasoning_context": "Design lacks risk section",
                    },
                },
                # 4. RefineDocuments iteration 1
                {
                    "structured": {
                        "edited_paths": [str(out_dir / "documents" / "design.md")],
                        "change_summary": "Added risk section",
                    },
                },
                # 5. CritiqueDocuments iteration 2 — above threshold
                {
                    "structured": {
                        "score": 8.5,
                        "issues": [],
                        "suggestions": ["Minor formatting"],
                        "reasoning_context": "Quality improved significantly",
                    },
                },
            ]
        )
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps(provider=provider) as scope:
            scope.bind("workspace", workspace)

            # Phase 1: Plan
            plan = PlanDesignRefinement(
                design_document_path=str(design_path),
                max_iterations=3,
                target_score=8.0,
            )
            assert Path(plan.plan_path).exists()
            assert Path(plan.output_dir) == out_dir

            plan_data = yaml.safe_load(Path(plan.plan_path).read_text())
            assert plan_data["principles"] == ["Keep it simple", "Test everything"]

            # Phase 2: Run
            run = RunDesignRefinement(plan_path=plan.plan_path)

        # Loop should have converged on iteration 2 (score 8.5 >= 8.0)
        assert run.converged is True
        assert run.iterations_used == 2
        assert run.final_score == 8.5

        # Refinement log in output dir
        log_path = Path(run.log_path)
        assert log_path.exists()
        assert log_path.parent == out_dir
        log_content = log_path.read_text()
        assert "## Iteration 1" in log_content
        assert "## Iteration 2" in log_content

        # Version snapshots in output dir
        versions_dir = out_dir / ".versions"
        assert versions_dir.exists()
        assert (versions_dir / "design.v1.md").exists()

        # Original design doc untouched
        assert design_path.read_text() == "# Design: Test\n\n## Problem\nA problem.\n"
        log_content = log_path.read_text()
        assert "## Iteration 1" in log_content
        assert "## Iteration 2" in log_content

        # Version snapshots in output dir
        versions_dir = out_dir / ".versions"
        assert versions_dir.exists()
        assert (versions_dir / "design.v1.md").exists()

        # Original design doc untouched
        assert design_path.read_text() == "# Design: Test\n\n## Problem\nA problem.\n"

    def test_plan_then_run_budget_exhausted(self, git_repo: Path):
        """Pipeline runs to budget exhaustion when score never reaches target."""
        design_path = git_repo / "DESIGN-test.md"
        design_path.write_text("# Design: Test\n\n## Problem\nA problem.\n")

        out_dir = git_repo / ".refinement" / "test"
        spikes_path = str(out_dir / "SPIKES.md")

        # Pre-create files that the LLM tasks would write (mock doesn't do I/O)
        out_dir.mkdir(parents=True, exist_ok=True)
        Path(spikes_path).write_text("# Spikes\n")
        (out_dir / "PRINCIPLES.md").write_text("1. principle 1\n")

        provider = MockProvider(
            mock_responses=[
                # Plan phase
                {
                    "structured": {
                        "principles_path": str(out_dir / "PRINCIPLES.md"),
                        "principles": ["principle 1"],
                    },
                },
                {"structured": {"spike_plan_path": spikes_path}},
                # Run phase: 2 iterations, never converging
                {
                    "structured": {
                        "score": 4.0,
                        "issues": [{"description": "incomplete", "status": "new"}],
                        "suggestions": [],
                        "reasoning_context": "ctx1",
                    },
                },
                {
                    "structured": {
                        "edited_paths": [],
                        "change_summary": "improvements",
                    },
                },
                {
                    "structured": {
                        "score": 5.5,
                        "issues": [{"description": "still incomplete", "status": "new"}],
                        "suggestions": [],
                        "reasoning_context": "ctx2",
                    },
                },
                {
                    "structured": {
                        "edited_paths": [],
                        "change_summary": "more improvements",
                    },
                },
            ]
        )
        workspace = WorkspaceRef.from_path(str(git_repo))
        with mock_steps(provider=provider) as scope:
            scope.bind("workspace", workspace)
            plan = PlanDesignRefinement(
                design_document_path=str(design_path),
                max_iterations=2,
                target_score=8.0,
            )
            run = RunDesignRefinement(plan_path=plan.plan_path)

        assert run.converged is False
        assert run.iterations_used == 2
        assert run.final_score == 5.5

        log_content = Path(run.log_path).read_text()
        assert "Diagnostic" in log_content
        assert "4.0 -> 5.5" in log_content


__all__ = ["TestEndToEnd", "TestPlanDesignRefinement", "TestRunDesignRefinement"]
