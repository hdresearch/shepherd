"""Tests for the configure_pr_review task: guidance content and smoke import.

Tranche 7 migration note (DECISIONS D5): the class-form
``ConfigurePRReview`` was retired in favor of function-form
``configure_pr_review``. The class-form ``extract_task_metadata`` /
``generate_output_schema`` introspection tests are dropped because
function-form metadata machinery (``TaskMetadata``) is different;
schema fidelity is now exercised at the nucleus layer in
``shepherd/packages/runtime/tests/unit/nucleus/`` and at the integration
gate in ``shepherd/integration-tests/test_appendix_c_quickstart.py``.
"""

from shepherd.autoconfig import WORKSPACE_ANALYSIS_GUIDANCE
from shepherd_coding.tasks.configure_pr_review import (
    PR_REVIEW_GUIDANCE,
    configure_pr_review,
)


class TestSmokeImport:
    def test_callable_task_imports(self) -> None:
        # configure_pr_review is now a function-form CallableTask
        # (per CONTRACTS A4 / DECISIONS D5).
        assert configure_pr_review is not None
        assert callable(configure_pr_review)

    def test_metadata_carries_guidance(self) -> None:
        # @task(guidance=PR_REVIEW_GUIDANCE) flows through to the
        # TaskMetadata; consumers (Plan 04 prompt construction) can
        # read it back.
        metadata = configure_pr_review.metadata
        assert metadata.guidance is PR_REVIEW_GUIDANCE
        # No `name=` override given; consumers fall back to qualname.
        assert metadata.name is None
        assert metadata.qualname.endswith("configure_pr_review")


class TestGuidanceQuality:
    def test_shared_guidance_names_specific_files(self) -> None:
        expected_patterns = [
            "pyproject.toml",
            ".github/workflows",
            "CONTRIBUTING.md",
            ".gitignore",
            "ruff.toml",
        ]
        for pattern in expected_patterns:
            assert pattern in WORKSPACE_ANALYSIS_GUIDANCE, f"Shared guidance missing file pattern: {pattern}"

    def test_shared_guidance_has_numbered_exploration_order(self) -> None:
        assert "1." in WORKSPACE_ANALYSIS_GUIDANCE
        assert "2." in WORKSPACE_ANALYSIS_GUIDANCE

    def test_domain_guidance_lists_infrastructure_fields(self) -> None:
        for field in ("repo", "github_token", "clone_url"):
            assert field in PR_REVIEW_GUIDANCE, f"Domain guidance missing infrastructure field: {field}"
        assert "null" in PR_REVIEW_GUIDANCE.lower()
