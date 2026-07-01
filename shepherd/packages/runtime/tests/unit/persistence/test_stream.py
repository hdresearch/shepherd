"""Unit tests for stream types.

Tests:
- StreamId generation and uniqueness
- StreamMetadata serialization
- StreamIndex operations
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from shepherd_runtime.persistence import StreamId, StreamIndex, StreamMetadata

# =============================================================================
# Tests: StreamId
# =============================================================================


class TestStreamId:
    """Tests for StreamId class."""

    def test_generate_creates_unique_ids(self) -> None:
        """generate() should create unique IDs."""
        ids = [StreamId.generate() for _ in range(100)]
        unique_values = {str(sid) for sid in ids}

        assert len(unique_values) == 100

    def test_generate_creates_valid_hex(self) -> None:
        """Generated IDs should be valid hex strings."""
        stream_id = StreamId.generate()

        assert len(stream_id.value) == 32  # UUID hex without dashes
        assert stream_id.value.isalnum()
        # Should be valid hex
        int(stream_id.value, 16)

    def test_from_string_creates_id(self) -> None:
        """from_string should create StreamId from string."""
        stream_id = StreamId.from_string("abc123def456")

        assert stream_id.value == "abc123def456"
        assert str(stream_id) == "abc123def456"

    def test_str_returns_value(self) -> None:
        """str() should return the value."""
        stream_id = StreamId.generate()

        assert str(stream_id) == stream_id.value

    def test_repr_shows_truncated_value(self) -> None:
        """Repr should show truncated value for readability."""
        stream_id = StreamId.generate()
        repr_str = repr(stream_id)

        assert "StreamId(" in repr_str
        assert stream_id.value[:8] in repr_str


# =============================================================================
# Tests: StreamMetadata
# =============================================================================


class TestStreamMetadata:
    """Tests for StreamMetadata class."""

    def test_create_with_defaults(self) -> None:
        """Creating metadata should set defaults."""
        before = datetime.now(timezone.utc)
        metadata = StreamMetadata(stream_id="test123")
        after = datetime.now(timezone.utc)

        assert metadata.stream_id == "test123"
        assert before <= metadata.created_at <= after
        assert metadata.closed_at is None
        assert metadata.continues_from is None
        assert metadata.effect_count == 0

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        """Serialization should roundtrip correctly."""
        original = StreamMetadata(
            stream_id="test123",
            continues_from="prev456",
            effect_count=42,
        )

        data = original.to_dict()
        restored = StreamMetadata.from_dict(data)

        assert restored.stream_id == original.stream_id
        assert restored.created_at == original.created_at
        assert restored.continues_from == original.continues_from
        assert restored.effect_count == original.effect_count

    def test_with_closed(self) -> None:
        """with_closed should set closed_at and effect_count."""
        original = StreamMetadata(stream_id="test123")

        closed = original.with_closed(effect_count=100)

        assert closed.stream_id == original.stream_id
        assert closed.created_at == original.created_at
        assert closed.closed_at is not None
        assert closed.effect_count == 100

    def test_to_dict_with_closed(self) -> None:
        """to_dict should include closed_at when set."""
        metadata = StreamMetadata(stream_id="test123").with_closed(50)
        data = metadata.to_dict()

        assert data["closed_at"] is not None
        assert "T" in data["closed_at"]  # ISO format

    def test_from_dict_with_null_closed(self) -> None:
        """from_dict should handle null closed_at."""
        data = {
            "stream_id": "test123",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None,
            "continues_from": None,
            "effect_count": 0,
        }

        metadata = StreamMetadata.from_dict(data)

        assert metadata.closed_at is None


# =============================================================================
# Tests: StreamIndex
# =============================================================================


class TestStreamIndex:
    """Tests for StreamIndex class."""

    def test_create_empty_index(self) -> None:
        """Empty index should have no streams."""
        index = StreamIndex()

        assert len(index.streams) == 0
        assert index.current_stream_id is None

    def test_add_stream(self) -> None:
        """add_stream should add metadata to index."""
        index = StreamIndex()
        metadata = StreamMetadata(stream_id="test123")

        index.add_stream(metadata)

        assert len(index.streams) == 1
        assert index.streams[0].stream_id == "test123"

    def test_get_stream(self) -> None:
        """get_stream should find stream by ID."""
        index = StreamIndex()
        metadata = StreamMetadata(stream_id="test123")
        index.add_stream(metadata)

        found = index.get_stream("test123")

        assert found is not None
        assert found.stream_id == "test123"

    def test_get_stream_not_found(self) -> None:
        """get_stream should return None for unknown ID."""
        index = StreamIndex()

        found = index.get_stream("nonexistent")

        assert found is None

    def test_update_stream(self) -> None:
        """update_stream should replace existing metadata."""
        index = StreamIndex()
        original = StreamMetadata(stream_id="test123", effect_count=0)
        index.add_stream(original)

        updated = StreamMetadata(stream_id="test123", effect_count=50)
        index.update_stream(updated)

        found = index.get_stream("test123")
        assert found is not None
        assert found.effect_count == 50

    def test_update_stream_not_found_raises(self) -> None:
        """update_stream should raise for unknown ID."""
        index = StreamIndex()
        metadata = StreamMetadata(stream_id="nonexistent")

        with pytest.raises(ValueError, match="not found"):
            index.update_stream(metadata)

    def test_get_latest_stream(self) -> None:
        """get_latest_stream should return most recent stream."""
        index = StreamIndex()

        # Add streams with different creation times
        import time

        stream1 = StreamMetadata(stream_id="older")
        index.add_stream(stream1)
        time.sleep(0.01)
        stream2 = StreamMetadata(stream_id="newer")
        index.add_stream(stream2)

        latest = index.get_latest_stream()

        assert latest is not None
        assert latest.stream_id == "newer"

    def test_get_latest_stream_empty_index(self) -> None:
        """get_latest_stream should return None for empty index."""
        index = StreamIndex()

        latest = index.get_latest_stream()

        assert latest is None

    def test_get_current_stream(self) -> None:
        """get_current_stream should return stream by current_stream_id."""
        index = StreamIndex()
        metadata = StreamMetadata(stream_id="test123")
        index.add_stream(metadata)
        index.current_stream_id = "test123"

        current = index.get_current_stream()

        assert current is not None
        assert current.stream_id == "test123"

    def test_get_current_stream_no_current(self) -> None:
        """get_current_stream should return None if no current stream."""
        index = StreamIndex()
        metadata = StreamMetadata(stream_id="test123")
        index.add_stream(metadata)

        current = index.get_current_stream()

        assert current is None

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        """Serialization should roundtrip correctly."""
        index = StreamIndex()
        index.add_stream(StreamMetadata(stream_id="stream1"))
        index.add_stream(StreamMetadata(stream_id="stream2"))
        index.current_stream_id = "stream2"

        data = index.to_dict()
        restored = StreamIndex.from_dict(data)

        assert len(restored.streams) == 2
        assert restored.current_stream_id == "stream2"
        assert restored.get_stream("stream1") is not None
        assert restored.get_stream("stream2") is not None

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Index should persist to disk correctly."""
        path = tmp_path / "index.json"

        # Create and save
        index = StreamIndex()
        index.add_stream(StreamMetadata(stream_id="test123"))
        index.current_stream_id = "test123"
        index.save(path)

        # Load
        loaded = StreamIndex.load(path)

        assert len(loaded.streams) == 1
        assert loaded.current_stream_id == "test123"

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """Loading nonexistent file should return empty index."""
        path = tmp_path / "nonexistent.json"

        index = StreamIndex.load(path)

        assert len(index.streams) == 0
        assert index.current_stream_id is None
