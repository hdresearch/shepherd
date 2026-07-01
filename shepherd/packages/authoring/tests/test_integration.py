"""Integration tests with a real LLM provider.

These tests call the Claude API to validate that LLM tasks:
- Read files from disk via workspace tools
- Produce structured Pydantic-parsed outputs
- Write artifacts to the filesystem

Run with: pytest tests/test_integration.py -m integration
Skip with: pytest tests/ -m "not integration"

Requires:
- ANTHROPIC_API_KEY in environment
- Claude Agent SDK subprocess transport functional
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from shepherd_runtime.context.sandbox import GITPYTHON_AVAILABLE

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


def _can_run_claude_sdk() -> bool:
    """Check if the Claude Agent SDK subprocess transport is functional."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import asyncio

        from claude_agent_sdk import ClaudeAgentOptions, query

        async def _probe():
            options = ClaudeAgentOptions(
                model="claude-sonnet-4-20250514",
                permission_mode="bypassPermissions",
                max_turns=1,
            )
            async for _msg in query(prompt="reply ok", options=options):
                return True
            return True

        asyncio.run(_probe())
        return True
    except Exception:
        return False


# Defer the SDK probe to first access so test collection doesn't make API calls.
_sdk_functional: bool | None = None


def _check_sdk() -> bool:
    global _sdk_functional
    if _sdk_functional is None:
        _sdk_functional = _can_run_claude_sdk()
    return _sdk_functional


integration = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set (full SDK probe runs at first test)",
)


SAMPLE_DESIGN_DOC = """\
# Design: Event Sourcing for User Notifications

## Problem
Users miss important notifications because the current fire-and-forget
delivery model has no retry mechanism and no audit trail.

## Approach
Replace the notification dispatcher with an event-sourced architecture:
1. Every notification intent becomes an immutable event in an append-only log.
2. A projection reads the log and drives delivery (email, push, in-app).
3. Failed deliveries are retried with exponential backoff.
4. A read model provides notification history per user.

## Constraints
- Must not increase p99 latency of the publish path beyond 50ms.
- Events must be durable (survive single-node failure).
- Schema evolution must be backwards-compatible.

## Risks
- Event log storage costs could grow unbounded without compaction.
- Projection lag could cause users to see stale notification state.
"""


@integration
class TestExtractPrinciplesLive:
    """Validate ExtractPrinciples with a real LLM."""

    @pytest.mark.asyncio
    async def test_extracts_principles_from_design_doc(self, git_repo: Path):
        """LLM reads a design doc and produces a structured list of principles."""
        from shepherd_authoring.tasks import ExtractPrinciples
        from shepherd_contexts import WorkspaceRef
        from shepherd_providers import ClaudeProvider
        from shepherd_runtime.scope import Scope

        design_path = git_repo / "DESIGN-event-sourcing.md"
        design_path.write_text(SAMPLE_DESIGN_DOC)

        provider = ClaudeProvider(
            name="test",
            model="claude-sonnet-4-20250514",
            default_permission_mode="auto",
        )
        workspace = WorkspaceRef.from_path(str(git_repo))

        with Scope() as scope:
            scope.register_provider("default", provider, default=True)
            scope.bind("workspace", workspace)

            result = await ExtractPrinciples.arun(
                design_document_path=str(design_path),
            )

        # The LLM should produce a non-empty list of principles
        assert result.principles is not None, "principles should not be None"
        assert len(result.principles) >= 2, (
            f"Expected at least 2 principles, got {len(result.principles)}: {result.principles}"
        )

        # Each principle should be a non-trivial string
        for i, p in enumerate(result.principles):
            assert isinstance(p, str), f"Principle {i} should be str, got {type(p)}"
            assert len(p) > 10, f"Principle {i} is too short: {p!r}"

        # The principles should relate to the design content
        all_text = " ".join(result.principles).lower()
        # At least one principle should mention a concept from the design
        design_concepts = ["event", "notification", "latency", "durable", "retry", "immutable", "append"]
        matches = [c for c in design_concepts if c in all_text]
        assert len(matches) >= 1, (
            f"Principles don't seem related to the design. "
            f"Concepts checked: {design_concepts}. Principles: {result.principles}"
        )


@integration
class TestCritiqueDocumentsLive:
    """Validate CritiqueDocuments with a real LLM."""

    @pytest.mark.asyncio
    async def test_critiques_design_doc(self, git_repo: Path):
        """LLM reads documents and produces a structured critique with a score."""
        from shepherd_authoring.tasks import CritiqueDocuments
        from shepherd_contexts import WorkspaceRef
        from shepherd_providers import ClaudeProvider
        from shepherd_runtime.scope import Scope

        design_path = git_repo / "DESIGN-event-sourcing.md"
        design_path.write_text(SAMPLE_DESIGN_DOC)

        provider = ClaudeProvider(
            name="test",
            model="claude-sonnet-4-20250514",
            default_permission_mode="auto",
        )
        workspace = WorkspaceRef.from_path(str(git_repo))

        with Scope() as scope:
            scope.register_provider("default", provider, default=True)
            scope.bind("workspace", workspace)

            result = await CritiqueDocuments.arun(
                document_paths={"design": str(design_path)},
                principles=[
                    "Every mutation must be durable",
                    "Latency budgets are hard constraints",
                    "Schema evolution must be backwards-compatible",
                ],
            )

        # Score should be a meaningful number in range
        assert result.score is not None, "score should not be None"
        assert 1.0 <= result.score <= 10.0, f"Score {result.score} out of range [1, 10]"

        # reasoning_context should be non-empty (chain-of-thought for refiner)
        assert result.reasoning_context, "reasoning_context should not be empty"
        assert len(result.reasoning_context) > 20, f"reasoning_context too short: {result.reasoning_context!r}"


__all__ = ["TestCritiqueDocumentsLive", "TestExtractPrinciplesLive"]
