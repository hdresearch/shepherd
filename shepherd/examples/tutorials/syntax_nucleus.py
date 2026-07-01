"""Minimal syntax nucleus example.

Run with:
    uv run python shepherd/examples/tutorials/syntax_nucleus.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from shepherd_core.schema import SINGLE_OUTPUT_KEY
from shepherd_runtime.provider_boundary import ModelRequest, ModelResponse

from shepherd import Run, deliver, handle, task, workspace


@dataclass(frozen=True)
class Triage:
    """Structured triage result."""

    category: str
    priority: str
    rationale: str


@dataclass(frozen=True)
class TutorialModel:
    """Small model identity used by the offline model.call handler."""

    name: str = "syntax-nucleus-tutorial"


@task
async def triage_change(diff: str) -> Triage:
    """Classify a code change."""
    return await deliver(
        Triage,
        goal="Classify this code change.",
        evidence=[diff],
        constraints=["Use category bugfix, feature, docs, or refactor."],
    )


async def main() -> None:
    """Run the example against a deterministic offline model.call handler."""

    async def fake_model(request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(
            structured_output={
                SINGLE_OUTPUT_KEY: {
                    "category": "feature",
                    "priority": "medium",
                    "rationale": "Adds a focused user-visible capability.",
                }
            }
        )

    with (
        workspace(model=TutorialModel()),
        handle("model.call", fake_model),
    ):
        result = await triage_change("diff --git a/app.py b/app.py")
        run: Run[Triage] = await triage_change.detailed("diff --git a/app.py b/app.py")

    assert result == Triage(
        category="feature",
        priority="medium",
        rationale="Adds a focused user-visible capability.",
    )
    assert run.unwrap() == result
    assert run.trace is not None
    assert run.trace.kernel
    assert run.trace.surface


if __name__ == "__main__":
    asyncio.run(main())
