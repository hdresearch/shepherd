"""Tests for retry combinators: retry, fallback, recover."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.combinators.retry import fallback, recover, retry
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Fixtures: Mock Tasks
# =============================================================================


async def simple_task(input: str, scope: Scope) -> str:
    """Simple task that always succeeds."""
    scope.emit(Effect(effect_type="simple_effect"))
    return f"processed: {input}"


async def failing_task(input: str, scope: Scope) -> str:
    """Task that always fails."""
    scope.emit(Effect(effect_type="before_fail"))
    raise ValueError("Intentional failure")


def make_flaky_task(fail_count: int):
    """Create a task that fails N times then succeeds."""
    attempts = [0]

    async def flaky_task(input: str, scope: Scope) -> str:
        attempts[0] += 1
        scope.emit(Effect(effect_type=f"attempt_{attempts[0]}"))
        if attempts[0] <= fail_count:
            raise ValueError(f"Failure #{attempts[0]}")
        return f"success after {attempts[0]} attempts"

    return flaky_task, attempts


def make_conditional_task(valid_after: int):
    """Create a task that returns invalid results N times then valid."""
    attempts = [0]

    async def conditional_task(input: str, scope: Scope) -> dict:
        attempts[0] += 1
        scope.emit(Effect(effect_type=f"attempt_{attempts[0]}"))
        is_valid = attempts[0] > valid_after
        return {"value": input, "is_valid": is_valid, "attempt": attempts[0]}

    return conditional_task, attempts


# =============================================================================
# Tests for retry()
# =============================================================================


class TestRetry:
    """Tests for retry combinator."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_first_attempt(self):
        """retry() returns result on first success."""
        retrying = retry(simple_task, max_attempts=3)

        with Scope() as scope:
            result = await retrying("test", scope)

            assert result == "processed: test"
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_failures(self):
        """retry() succeeds after transient failures."""
        flaky, attempts = make_flaky_task(fail_count=2)
        retrying = retry(flaky, max_attempts=5)

        with Scope() as scope:
            result = await retrying("test", scope)

            assert "success" in result
            assert attempts[0] == 3  # Failed twice, succeeded third
            # Only successful attempt's effect should be merged
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "attempt_3"

    @pytest.mark.asyncio
    async def test_retry_exhausts_attempts(self):
        """retry() raises after max attempts exhausted."""
        retrying = retry(failing_task, max_attempts=3)

        with Scope() as scope:
            with pytest.raises(ValueError, match="Intentional failure"):
                await retrying("test", scope)

            # No effects should be merged (all attempts failed)
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_retry_with_until_predicate(self):
        """retry() retries until predicate is satisfied."""
        conditional, _attempts = make_conditional_task(valid_after=2)
        retrying = retry(conditional, max_attempts=5, until=lambda r: r["is_valid"])

        with Scope() as scope:
            result = await retrying("test", scope)

            assert result["is_valid"] is True
            assert result["attempt"] == 3
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_retry_with_delay(self):
        """retry() waits between attempts."""
        flaky, _ = make_flaky_task(fail_count=1)
        retrying = retry(flaky, max_attempts=3, delay_seconds=0.05)

        # Mock asyncio.sleep to verify delay is called correctly
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, Scope() as scope:
            await retrying("test", scope)

            # Should have called sleep once (after first failure) with 0.05 seconds
            mock_sleep.assert_called_once_with(0.05)

    @pytest.mark.asyncio
    async def test_retry_with_backoff(self):
        """retry() applies exponential backoff."""
        flaky, _ = make_flaky_task(fail_count=2)
        retrying = retry(
            flaky,
            max_attempts=4,
            delay_seconds=0.02,
            backoff=2.0,  # 20ms, 40ms, 80ms
        )

        # Mock asyncio.sleep to verify exponential backoff is applied
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, Scope() as scope:
            await retrying("test", scope)

            # Should have called sleep twice (after first and second failures)
            # with exponentially increasing delays: 0.02, then 0.04
            assert mock_sleep.call_count == 2
            calls = [call.args[0] for call in mock_sleep.call_args_list]
            assert calls[0] == 0.02  # First delay
            assert calls[1] == 0.04  # Second delay (0.02 * 2.0)

    @pytest.mark.asyncio
    async def test_retry_preserves_task_name(self):
        """retry() preserves task name for debugging."""
        retrying = retry(simple_task, max_attempts=3)

        assert "simple_task" in retrying.__name__


# =============================================================================
# Tests for fallback()
# =============================================================================


class TestFallback:
    """Tests for fallback combinator."""

    @pytest.mark.asyncio
    async def test_fallback_uses_first_success(self):
        """fallback() uses first task if it succeeds."""

        async def task_a(input: str, scope: Scope) -> str:
            scope.emit(Effect(effect_type="task_a"))
            return "result_a"

        async def task_b(input: str, scope: Scope) -> str:
            scope.emit(Effect(effect_type="task_b"))
            return "result_b"

        fb = fallback(task_a, task_b)

        with Scope() as scope:
            result = await fb("test", scope)

            assert result == "result_a"
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "task_a"

    @pytest.mark.asyncio
    async def test_fallback_tries_next_on_failure(self):
        """fallback() tries next task when first fails."""

        async def task_b(input: str, scope: Scope) -> str:
            scope.emit(Effect(effect_type="task_b"))
            return "result_b"

        fb = fallback(failing_task, task_b)

        with Scope() as scope:
            result = await fb("test", scope)

            assert result == "result_b"
            # Only task_b's effects merged
            assert len(scope.effects) == 1
            assert scope.effects[0].effect.effect_type == "task_b"

    @pytest.mark.asyncio
    async def test_fallback_raises_if_all_fail(self):
        """fallback() raises last error if all tasks fail."""

        async def fail_a(input: str, scope: Scope) -> str:
            raise ValueError("Error A")

        async def fail_b(input: str, scope: Scope) -> str:
            raise RuntimeError("Error B")

        fb = fallback(fail_a, fail_b)

        with Scope() as scope, pytest.raises(RuntimeError, match="Error B"):
            await fb("test", scope)

    @pytest.mark.asyncio
    async def test_fallback_requires_tasks(self):
        """fallback() raises if no tasks provided."""
        with pytest.raises(ValueError, match="at least one task"):
            fallback()

    @pytest.mark.asyncio
    async def test_fallback_preserves_task_names(self):
        """fallback() preserves task names for debugging."""

        async def task_a(input: str, scope: Scope) -> str:
            return "a"

        async def task_b(input: str, scope: Scope) -> str:
            return "b"

        fb = fallback(task_a, task_b)

        assert "task_a" in fb.__name__
        assert "task_b" in fb.__name__


# =============================================================================
# Tests for recover()
# =============================================================================


class TestRecover:
    """Tests for recover combinator."""

    @pytest.mark.asyncio
    async def test_recover_returns_result_on_success(self):
        """recover() returns normal result when task succeeds."""
        recovered = recover(simple_task, on_error=lambda e: "fallback")

        with Scope() as scope:
            result = await recovered("test", scope)

            assert result == "processed: test"
            assert len(scope.effects) == 1

    @pytest.mark.asyncio
    async def test_recover_calls_handler_on_failure(self):
        """recover() calls error handler when task fails."""
        errors_received = []

        def handle_error(e):
            errors_received.append(e)
            return "recovered"

        recovered = recover(failing_task, on_error=handle_error)

        with Scope() as scope:
            result = await recovered("test", scope)

            assert result == "recovered"
            assert len(errors_received) == 1
            assert isinstance(errors_received[0], ValueError)
            # No effects merged (task failed, fork discarded)
            assert len(scope.effects) == 0

    @pytest.mark.asyncio
    async def test_recover_async_handler(self):
        """recover() supports async error handlers."""

        async def async_handler(e):
            await asyncio.sleep(0.01)
            return f"async recovered from: {type(e).__name__}"

        recovered = recover(failing_task, on_error=async_handler)

        with Scope() as scope:
            result = await recovered("test", scope)

            assert "async recovered" in result
            assert "ValueError" in result

    @pytest.mark.asyncio
    async def test_recover_preserves_task_name(self):
        """recover() preserves task name for debugging."""
        recovered = recover(simple_task, on_error=lambda e: None)

        assert "simple_task" in recovered.__name__
