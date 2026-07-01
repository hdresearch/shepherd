"""Tests for Attribution-based effect attribution (Spike 1).

Validates that ExtractPhase and cleanup_contexts work correctly
when provider is None and attribution carries provider_id=None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest
from shepherd_core.effects import Effect
from shepherd_core.types import ExecutionResult, ProviderBinding, ReversibilityLevel
from shepherd_runtime._lifecycle import Attribution, ExtractPhase, PhaseContext, cleanup_contexts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubEffect(Effect):
    """Minimal concrete Effect for testing."""

    effect_type: str = "stub"


@dataclass(frozen=True)
class _StubContext:
    """Minimal context that yields one effect from extract_effects."""

    name: str = "stub"

    @property
    def context_id(self) -> str:
        return f"stub:{self.name}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    def configure(self, capabilities: Any = None) -> ProviderBinding:
        return ProviderBinding(context_ids=[self.context_id])

    def prepare(self) -> _StubContext:
        return self

    def cleanup(self, error: Exception | None = None) -> None:
        pass

    def extract_effects(self, sandbox: Any, result: Any) -> Iterator[Effect]:
        yield _StubEffect()

    def apply_effect(self, effect: Any) -> _StubContext:
        return self


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def programmatic_attribution() -> Attribution:
    return Attribution(task_name="TestTask", provider_id=None, source="programmatic")


@pytest.fixture
def programmatic_context(programmatic_attribution: Attribution) -> PhaseContext:
    """PhaseContext with no provider, programmatic attribution."""
    scope = MagicMock()
    scope.emit = MagicMock()
    scope.mark_binding_lifecycle = MagicMock()

    stub_ctx = _StubContext()
    sentinel = ExecutionResult(
        success=True,
        output_text="",
        tool_calls=(),
        tool_results=(),
        metadata={"task_name": "TestTask"},
    )

    return PhaseContext(
        scope=scope,
        provider=None,
        attribution=programmatic_attribution,
        task_name="TestTask",
        prepared_contexts={"workspace": stub_ctx},  # type: ignore[arg-type]
        result=sentinel,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProgrammaticAttribution:
    """Validate that phases use ctx.attribution instead of ctx.provider for attribution."""

    @pytest.mark.asyncio
    async def test_extract_phase_uses_attribution(self, programmatic_context: PhaseContext) -> None:
        """ExtractPhase should use attribution.provider_id (None) without AttributeError."""
        emitter = MagicMock()
        phase = ExtractPhase(emitter)

        result_ctx = await phase.execute(programmatic_context)

        # Should have extracted 1 effect from _StubContext
        assert len(result_ctx.extracted_effects) == 1

        # The effect should have provider_id=None (from attribution)
        effect = result_ctx.extracted_effects[0]
        assert effect.provider_id is None
        assert effect.task_name == "TestTask"

    @pytest.mark.asyncio
    async def test_cleanup_uses_attribution(self, programmatic_context: PhaseContext) -> None:
        """cleanup_contexts should use attribution.provider_id without AttributeError."""
        emitter = MagicMock()
        errors: list[tuple[str, Exception]] = []

        await cleanup_contexts(
            programmatic_context,
            error=None,
            errors=errors,
            emit_effects=True,
            emitter=emitter,
        )

        # Should complete without errors
        assert len(errors) == 0

        # ContextCleanedUp effect should have been emitted
        emitter.emit.assert_called_once()
        emitted_effect = emitter.emit.call_args[0][0]
        assert emitted_effect.provider_id is None
        assert emitted_effect.task_name == "TestTask"


class TestLLMAttribution:
    """Validate that LLM-path attribution still works correctly."""

    @pytest.mark.asyncio
    async def test_extract_phase_uses_llm_attribution(
        self,
        basic_context: PhaseContext,
    ) -> None:
        """ExtractPhase should use attribution.provider_id from LLM attribution."""
        # basic_context has attribution with provider_id="test-provider"
        # Add a prepared context with an effect-producing stub
        from dataclasses import replace

        stub_ctx = _StubContext()
        sentinel = ExecutionResult(
            success=True,
            output_text="test",
            metadata={"task_name": "test-task"},
        )
        ctx = replace(
            basic_context,
            prepared_contexts={"workspace": stub_ctx},
            result=sentinel,
        )

        emitter = MagicMock()
        phase = ExtractPhase(emitter)
        result_ctx = await phase.execute(ctx)

        assert len(result_ctx.extracted_effects) == 1
        effect = result_ctx.extracted_effects[0]
        # with_attribution preserves existing None, but for LLM the attribution
        # carries "test-provider" — however, with_attribution(provider_id=X) only
        # sets provider_id if X is not None. "test-provider" is not None, so it sets.
        assert effect.provider_id == "test-provider"
