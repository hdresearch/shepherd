"""Regression guard: infrastructure field descriptions must discourage LLM population."""

from shepherd_coding.workflows.pr_review.config import PRReviewConfig


class TestInfrastructureFieldDescriptions:
    def test_passive_language(self) -> None:
        """Infrastructure fields must contain 'populated at runtime' or 'leave null'."""
        infra_fields = ["repo", "github_token", "clone_url"]
        for name in infra_fields:
            field = PRReviewConfig.model_fields[name]
            desc = (field.description or "").lower()
            assert "populated at runtime" in desc or "leave null" in desc, (
                f"Field '{name}' description lacks passive language: {field.description!r}"
            )
