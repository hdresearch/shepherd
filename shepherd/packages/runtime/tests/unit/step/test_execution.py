"""Tests for execution utilities: run_sync, run_in_thread, ContextVar propagation.

These tests validate:
1. run_sync handles nested event loops correctly
2. run_in_thread propagates ContextVars on Python 3.11+
3. Thread safety of concurrent operations
"""

import asyncio
import threading
from contextvars import ContextVar

import pytest
from shepherd_runtime.step._execution import run_in_thread, run_sync

# =============================================================================
# Test ContextVar for Propagation Tests
# =============================================================================

_test_context: ContextVar[str] = ContextVar("test_context", default="unset")


# =============================================================================
# Tests for run_sync
# =============================================================================


class TestRunSync:
    """Tests for run_sync() function."""

    def test_run_sync_executes_coroutine(self):
        """run_sync executes a coroutine and returns result."""

        async def simple_coro():
            return 42

        result = run_sync(simple_coro())
        assert result == 42

    def test_run_sync_propagates_exception(self):
        """run_sync propagates exceptions from coroutine."""

        async def failing_coro():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_sync(failing_coro())

    def test_run_sync_preserves_context_vars(self):
        """run_sync preserves ContextVars when using thread pool."""
        _test_context.set("test_value")

        async def check_context():
            # This runs in a different thread when nested
            return _test_context.get()

        # Simulate nested async context by running inside an event loop
        async def outer():
            # Inside an event loop, run_sync uses thread pool
            return run_sync(check_context())

        result = asyncio.run(outer())
        assert result == "test_value"


# =============================================================================
# Tests for run_in_thread
# =============================================================================


class TestRunInThread:
    """Tests for run_in_thread() function."""

    @pytest.mark.asyncio
    async def test_run_in_thread_executes_sync_function(self):
        """run_in_thread executes sync function and returns result."""

        def sync_function(x, y):
            return x + y

        result = await run_in_thread(sync_function, 10, 20)
        assert result == 30

    @pytest.mark.asyncio
    async def test_run_in_thread_propagates_exception(self):
        """run_in_thread propagates exceptions from sync function."""

        def failing_function():
            raise RuntimeError("sync error")

        with pytest.raises(RuntimeError, match="sync error"):
            await run_in_thread(failing_function)

    @pytest.mark.asyncio
    async def test_run_in_thread_preserves_context_vars(self):
        """run_in_thread preserves ContextVars in worker thread."""
        _test_context.set("preserved_value")

        def check_context():
            return _test_context.get()

        result = await run_in_thread(check_context)
        assert result == "preserved_value"

    @pytest.mark.asyncio
    async def test_run_in_thread_with_kwargs(self):
        """run_in_thread passes kwargs correctly."""

        def func_with_kwargs(a, b=10, c=20):
            return a + b + c

        result = await run_in_thread(func_with_kwargs, 1, b=2, c=3)
        assert result == 6

    @pytest.mark.asyncio
    async def test_run_in_thread_reports_correct_thread(self):
        """run_in_thread runs function in different thread."""
        main_thread_id = threading.current_thread().ident

        def get_thread_id():
            return threading.current_thread().ident

        worker_thread_id = await run_in_thread(get_thread_id)
        assert worker_thread_id != main_thread_id


# =============================================================================
# Tests for Thread Safety
# =============================================================================


class TestThreadSafety:
    """Tests for thread-safe operations."""

    def test_concurrent_emit_preserves_all_effects(self):
        """Concurrent emit() calls from multiple threads preserve all effects."""
        from shepherd_core.effects import Effect
        from shepherd_runtime.scope import Scope

        num_threads = 10
        effects_per_thread = 50

        with Scope() as scope:
            threads = []
            errors = []

            def emit_effects(thread_id):
                try:
                    for i in range(effects_per_thread):
                        scope.emit(
                            Effect(
                                effect_type="test",
                                thread_id=thread_id,
                                sequence_in_thread=i,
                            )
                        )
                except Exception as e:
                    errors.append(e)

            # Start all threads
            for t_id in range(num_threads):
                t = threading.Thread(target=emit_effects, args=(t_id,))
                threads.append(t)
                t.start()

            # Wait for all threads to complete
            for t in threads:
                t.join()

            # Check no errors occurred
            assert len(errors) == 0, f"Errors during concurrent emit: {errors}"

            # Check all effects were recorded
            expected_total = num_threads * effects_per_thread
            assert len(scope.effects) == expected_total, f"Expected {expected_total} effects, got {len(scope.effects)}"

    def test_concurrent_emit_unique_sequences(self):
        """Concurrent emit() assigns unique sequence numbers."""
        from shepherd_core.effects import Effect
        from shepherd_runtime.scope import Scope

        num_threads = 5
        effects_per_thread = 20

        with Scope() as scope:
            threads = []

            def emit_effects(thread_id):
                for i in range(effects_per_thread):
                    scope.emit(Effect(effect_type="test", thread_id=thread_id))

            for t_id in range(num_threads):
                t = threading.Thread(target=emit_effects, args=(t_id,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # Check all sequences are unique
            sequences = [layer.sequence for layer in scope.effects]
            assert len(sequences) == len(set(sequences)), "Duplicate sequences found"

            # Check sequences are contiguous (0 to N-1)
            expected_total = num_threads * effects_per_thread
            assert sorted(sequences) == list(range(expected_total))
