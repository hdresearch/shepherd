"""Tests for gating combinators: gate, budget, timeout."""

import asyncio

import pytest
from shepherd_core.effects import Effect, FileCreate
from shepherd_runtime.combinators.gating import budget, gate, timeout
from shepherd_runtime.combinators.types import Budget, Rejected
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Fixtures: Mock Tasks
# =============================================================================


async def simple_task(input: str, scope: Scope) -> str:
    """Simple task that emits an effect and returns processed input."""
    scope.emit(Effect(effect_type="simple_effect"))
    return f"processed: {input}"


async def multi_effect_task(input: str, scope: Scope) -> str:
    """Task that emits multiple effects."""
    for i in range(5):
        scope.emit(Effect(effect_type=f"effect_{i}"))
    return f"multi: {input}"


async def file_task(input: str, scope: Scope) -> str:
    """Task that creates files."""
    scope.emit(FileCreate(path="/a.py", content=""))
    scope.emit(FileCreate(path="/b.py", content=""))
    scope.emit(FileCreate(path="/c.py", content=""))
    return f"files: {input}"


async def failing_task(input: str, scope: Scope) -> str:
    """Task that raises an exception."""
    scope.emit(Effect(effect_type="before_fail"))
    raise ValueError("Intentional failure")


async def slow_task(input: str, scope: Scope) -> str:
    """Task that takes a while to complete."""
    scope.emit(Effect(effect_type="slow_start"))
    await asyncio.sleep(0.3)  # 300ms - longer than test timeout (100ms) but fast
    scope.emit(Effect(effect_type="slow_end"))
    return f"slow: {input}"


# =============================================================================
# Tests for gate()
# =============================================================================


class TestGate:
    """Tests for gate combinator."""

    @pytest.mark.asyncio
    async def test_gate_merges_on_approval(self):
        """gate() merges effects when predicate returns True."""

        def approve(result, effects):
            return True

        gated = gate(simple_task, approve)

        with Scope() as scope:
            result = await gated("test", scope)

            assert result == "processed: test"
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "simple_effect"

    @pytest.mark.asyncio
    async def test_gate_rejects_on_disapproval(self):
        """gate() returns Rejected when predicate returns False."""

        def reject(result, effects):
            return False

        gated = gate(simple_task, reject)

        with Scope() as scope:
            result = await gated("test", scope)

            assert isinstance(result, Rejected)
            assert result.value == "processed: test"
            assert len(result.effects) == 1  # Effects captured
            assert len(scope.effects) == 0  # But not merged to parent

    @pytest.mark.asyncio
    async def test_gate_predicate_receives_effects(self):
        """gate() predicate receives the effects stream."""
        received_effects = []

        def capture_effects(result, effects):
            received_effects.append(len(effects))
            return True

        gated = gate(multi_effect_task, capture_effects)

        with Scope() as scope:
            await gated("test", scope)

            assert received_effects == [5]

    @pytest.mark.asyncio
    async def test_gate_async_predicate(self):
        """gate() supports async predicates."""

        async def async_approve(result, effects):
            await asyncio.sleep(0.01)  # Simulate async work
            return len(effects) < 10

        gated = gate(multi_effect_task, async_approve)

        with Scope() as scope:
            result = await gated("test", scope)

            assert result == "multi: test"
            assert len(scope.effects) == 5

    @pytest.mark.asyncio
    async def test_gate_cleans_up_on_exception(self):
        """gate() discards fork and re-raises on exception."""

        def approve(result, effects):
            return True  # Never reached

        gated = gate(failing_task, approve)

        with Scope() as scope:
            with pytest.raises(ValueError, match="Intentional failure"):
                await gated("test", scope)

            # Parent scope should be unchanged
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_gate_preserves_task_name(self):
        """gate() preserves task name for debugging."""
        gated = gate(simple_task, lambda r, e: True)

        assert "simple_task" in gated.__name__


# =============================================================================
# Tests for budget()
# =============================================================================


class TestBudget:
    """Tests for budget combinator."""

    @pytest.mark.asyncio
    async def test_budget_allows_within_limits(self):
        """budget() merges when within limits."""
        limited = budget(simple_task, Budget(max_effects=10))

        with Scope() as scope:
            result = await limited("test", scope)

            assert result == "processed: test"
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_budget_rejects_excess_effects(self):
        """budget() rejects when effect limit exceeded."""
        limited = budget(multi_effect_task, Budget(max_effects=3))

        with Scope() as scope:
            result = await limited("test", scope)

            assert isinstance(result, Rejected)
            assert "Effect limit exceeded" in result.reason
            assert len(scope.effects) == 0  # Effects not merged

    @pytest.mark.asyncio
    async def test_budget_rejects_excess_files(self):
        """budget() rejects when file limit exceeded."""
        limited = budget(file_task, Budget(max_files=2))

        with Scope() as scope:
            result = await limited("test", scope)

            assert isinstance(result, Rejected)
            assert "File limit exceeded" in result.reason

    @pytest.mark.asyncio
    async def test_budget_cleans_up_on_exception(self):
        """budget() discards fork and re-raises on exception."""
        limited = budget(failing_task, Budget(max_effects=100))

        with Scope() as scope:
            with pytest.raises(ValueError):
                await limited("test", scope)

            assert len(scope.effects) == 0


# =============================================================================
# Tests for timeout()
# =============================================================================


class TestTimeout:
    """Tests for timeout combinator."""

    @pytest.mark.asyncio
    async def test_timeout_allows_fast_task(self):
        """timeout() merges effects when task completes in time."""
        timed = timeout(simple_task, seconds=5.0)

        with Scope() as scope:
            result = await timed("test", scope)

            assert result == "processed: test"
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_timeout_rejects_slow_task(self):
        """timeout() rejects when task exceeds time limit."""
        timed = timeout(slow_task, seconds=0.1)  # 100ms timeout

        with Scope() as scope:
            result = await timed("test", scope)

            assert isinstance(result, Rejected)
            assert "Timeout" in result.reason
            # Effects before timeout are captured
            assert len(result.effects) >= 1
            # But not merged to parent
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_timeout_cleans_up_on_exception(self):
        """timeout() discards fork and re-raises on exception."""
        timed = timeout(failing_task, seconds=5.0)

        with Scope() as scope:
            with pytest.raises(ValueError):
                await timed("test", scope)

            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_timeout_preserves_task_name(self):
        """timeout() preserves task name for debugging."""
        timed = timeout(simple_task, 5.0)

        assert "simple_task" in timed.__name__
