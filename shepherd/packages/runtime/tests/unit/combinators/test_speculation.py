"""Tests for speculation combinators: speculate."""

import warnings

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.combinators.speculation import SpeculativeResult, speculate
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


async def failing_task(input: str, scope: Scope) -> str:
    """Task that raises an exception."""
    scope.emit(Effect(effect_type="before_fail"))
    raise ValueError("Intentional failure")


# =============================================================================
# Tests for SpeculativeResult
# =============================================================================


class TestSpeculativeResult:
    """Tests for SpeculativeResult container."""

    @pytest.mark.asyncio
    async def test_speculate_returns_result_container(self):
        """speculate() returns SpeculativeResult, not raw value."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            result = await speculative("test", scope)

            assert isinstance(result, SpeculativeResult)
            assert result.output == "processed: test"
            assert len(result.effects) == 1
            # Not yet merged to parent
            assert len(scope.effects) == 0

            # Clean up
            result.abandon()

    @pytest.mark.asyncio
    async def test_commit_merges_effects(self):
        """commit() merges effects to parent scope."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            result = await speculative("test", scope)

            # Effects not merged yet
            assert len(scope.effects) == 0

            # Commit
            output = result.commit()

            # Now effects are merged
            assert output == "processed: test"
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "simple_effect"

    @pytest.mark.asyncio
    async def test_abandon_discards_effects(self):
        """abandon() discards effects without merging."""
        speculative = speculate(multi_effect_task)

        with Scope() as scope:
            result = await speculative("test", scope)

            # Captured 5 effects
            assert len(result.effects) == 5

            # Abandon
            result.abandon()

            # Parent scope unchanged
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_double_commit_raises(self):
        """Cannot commit() twice."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            result = await speculative("test", scope)
            result.commit()

            with pytest.raises(ValueError, match="already committed or abandoned"):
                result.commit()

    @pytest.mark.asyncio
    async def test_commit_after_abandon_raises(self):
        """Cannot commit() after abandon()."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            result = await speculative("test", scope)
            result.abandon()

            with pytest.raises(ValueError, match="already committed or abandoned"):
                result.commit()

    @pytest.mark.asyncio
    async def test_abandon_is_idempotent(self):
        """abandon() can be called multiple times safely."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            result = await speculative("test", scope)

            # Multiple abandons should not raise
            result.abandon()
            result.abandon()
            result.abandon()

            assert result.is_decided
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_context_manager_auto_abandons(self):
        """Context manager auto-abandons if not committed."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            with await speculative("test", scope) as result:
                # Don't commit - let context manager handle it
                pass

            # Auto-abandoned
            assert result.is_decided
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_context_manager_with_commit(self):
        """Context manager doesn't abandon if already committed."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            with await speculative("test", scope) as result:
                result.commit()

            # Committed, not abandoned
            assert result.is_decided
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_exception_cleanup(self):
        """Fork discarded if task raises."""
        speculative = speculate(failing_task)

        with Scope() as scope:
            with pytest.raises(ValueError, match="Intentional failure"):
                await speculative("test", scope)

            # Parent scope unchanged
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_map_transforms_output(self):
        """map() transforms output while preserving speculative context."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            result = await speculative("test", scope)
            mapped = result.map(len)

            assert mapped.output == len("processed: test")
            # Same effects
            assert len(mapped.effects) == 1

            # Commit on mapped result
            mapped.commit()
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_is_decided_property(self):
        """is_decided reflects commit/abandon state."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            result = await speculative("test", scope)

            assert not result.is_decided
            result.commit()
            assert result.is_decided

    @pytest.mark.asyncio
    async def test_nested_speculation(self):
        """Nested speculate calls work correctly."""

        async def outer_task(input: str, scope: Scope) -> str:
            scope.emit(Effect(effect_type="outer"))
            inner = await speculate(simple_task)(input, scope)
            inner.commit()
            return f"outer({inner.output})"

        speculative = speculate(outer_task)

        with Scope() as scope:
            result = await speculative("test", scope)

            # Both outer and inner effects captured
            assert len(result.effects) == 2

            result.commit()
            assert len(scope.effects) == 2

    @pytest.mark.asyncio
    async def test_preserves_task_name(self):
        """speculate() preserves task name for debugging."""
        speculative = speculate(simple_task)

        assert "simple_task" in speculative.__name__


# =============================================================================
# Tests for GC Warning
# =============================================================================


class TestSpeculativeResultGC:
    """Tests for garbage collection warning."""

    @pytest.mark.asyncio
    async def test_gc_warning_on_undecided(self):
        """ResourceWarning emitted if GC'd without decision."""
        speculative = speculate(simple_task)

        with Scope() as scope:
            # Create result but don't decide
            result = await speculative("test", scope)

            # Manually trigger __del__ to check warning
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result.__del__()

                assert len(w) == 1
                assert issubclass(w[0].category, ResourceWarning)
                assert "garbage collected without commit/abandon" in str(w[0].message)
