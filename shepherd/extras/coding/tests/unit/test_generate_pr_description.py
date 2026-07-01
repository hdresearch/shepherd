"""Tests for the function-form generate_pr_description task."""

from __future__ import annotations

from shepherd_coding.tasks.generate_pr_description import (
    PRDescriptionResult,
    generate_pr_description,
)
from shepherd_core.schema import SINGLE_OUTPUT_KEY
from shepherd_runtime.provider_boundary import ModelResponse

from shepherd import handle, workspace


def test_generate_pr_description_metadata_carries_guidance() -> None:
    assert generate_pr_description.metadata.guidance
    assert "pull request description" in generate_pr_description.metadata.guidance
    assert generate_pr_description.metadata.qualname.endswith("generate_pr_description")


async def test_generate_pr_description_returns_typed_result() -> None:
    def fake_model(request: object) -> ModelResponse:
        del request
        return ModelResponse(
            structured_output={
                SINGLE_OUTPUT_KEY: {
                    "pr_title": "feat: improve review summary",
                    "pr_body": "## Summary\n- Improves review summary generation.",
                }
            }
        )

    with workspace(model="offline-coding-test"), handle("model.call", fake_model):
        result = await generate_pr_description(
            diff_text="diff --git a/app.py b/app.py",
            commit_log="abc123 feat: improve summary",
            changed_files=["app.py"],
            verdict="ready",
            tool_summary="tests passed",
            unresolved_summary="none",
        )

    assert result == PRDescriptionResult(
        pr_title="feat: improve review summary",
        pr_body="## Summary\n- Improves review summary generation.",
    )
