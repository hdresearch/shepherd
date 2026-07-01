"""Tests for effect transformation combinators: filter_effects, map_effects, tap."""

import asyncio

import pytest
from shepherd_core.effects import Effect, FileCreate
from shepherd_runtime.combinators.effects import (
    filter_effects,
    map_effects,
    scope_tap,
    tap,
)
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Fixtures: Mock Tasks
# =============================================================================


async def multi_effect_task(input: str, scope: Scope) -> str:
    """Task that emits multiple effect types."""
    scope.emit(Effect(effect_type="log", task_name="test"))
    scope.emit(FileCreate(path="/a.py", content=""))
    scope.emit(Effect(effect_type="metric", task_name="test"))
    scope.emit(FileCreate(path="/b.py", content=""))
    return f"processed: {input}"


async def simple_task(input: str, scope: Scope) -> str:
    """Simple task that emits one effect."""
    scope.emit(Effect(effect_type="simple"))
    return f"simple: {input}"


async def failing_task(input: str, scope: Scope) -> str:
    """Task that always fails."""
    scope.emit(Effect(effect_type="before_fail"))
    raise ValueError("Intentional failure")


# =============================================================================
# Tests for filter_effects()
# =============================================================================


class TestFilterEffects:
    """Tests for filter_effects combinator."""

    @pytest.mark.asyncio
    async def test_filter_keeps_matching_effects(self):
        """filter_effects() keeps effects matching predicate."""
        filtered = filter_effects(multi_effect_task, lambda e: isinstance(e, FileCreate))

        with Scope() as scope:
            result = await filtered("test", scope)

            assert result == "processed: test"
            # Only FileCreate effects should be kept
            assert len(scope.effects) == 2
            for layer in scope.effects:
                assert isinstance(layer.effect, FileCreate)

    @pytest.mark.asyncio
    async def test_filter_removes_non_matching_effects(self):
        """filter_effects() removes effects not matching predicate."""
        filtered = filter_effects(multi_effect_task, lambda e: e.effect_type == "log")

        with Scope() as scope:
            await filtered("test", scope)

            # Only log effects should be kept
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "log"

    @pytest.mark.asyncio
    async def test_filter_empty_result(self):
        """filter_effects() can result in no effects."""
        filtered = filter_effects(
            multi_effect_task,
            lambda e: False,  # Reject all
        )

        with Scope() as scope:
            result = await filtered("test", scope)

            assert result == "processed: test"
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_filter_cleans_up_on_exception(self):
        """filter_effects() discards fork on exception."""
        filtered = filter_effects(failing_task, lambda e: True)

        with Scope() as scope:
            with pytest.raises(ValueError):
                await filtered("test", scope)

            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_filter_preserves_task_name(self):
        """filter_effects() preserves task name for debugging."""
        filtered = filter_effects(simple_task, lambda e: True)

        assert "simple_task" in filtered.__name__


# =============================================================================
# Tests for map_effects()
# =============================================================================


class TestMapEffects:
    """Tests for map_effects combinator."""

    @pytest.mark.asyncio
    async def test_map_transforms_effects(self):
        """map_effects() transforms each effect."""
        mapped = map_effects(simple_task, lambda e: e.with_attribution(task_name="transformed"))

        with Scope() as scope:
            result = await mapped("test", scope)

            assert result == "simple: test"
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.task_name == "transformed"

    @pytest.mark.asyncio
    async def test_map_transforms_all_effects(self):
        """map_effects() transforms all effects in stream."""
        mapped = map_effects(multi_effect_task, lambda e: e.with_attribution(binding_name="mapped"))

        with Scope() as scope:
            await mapped("test", scope)

            assert len(scope.effects) == 4
            for layer in scope.effects:
                assert layer.effect.binding_name == "mapped"

    @pytest.mark.asyncio
    async def test_map_cleans_up_on_exception(self):
        """map_effects() discards fork on exception."""
        mapped = map_effects(failing_task, lambda e: e)

        with Scope() as scope:
            with pytest.raises(ValueError):
                await mapped("test", scope)

            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_map_preserves_task_name(self):
        """map_effects() preserves task name for debugging."""
        mapped = map_effects(simple_task, lambda e: e)

        assert "simple_task" in mapped.__name__


# =============================================================================
# Tests for tap()
# =============================================================================


class TestTap:
    """Tests for tap combinator."""

    @pytest.mark.asyncio
    async def test_tap_calls_observer(self):
        """tap() calls observer with result and effects."""
        observed_results = []

        def observer(result, effects):
            observed_results.append((result, len(effects)))

        tapped = tap(simple_task, observer)

        with Scope() as scope:
            result = await tapped("test", scope)

            assert result == "simple: test"
            assert observed_results == [("simple: test", 1)]

    @pytest.mark.asyncio
    async def test_tap_does_not_modify_result(self):
        """tap() does not modify the result."""

        def observer(result, effects):
            return "modified"  # Return value should be ignored

        tapped = tap(simple_task, observer)

        with Scope() as scope:
            result = await tapped("test", scope)

            assert result == "simple: test"

    @pytest.mark.asyncio
    async def test_tap_merges_effects(self):
        """tap() merges all effects (doesn't filter)."""

        def observer(result, effects):
            pass

        tapped = tap(multi_effect_task, observer)

        with Scope() as scope:
            await tapped("test", scope)

            # All effects should be merged
            assert len(scope.effects) == 4

    @pytest.mark.asyncio
    async def test_tap_async_observer(self):
        """tap() supports async observers."""
        observed = []

        async def async_observer(result, effects):
            await asyncio.sleep(0.01)
            observed.append(result)

        tapped = tap(simple_task, async_observer)

        with Scope() as scope:
            result = await tapped("test", scope)

            assert result == "simple: test"
            assert observed == ["simple: test"]

    @pytest.mark.asyncio
    async def test_tap_cleans_up_on_exception(self):
        """tap() discards fork on task exception."""

        def observer(result, effects):
            pass

        tapped = tap(failing_task, observer)

        with Scope() as scope:
            with pytest.raises(ValueError):
                await tapped("test", scope)

            assert len(scope.effects) == 0


# =============================================================================
# Tests for scope_tap()
# =============================================================================


class TestScopeTap:
    """Tests for scope_tap combinator."""

    @pytest.mark.asyncio
    async def test_scope_tap_receives_scope(self):
        """scope_tap() observer receives the child scope."""
        observed_scopes = []

        def observer(child_scope):
            observed_scopes.append(child_scope.id)

        tapped = scope_tap(simple_task, observer)

        with Scope() as scope:
            await tapped("test", scope)

            assert len(observed_scopes) == 1
            # Child scope ID should be different from parent
            assert observed_scopes[0] != scope.id

    @pytest.mark.asyncio
    async def test_scope_tap_can_inspect_stream(self):
        """scope_tap() can inspect the child scope's stream."""
        stream_lengths = []

        def observer(child_scope):
            stream_lengths.append(len(child_scope.effects))

        tapped = scope_tap(multi_effect_task, observer)

        with Scope() as scope:
            await tapped("test", scope)

            assert stream_lengths == [4]

    @pytest.mark.asyncio
    async def test_scope_tap_async_observer(self):
        """scope_tap() supports async observers."""
        scope_ids = []

        async def async_observer(child_scope):
            await asyncio.sleep(0.01)
            scope_ids.append(child_scope.id)

        tapped = scope_tap(simple_task, async_observer)

        with Scope() as scope:
            await tapped("test", scope)

            assert len(scope_ids) == 1

    @pytest.mark.asyncio
    async def test_scope_tap_preserves_task_name(self):
        """scope_tap() preserves task name for debugging."""
        tapped = scope_tap(simple_task, lambda s: None)

        assert "simple_task" in tapped.__name__
