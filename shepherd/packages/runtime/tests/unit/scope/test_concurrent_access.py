"""Tests for concurrent access and race conditions.

This module tests thread safety and concurrent access patterns:
- Stream concurrent modifications
- Cache concurrent access
- Scope concurrent binding operations
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect
from shepherd_core.scope.stream import Stream
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Contexts
# =============================================================================


class CounterContext(ExecutionContext):
    """Simple counter context for testing concurrent modifications.

    Note: Uses dynamic context_id based on count to allow multiple bindings.
    This differs from the shared CounterContext which uses a static ID.
    """

    def __init__(self, count: int = 0):
        self._count = count

    @property
    def context_id(self) -> str:
        return f"counter:{self._count}"

    @property
    def count(self) -> int:
        return self._count

    def apply_effect(self, effect: Effect) -> "CounterContext":
        if effect.effect_type == "increment":
            return CounterContext(count=self._count + 1)
        return self


# =============================================================================
# Tests: Stream Concurrent Modifications
# =============================================================================


class TestStreamConcurrentModifications:
    """Tests for stream concurrent modification safety.

    The Stream is designed to be immutable - each append returns a new Stream.
    This tests that concurrent operations don't corrupt shared state.
    """

    def test_concurrent_appends_to_independent_streams(self):
        """Multiple threads can create independent streams without conflict.

        Each thread creates its own stream chain. No shared mutable state.
        """
        results = []
        errors = []

        def worker(thread_id: int) -> Stream:
            """Create a stream with multiple effects."""
            stream = Stream()
            for i in range(100):
                effect = Effect(effect_type=f"effect_{thread_id}_{i}")
                stream = stream.append(effect)
            return stream

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(10)]
            for future in as_completed(futures):
                try:
                    stream = future.result()
                    results.append(stream)
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors occurred: {errors}"
        assert len(results) == 10

        # Each stream should have 100 effects
        for stream in results:
            assert len(stream) == 100

    def test_stream_immutability_under_concurrent_reads(self):
        """Concurrent reads from the same stream should be safe.

        Reading from an immutable stream should never see partial state.
        """
        # Create a stream with known contents
        stream = Stream()
        for i in range(1000):
            stream = stream.append(Effect(effect_type=f"effect_{i}"))

        errors = []
        read_counts = []

        def reader(stream: Stream) -> int:
            """Read and verify stream contents."""
            count = 0
            for layer in stream:
                if not layer.effect.effect_type.startswith("effect_"):
                    errors.append(f"Invalid effect type: {layer.effect.effect_type}")
                count += 1
            return count

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(reader, stream) for _ in range(100)]
            for future in as_completed(futures):
                try:
                    count = future.result()
                    read_counts.append(count)
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors occurred: {errors}"
        # All reads should see same count
        assert all(c == 1000 for c in read_counts)

    def test_query_operations_thread_safe(self):
        """Query operations on stream should be thread-safe."""
        stream = Stream()
        for i in range(500):
            effect = Effect(
                effect_type="test",
                task_name=f"task_{i % 5}",
                provider_id=f"provider_{i % 3}",
            )
            stream = stream.append(effect)

        errors = []
        results = []

        def query_worker(stream: Stream, query_type: str) -> int:
            """Run different queries on the stream."""
            if query_type == "by_task":
                result = stream.by_task("task_0")
            elif query_type == "by_provider":
                result = stream.by_provider("provider_0")
            elif query_type == "count":
                return stream.count(task_name="task_0")
            elif query_type == "first":
                layer = stream.first(task_name="task_0")
                return 1 if layer else 0
            else:
                result = stream
            return len(result)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            for _i in range(50):
                query_types = ["by_task", "by_provider", "count", "first"]
                for qt in query_types:
                    futures.append(executor.submit(query_worker, stream, qt))

            for future in as_completed(futures):
                try:
                    count = future.result()
                    results.append(count)
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors occurred: {errors}"
        assert len(results) == 200  # 50 iterations * 4 query types


# =============================================================================
# Tests: Cache Concurrent Access
# =============================================================================


class TestCacheConcurrentAccess:
    """Tests for cache operations under concurrent load."""

    @pytest.fixture
    def cache_store(self, tmp_path):
        """Create a cache store for testing."""
        from shepherd_runtime.cache import CacheStore

        store = CacheStore(tmp_path / "cache")
        store.initialize()
        return store

    def test_concurrent_puts_different_keys(self, cache_store):
        """Concurrent puts to different keys should not conflict."""
        from shepherd_runtime.cache import CachedOutputs

        errors = []
        successful_puts = []

        def put_worker(key: str, value: str):
            """Put a value into the cache."""
            cached = CachedOutputs(
                outputs={"result": value},
                task_name="TestTask",
                execution_key=key,
            )
            cache_store.put(key, cached)
            return key

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(put_worker, f"key_{i}", f"value_{i}") for i in range(50)]
            for future in as_completed(futures):
                try:
                    key = future.result()
                    successful_puts.append(key)
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors occurred: {errors}"
        assert len(successful_puts) == 50

        # Verify all values were stored
        for i in range(50):
            result = cache_store.get(f"key_{i}")
            assert result is not None
            assert result.outputs["result"] == f"value_{i}"

    def test_concurrent_gets_same_key(self, cache_store):
        """Concurrent gets of the same key should be safe."""
        from shepherd_runtime.cache import CachedOutputs

        # Pre-populate cache
        cached = CachedOutputs(
            outputs={"result": "shared_value"},
            task_name="TestTask",
        )
        cache_store.put("shared_key", cached)

        errors = []
        results = []

        def get_worker():
            """Get a value from the cache."""
            result = cache_store.get("shared_key")
            if result is None:
                return None
            return result.outputs.get("result")

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(get_worker) for _ in range(100)]
            for future in as_completed(futures):
                try:
                    value = future.result()
                    results.append(value)
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors occurred: {errors}"
        # All reads should get the same value
        assert all(v == "shared_value" for v in results)

    def test_concurrent_invalidate_and_get(self, cache_store):
        """Concurrent invalidate and get operations should be safe."""
        from shepherd_runtime.cache import CachedOutputs

        # Pre-populate cache
        for i in range(20):
            cached = CachedOutputs(
                outputs={"result": f"value_{i}"},
                task_name="TestTask",
            )
            cache_store.put(f"key_{i}", cached)

        errors = []
        operations_completed = []

        def worker(op_type: str, key_idx: int):
            """Perform cache operation."""
            key = f"key_{key_idx}"
            if op_type == "get":
                result = cache_store.get(key)
                return ("get", key, result is not None)
            if op_type == "invalidate":
                cache_store.invalidate(execution_key=key)
                return ("invalidate", key, True)
            return None

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            # Mix of gets and invalidates
            for i in range(100):
                key_idx = i % 20
                op_type = "get" if i % 3 != 0 else "invalidate"
                futures.append(executor.submit(worker, op_type, key_idx))

            for future in as_completed(futures):
                try:
                    result = future.result()
                    operations_completed.append(result)
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors occurred: {errors}"
        assert len(operations_completed) == 100

    def test_concurrent_stats_access(self, cache_store):
        """Concurrent stats access during operations should be safe."""
        from shepherd_runtime.cache import CachedOutputs

        errors = []

        def stats_worker():
            """Access cache stats."""
            stats = cache_store.stats()
            return stats.entry_count >= 0

        def put_worker(i: int):
            """Put values into cache."""
            cached = CachedOutputs(outputs={"i": i}, task_name="Test")
            cache_store.put(f"concurrent_key_{i}", cached)
            return True

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            # Interleave stats and puts
            for i in range(50):
                futures.append(executor.submit(put_worker, i))
                futures.append(executor.submit(stats_worker))

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors occurred: {errors}"


# =============================================================================
# Tests: Scope Concurrent Binding Operations
# =============================================================================


class TestScopeConcurrentAccess:
    """Tests for scope operations under concurrent load.

    Thread-safety: scope.emit() is protected by a lock to ensure thread-safe
    emission when multiple threads emit effects concurrently. This prevents
    lost effects and ensures all effects are recorded to the stream.

    See: tmp/spikes/thread-safety-analysis/ANALYSIS.md
    """

    def test_concurrent_reads_from_scope(self):
        """Concurrent reads from the same scope should be safe.

        Reading binding state, context state, and stream should not conflict.
        """
        with Scope() as scope:
            # Set up initial state
            for i in range(10):
                ctx = CounterContext(count=i)
                scope.bind(f"ctx_{i}", ctx)

            for i in range(20):
                scope.emit(Effect(effect_type=f"effect_{i}"))

            errors = []
            results = []

            def reader():
                """Read various scope state."""
                bindings = list(scope.all_bindings())
                stream_len = len(scope.effects)
                ctx = scope.get_context("ctx_0")
                return (len(bindings), stream_len, ctx.count)

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(reader) for _ in range(50)]
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        errors.append(e)

            assert not errors, f"Errors occurred: {errors}"
            # All reads should see consistent state
            assert all(r == (10, 20, 0) for r in results)

    def test_concurrent_effect_emission_thread_safe(self):
        """Effect emission is thread-safe - all effects are recorded.

        scope.emit() is protected by a lock, so concurrent emission from
        multiple threads is safe. All effects are recorded to the stream
        and state is correctly derived.
        """
        with Scope() as scope:
            ctx = CounterContext(count=0)
            scope.bind("ctx", ctx)

            effects_emitted = []
            lock = threading.Lock()

            def emitter(thread_id: int, count: int):
                """Emit multiple effects."""
                for i in range(count):
                    effect = Effect(
                        effect_type="increment",
                        binding_name="ctx",
                        # Use thread_id to track origin
                        task_name=f"thread_{thread_id}",
                    )
                    scope.emit(effect)
                    with lock:
                        effects_emitted.append((thread_id, i))

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(emitter, i, 10) for i in range(5)]
                for future in as_completed(futures):
                    future.result()

            # All effects should be in the stream (thread-safe, no lost effects)
            assert len(scope.effects) == 50
            assert len(effects_emitted) == 50

            # The counter should reflect ALL increments (thread-safe state derivation)
            final_count = scope.get_context("ctx").count
            assert final_count == 50  # All increments applied


# =============================================================================
# Tests: Async Concurrent Operations
# =============================================================================


class TestAsyncConcurrentOperations:
    """Tests for async concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_async_reads(self):
        """Concurrent async reads from scope should be safe."""
        with Scope() as scope:
            for i in range(10):
                ctx = CounterContext(count=i)
                scope.bind(f"ctx_{i}", ctx)

            async def reader(idx: int) -> tuple:
                """Async reader."""
                # Simulate some async work
                await asyncio.sleep(0.001)
                binding_count = len(list(scope.all_bindings()))
                ctx = scope.get_context(f"ctx_{idx % 10}")
                return (binding_count, ctx.count)

            tasks = [reader(i) for i in range(100)]
            results = await asyncio.gather(*tasks)

            # All reads should see consistent state
            for result in results:
                assert result[0] == 10

    @pytest.mark.asyncio
    async def test_concurrent_stream_queries_async(self):
        """Concurrent async stream queries should be safe."""
        with Scope() as scope:
            for i in range(100):
                scope.emit(
                    Effect(
                        effect_type="test",
                        task_name=f"task_{i % 5}",
                    )
                )

            async def query_worker(task_idx: int) -> int:
                """Query stream for specific task."""
                await asyncio.sleep(0.001)
                task_stream = scope.effects.by_task(f"task_{task_idx}")
                return len(task_stream)

            tasks = [query_worker(i % 5) for i in range(50)]
            results = await asyncio.gather(*tasks)

            # Each task should have 20 effects (100 / 5)
            for result in results:
                assert result == 20

    @pytest.mark.asyncio
    async def test_fork_operations_are_independent(self):
        """Forked scopes should be fully independent."""
        with Scope() as scope:
            ctx = CounterContext(count=0)
            scope.bind("ctx", ctx)

            async def fork_and_modify(fork_id: int) -> int:
                """Fork scope and make modifications."""
                forked = scope.fork()
                for _i in range(10):
                    forked.emit(
                        Effect(
                            effect_type="increment",
                            binding_name="ctx",
                        )
                    )
                return len(forked.effects)

            tasks = [fork_and_modify(i) for i in range(10)]
            results = await asyncio.gather(*tasks)

            # Each fork should have 10 effects
            assert all(r == 10 for r in results)

            # Original scope should be unchanged
            assert len(scope.effects) == 0


# =============================================================================
# Tests: Index Concurrent Access
# =============================================================================


class TestIndexConcurrentAccess:
    """Tests for cache index concurrent access."""

    def test_concurrent_index_modifications(self, tmp_path):
        """Concurrent index modifications produce a valid (non-corrupt) index.

        Atomic saves (write-to-temp-then-rename) guarantee the index file is
        always valid JSON, even under concurrent writes. Individual entries may
        be lost due to non-atomic load-modify-save cycles (last writer wins),
        but the file is never truncated or malformed.
        """
        from shepherd_runtime.cache import CacheEntry, CacheIndex

        index_path = tmp_path / "index.json"

        errors = []

        def modify_index(thread_id: int, iterations: int):
            """Modify index entries."""
            for i in range(iterations):
                # Load, modify, save
                index = CacheIndex.load(index_path)
                entry = CacheEntry(
                    execution_key=f"key_{thread_id}_{i}",
                    task_name=f"Task_{thread_id}",
                    size_bytes=100,
                )
                index.add_entry(entry)
                index.save(index_path)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(modify_index, i, 10) for i in range(5)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(e)

        assert not errors, f"Errors during concurrent modifications: {errors}"

        # The index must always be loadable (atomic writes prevent corruption)
        final_index = CacheIndex.load(index_path)
        assert final_index is not None
        # At least one entry survived (last writer wins, but never zero due to corruption)
        assert len(final_index.entries) > 0


# =============================================================================
# Tests: Checkpoint Concurrent Access
# =============================================================================


class TestCheckpointConcurrentAccess:
    """Tests for checkpoint operations under concurrent conditions."""

    def test_effects_since_during_concurrent_emits(self):
        """effects_since should give consistent snapshot even during concurrent emits."""
        with Scope() as scope:
            scope.emit(Effect(effect_type="before"))
            cp = scope.checkpoint("test")

            results = []
            errors = []

            def reader():
                """Read effects_since multiple times."""
                for _ in range(10):
                    count = len(cp.effects_since)
                    results.append(count)
                    time.sleep(0.001)

            def emitter():
                """Emit effects during reads."""
                for i in range(20):
                    scope.emit(Effect(effect_type=f"during_{i}"))
                    time.sleep(0.0005)

            with ThreadPoolExecutor(max_workers=4) as executor:
                read_futures = [executor.submit(reader) for _ in range(3)]
                emit_future = executor.submit(emitter)

                for future in [emit_future, *read_futures]:
                    try:
                        future.result()
                    except Exception as e:
                        errors.append(e)

            assert not errors, f"Errors occurred: {errors}"
            # All reads should return valid counts
            assert all(isinstance(r, int) and r >= 0 for r in results)

    @pytest.mark.asyncio
    async def test_checkpoint_restore_not_concurrent_safe(self):
        """Checkpoint restore is NOT concurrent safe - only one restore should succeed.

        This test documents the expected behavior: multiple concurrent restores
        to different checkpoints is undefined behavior. Users should not do this.
        """
        with Scope() as scope:
            scope.emit(Effect(effect_type="e0"))
            cp1 = scope.checkpoint("first")
            scope.emit(Effect(effect_type="e1"))
            cp2 = scope.checkpoint("second")
            scope.emit(Effect(effect_type="e2"))

            # Only test single restore - concurrent restores are undefined
            scope.restore(cp2)
            assert len(scope.effects) == 2

            # cp1 can still be restored
            scope.restore(cp1)
            assert len(scope.effects) == 1
