"""Tests for composition combinators: sequence, branch, loop."""

import asyncio

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.combinators.composition import (
    branch,
    loop,
    sequence,
    sequence_all,
)
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Fixtures: Mock Tasks
# =============================================================================


async def double(input: int, scope: Scope) -> int:
    """Task that doubles input."""
    scope.emit(Effect(effect_type="double"))
    return input * 2


async def add_one(input: int, scope: Scope) -> int:
    """Task that adds one."""
    scope.emit(Effect(effect_type="add_one"))
    return input + 1


async def to_string(input: int, scope: Scope) -> str:
    """Task that converts to string."""
    scope.emit(Effect(effect_type="to_string"))
    return f"value: {input}"


async def parse_int(input: str, scope: Scope) -> int:
    """Task that parses integer from string."""
    scope.emit(Effect(effect_type="parse_int"))
    return int(input.split(": ")[1])


async def uppercase(input: str, scope: Scope) -> str:
    """Task that uppercases string."""
    scope.emit(Effect(effect_type="uppercase"))
    return input.upper()


async def append_suffix(input: str, scope: Scope) -> str:
    """Task that appends suffix."""
    scope.emit(Effect(effect_type="append_suffix"))
    return f"{input}_done"


# =============================================================================
# Tests for sequence()
# =============================================================================


class TestSequence:
    """Tests for sequence combinator."""

    @pytest.mark.asyncio
    async def test_sequence_chains_tasks(self):
        """sequence() pipes first output to second input."""
        chained = sequence(double, add_one)

        with Scope() as scope:
            result = await chained(5, scope)

            assert result == 11  # (5 * 2) + 1
            # Both effects should be present
            assert len(scope.effects) == 2

    @pytest.mark.asyncio
    async def test_sequence_preserves_effect_order(self):
        """sequence() preserves effect ordering."""
        chained = sequence(double, add_one)

        with Scope() as scope:
            await chained(5, scope)

            effect_types = [layer.effect.effect_type for layer in scope.effects]
            assert effect_types == ["double", "add_one"]

    @pytest.mark.asyncio
    async def test_sequence_different_types(self):
        """sequence() works with different input/output types."""
        chained = sequence(double, to_string)

        with Scope() as scope:
            result = await chained(5, scope)

            assert result == "value: 10"

    @pytest.mark.asyncio
    async def test_sequence_preserves_task_names(self):
        """sequence() preserves task names for debugging."""
        chained = sequence(double, add_one)

        assert "double" in chained.__name__
        assert "add_one" in chained.__name__


# =============================================================================
# Tests for sequence_all()
# =============================================================================


class TestSequenceAll:
    """Tests for sequence_all combinator."""

    @pytest.mark.asyncio
    async def test_sequence_all_chains_multiple(self):
        """sequence_all() chains multiple tasks."""
        pipeline = sequence_all(double, add_one, double)

        with Scope() as scope:
            result = await pipeline(5, scope)

            assert result == 22  # ((5 * 2) + 1) * 2
            assert len(scope.effects) == 3

    @pytest.mark.asyncio
    async def test_sequence_all_single_task(self):
        """sequence_all() works with single task."""
        pipeline = sequence_all(double)

        with Scope() as scope:
            result = await pipeline(5, scope)

            assert result == 10

    @pytest.mark.asyncio
    async def test_sequence_all_requires_tasks(self):
        """sequence_all() raises if no tasks provided."""
        with pytest.raises(ValueError, match="at least one task"):
            sequence_all()

    @pytest.mark.asyncio
    async def test_sequence_all_preserves_order(self):
        """sequence_all() preserves effect ordering."""
        pipeline = sequence_all(double, add_one, double)

        with Scope() as scope:
            await pipeline(5, scope)

            effect_types = [layer.effect.effect_type for layer in scope.effects]
            assert effect_types == ["double", "add_one", "double"]


# =============================================================================
# Tests for branch()
# =============================================================================


class TestBranch:
    """Tests for branch combinator."""

    @pytest.mark.asyncio
    async def test_branch_executes_true_branch(self):
        """branch() executes if_true when predicate returns True."""
        branching = branch(lambda x: x > 10, if_true=double, if_false=add_one)

        with Scope() as scope:
            result = await branching(15, scope)

            assert result == 30  # doubled
            assert scope.effects[0].effect.effect_type == "double"

    @pytest.mark.asyncio
    async def test_branch_executes_false_branch(self):
        """branch() executes if_false when predicate returns False."""
        branching = branch(lambda x: x > 10, if_true=double, if_false=add_one)

        with Scope() as scope:
            result = await branching(5, scope)

            assert result == 6  # add_one
            assert scope.effects[0].effect.effect_type == "add_one"

    @pytest.mark.asyncio
    async def test_branch_async_predicate(self):
        """branch() supports async predicates."""

        async def async_check(x):
            await asyncio.sleep(0.01)
            return x > 10

        branching = branch(async_check, if_true=double, if_false=add_one)

        with Scope() as scope:
            result = await branching(15, scope)

            assert result == 30

    @pytest.mark.asyncio
    async def test_branch_only_one_effect(self):
        """branch() only emits effects from chosen branch."""
        branching = branch(lambda x: x > 10, if_true=double, if_false=add_one)

        with Scope() as scope:
            await branching(15, scope)

            # Only one effect (from true branch)
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "double"


# =============================================================================
# Tests for loop()
# =============================================================================


class TestLoop:
    """Tests for loop combinator."""

    @pytest.mark.asyncio
    async def test_loop_iterates_until_condition(self):
        """loop() repeats until predicate returns True."""
        looping = loop(double, until=lambda x: x > 100, max_iterations=10)

        with Scope() as scope:
            result = await looping(5, scope)

            # 5 -> 10 -> 20 -> 40 -> 80 -> 160 (stops here)
            assert result == 160
            assert len(scope.effects) == 5  # 5 iterations

    @pytest.mark.asyncio
    async def test_loop_stops_at_max_iterations(self):
        """loop() raises when max_iterations exceeded."""
        looping = loop(add_one, until=lambda x: x > 1000, max_iterations=3)

        with Scope() as scope, pytest.raises(RuntimeError, match="exceeded 3 iterations"):
            await looping(0, scope)

    @pytest.mark.asyncio
    async def test_loop_first_iteration_satisfies(self):
        """loop() returns immediately if first result satisfies predicate."""
        looping = loop(double, until=lambda x: x > 5, max_iterations=10)

        with Scope() as scope:
            result = await looping(10, scope)

            # 10 -> 20 (satisfies immediately)
            assert result == 20
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_loop_async_predicate(self):
        """loop() supports async predicates."""

        async def async_check(x):
            await asyncio.sleep(0.01)
            return x > 50

        looping = loop(double, until=async_check, max_iterations=10)

        with Scope() as scope:
            result = await looping(5, scope)

            assert result == 80  # 5 -> 10 -> 20 -> 40 -> 80

    @pytest.mark.asyncio
    async def test_loop_accumulates_effects(self):
        """loop() accumulates effects across iterations."""
        looping = loop(add_one, until=lambda x: x >= 3, max_iterations=10)

        with Scope() as scope:
            result = await looping(0, scope)

            assert result == 3
            # Each iteration emits one effect
            assert len(scope.effects) == 3

    @pytest.mark.asyncio
    async def test_loop_preserves_task_name(self):
        """loop() preserves task name for debugging."""
        looping = loop(double, until=lambda x: True)

        assert "double" in looping.__name__
