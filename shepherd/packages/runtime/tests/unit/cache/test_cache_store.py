"""Tests for CacheStore operations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from shepherd_runtime.cache import (
    CachedOutputs,
    CacheEntry,
    CacheIndex,
    CacheStats,
    CacheStore,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestCachedOutputs:
    """Test CachedOutputs serialization."""

    def test_to_dict_round_trip(self):
        """CachedOutputs should serialize and deserialize correctly."""
        outputs = CachedOutputs(
            outputs={"result": "hello world", "count": 42},
            task_name="TestTask",
            execution_key="abc123",
        )

        data = outputs.to_dict()
        restored = CachedOutputs.from_dict(data)

        assert restored.outputs == outputs.outputs
        assert restored.task_name == outputs.task_name
        assert restored.execution_key == outputs.execution_key

    def test_created_at_set_automatically(self):
        """CachedOutputs should have created_at set automatically."""
        outputs = CachedOutputs(outputs={"result": "test"})

        assert outputs.created_at != ""
        # Should be valid ISO format
        datetime.fromisoformat(outputs.created_at)


class TestCacheEntry:
    """Test CacheEntry metadata."""

    def test_to_dict_round_trip(self):
        """CacheEntry should serialize and deserialize correctly."""
        entry = CacheEntry(
            execution_key="abc123",
            task_name="TestTask",
            access_count=5,
            size_bytes=1024,
            mode="outputs_only",
        )

        data = entry.to_dict()
        restored = CacheEntry.from_dict(data)

        assert restored.execution_key == entry.execution_key
        assert restored.task_name == entry.task_name
        assert restored.access_count == entry.access_count
        assert restored.size_bytes == entry.size_bytes


class TestCacheIndex:
    """Test CacheIndex operations."""

    def test_add_entry(self):
        """Adding entries should update the index."""
        index = CacheIndex()
        entry = CacheEntry(execution_key="key1", task_name="Task", size_bytes=100)

        index.add_entry(entry)

        assert "key1" in index.entries
        assert index.total_size_bytes == 100

    def test_add_entry_replaces_existing(self):
        """Adding entry with same key should replace and update size."""
        index = CacheIndex()
        entry1 = CacheEntry(execution_key="key1", task_name="Task", size_bytes=100)
        entry2 = CacheEntry(execution_key="key1", task_name="Task", size_bytes=200)

        index.add_entry(entry1)
        index.add_entry(entry2)

        assert len(index.entries) == 1
        assert index.total_size_bytes == 200

    def test_remove_entry(self):
        """Removing entry should update size."""
        index = CacheIndex()
        entry = CacheEntry(execution_key="key1", task_name="Task", size_bytes=100)

        index.add_entry(entry)
        index.remove_entry("key1")

        assert "key1" not in index.entries
        assert index.total_size_bytes == 0

    def test_save_and_load(self, tmp_path: Path):
        """Index should persist to disk and load correctly."""
        index = CacheIndex()
        entry = CacheEntry(execution_key="key1", task_name="Task", size_bytes=100)
        index.add_entry(entry)

        # Save
        index_path = tmp_path / "index.json"
        index.save(index_path)

        # Load
        loaded = CacheIndex.load(index_path)

        assert "key1" in loaded.entries
        assert loaded.total_size_bytes == 100

    def test_load_nonexistent_returns_empty(self, tmp_path: Path):
        """Loading from nonexistent file should return empty index."""
        index_path = tmp_path / "nonexistent.json"
        loaded = CacheIndex.load(index_path)

        assert len(loaded.entries) == 0


class TestCacheStore:
    """Test CacheStore operations."""

    @pytest.fixture
    def cache_store(self, tmp_path: Path) -> CacheStore:
        """Create a cache store for testing."""
        store = CacheStore(tmp_path / "cache")
        store.initialize()
        return store

    def test_initialize_creates_directories(self, tmp_path: Path):
        """Initialize should create cache directory structure."""
        cache_dir = tmp_path / "cache"
        store = CacheStore(cache_dir)
        store.initialize()

        assert cache_dir.exists()
        assert store.entries_dir.exists()

    def test_put_and_get(self, cache_store: CacheStore):
        """Basic put and get should work."""
        cached = CachedOutputs(
            outputs={"result": "hello"},
            task_name="TestTask",
        )

        cache_store.put("key123", cached)
        retrieved = cache_store.get("key123")

        assert retrieved is not None
        assert retrieved.outputs == {"result": "hello"}
        assert retrieved.task_name == "TestTask"

    def test_get_nonexistent_returns_none(self, cache_store: CacheStore):
        """Getting nonexistent key should return None."""
        result = cache_store.get("nonexistent")
        assert result is None

    def test_get_updates_access_count(self, cache_store: CacheStore):
        """Getting an entry should update access count."""
        cached = CachedOutputs(outputs={"result": "hello"}, task_name="Test")
        cache_store.put("key1", cached)

        # Access multiple times
        cache_store.get("key1")
        cache_store.get("key1")
        cache_store.get("key1")

        # Check access count in index
        entry = cache_store._index.get_entry("key1")
        assert entry is not None
        assert entry.access_count == 3

    def test_invalidate_by_key(self, cache_store: CacheStore):
        """Invalidate by key should remove specific entry."""
        cached1 = CachedOutputs(outputs={"r": 1}, task_name="Task1")
        cached2 = CachedOutputs(outputs={"r": 2}, task_name="Task2")

        cache_store.put("key1", cached1)
        cache_store.put("key2", cached2)

        removed = cache_store.invalidate(execution_key="key1")

        assert removed == 1
        assert cache_store.get("key1") is None
        assert cache_store.get("key2") is not None

    def test_invalidate_by_task(self, cache_store: CacheStore):
        """Invalidate by task should remove all entries for that task."""

        class TaskA:
            __name__ = "TaskA"

        class TaskB:
            __name__ = "TaskB"

        cached1 = CachedOutputs(outputs={"r": 1}, task_name="TaskA")
        cached2 = CachedOutputs(outputs={"r": 2}, task_name="TaskA")
        cached3 = CachedOutputs(outputs={"r": 3}, task_name="TaskB")

        cache_store.put("key1", cached1)
        cache_store.put("key2", cached2)
        cache_store.put("key3", cached3)

        removed = cache_store.invalidate(task=TaskA)

        assert removed == 2
        assert cache_store.get("key1") is None
        assert cache_store.get("key2") is None
        assert cache_store.get("key3") is not None

    def test_invalidate_all(self, cache_store: CacheStore):
        """Invalidate with no args should remove all entries."""
        cached1 = CachedOutputs(outputs={"r": 1}, task_name="Task1")
        cached2 = CachedOutputs(outputs={"r": 2}, task_name="Task2")

        cache_store.put("key1", cached1)
        cache_store.put("key2", cached2)

        removed = cache_store.invalidate()

        assert removed == 2
        assert cache_store.get("key1") is None
        assert cache_store.get("key2") is None

    def test_cleanup_expired(self, cache_store: CacheStore):
        """Cleanup should remove entries older than TTL."""
        # Create an old entry by manipulating the index directly
        cached = CachedOutputs(outputs={"r": 1}, task_name="Task")
        cache_store.put("key1", cached)

        # Manually set created_at to old date
        entry = cache_store._index.get_entry("key1")
        old_date = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        entry_with_old_date = CacheEntry(
            execution_key=entry.execution_key,
            task_name=entry.task_name,
            created_at=old_date,
            size_bytes=entry.size_bytes,
        )
        cache_store._index.entries["key1"] = entry_with_old_date
        cache_store._save_index()

        # Cleanup entries older than 24 hours
        removed = cache_store.cleanup_expired(ttl_hours=24)

        assert removed == 1
        assert cache_store.get("key1") is None

    def test_stats(self, cache_store: CacheStore):
        """Stats should return correct values."""
        cached1 = CachedOutputs(outputs={"r": 1}, task_name="Task1")
        cached2 = CachedOutputs(outputs={"r": 2}, task_name="Task2")

        cache_store.put("key1", cached1)
        cache_store.put("key2", cached2)

        # Cause hit and miss
        cache_store.get("key1")  # hit
        cache_store.get("nonexistent")  # miss

        stats = cache_store.stats()

        assert stats.entry_count == 2
        assert stats.hit_count == 1
        assert stats.miss_count == 1
        assert stats.hit_rate == 0.5


class TestCacheStats:
    """Test CacheStats calculations."""

    def test_hit_rate_zero_when_empty(self):
        """Hit rate should be 0 when no accesses."""
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        """Hit rate should be calculated correctly."""
        stats = CacheStats(hit_count=3, miss_count=1)
        assert stats.hit_rate == 0.75

    def test_hit_rate_perfect(self):
        """Hit rate should be 1.0 when all hits."""
        stats = CacheStats(hit_count=10, miss_count=0)
        assert stats.hit_rate == 1.0


class TestCacheStorePersistence:
    """Test that cache persists across store instances."""

    def test_data_persists_across_instances(self, tmp_path: Path):
        """Data should persist when store is recreated."""
        cache_dir = tmp_path / "cache"

        # First instance - write data
        store1 = CacheStore(cache_dir)
        store1.initialize()
        cached = CachedOutputs(outputs={"result": "persisted"}, task_name="Test")
        store1.put("persist_key", cached)

        # Second instance - read data
        store2 = CacheStore(cache_dir)
        store2.initialize()
        retrieved = store2.get("persist_key")

        assert retrieved is not None
        assert retrieved.outputs == {"result": "persisted"}

    def test_corrupted_entry_handled_gracefully(self, tmp_path: Path):
        """Corrupted entry file should not crash get()."""
        cache_dir = tmp_path / "cache"

        store = CacheStore(cache_dir)
        store.initialize()

        # Write valid entry
        cached = CachedOutputs(outputs={"r": 1}, task_name="Test")
        store.put("key1", cached)

        # Corrupt the entry file
        entry_path = store.entries_dir / "key1.json"
        entry_path.write_text("not valid json {{{")

        # Should return None, not crash
        result = store.get("key1")
        assert result is None
