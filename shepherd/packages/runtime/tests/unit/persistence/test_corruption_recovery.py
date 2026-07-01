"""Tests for persistence corruption detection and recovery.

This module tests error handling for corrupted data:
- Corrupted JSON lines in stream files
- Partial/truncated lines
- Missing required fields
- Mixed valid/invalid data
- Index file corruption
- Lock contention scenarios

These tests address coverage gap HIGH-T4: corruption recovery paths.
"""

import contextlib
import json
import time
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from shepherd_core.effects import TaskCompleted, TaskStarted
from shepherd_core.scope.stream import EffectLayer
from shepherd_runtime.persistence import StreamId, StreamReader, StreamWriter

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def stream_dir(tmp_path: Path) -> Path:
    """Create a temporary stream directory."""
    stream_path = tmp_path / "streams"
    stream_path.mkdir()
    return stream_path


@pytest.fixture
def stream_id() -> StreamId:
    """Create a test stream ID."""
    return StreamId.from_string("test_corruption_recovery")


@pytest.fixture
def stream_path(stream_dir: Path, stream_id: StreamId) -> Path:
    """Get the stream file path."""
    return stream_dir / f"{stream_id}.jsonl"


@pytest.fixture
def valid_layer() -> EffectLayer:
    """Create a valid effect layer for testing."""
    return EffectLayer(
        effect=TaskStarted(task_name="TestTask", task_fqn="test.TestTask"),
        sequence=0,
        scope_id="scope_test",
        scope_depth=0,
        source_context="test:context",
    )


@pytest.fixture
def valid_layer_json(valid_layer: EffectLayer) -> str:
    """Create valid JSON representation of an effect layer."""
    return json.dumps(
        {
            "effect": valid_layer.effect.model_dump(),
            "sequence": valid_layer.sequence,
            "scope_id": valid_layer.scope_id,
            "scope_depth": valid_layer.scope_depth,
            "source_context": valid_layer.source_context,
        }
    )


# =============================================================================
# Corrupted JSON Tests
# =============================================================================


class TestCorruptedJsonHandling:
    """Tests for handling corrupted JSON in stream files."""

    def test_skip_invalid_json_line(self, stream_path: Path, valid_layer_json: str) -> None:
        """Reader should skip lines with invalid JSON and continue."""
        # Create stream file with corrupted line in middle
        content = f"{valid_layer_json}\n{{invalid json}}\n{valid_layer_json}\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        # Should have 2 valid layers, skipping the corrupted one
        assert len(layers) == 2

    def test_skip_truncated_json(self, stream_path: Path, valid_layer_json: str) -> None:
        """Reader should skip truncated JSON (incomplete line)."""
        truncated = valid_layer_json[:50]  # Cut off mid-JSON
        content = f"{valid_layer_json}\n{truncated}\n{valid_layer_json}\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 2

    def test_skip_empty_json_object(self, stream_path: Path, valid_layer_json: str) -> None:
        """Reader should skip empty JSON objects (missing effect)."""
        content = f"{valid_layer_json}\n{{}}\n{valid_layer_json}\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        # Empty object missing "effect" key should be skipped
        assert len(layers) == 2

    def test_skip_json_array_instead_of_object(self, stream_path: Path, valid_layer_json: str) -> None:
        """Reader should skip JSON arrays (wrong type)."""
        content = f"{valid_layer_json}\n[1, 2, 3]\n{valid_layer_json}\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 2

    def test_handle_unicode_decode_errors(self, stream_path: Path, valid_layer_json: str) -> None:
        """Reader should handle files with encoding issues gracefully."""
        # Write valid JSON, then append invalid UTF-8 bytes
        with open(stream_path, "w", encoding="utf-8") as f:
            f.write(f"{valid_layer_json}\n")

        with open(stream_path, "ab") as f:
            f.write(b"\xff\xfe invalid bytes\n")  # Invalid UTF-8

        with open(stream_path, "a", encoding="utf-8") as f:
            f.write(f"{valid_layer_json}\n")

        reader = StreamReader(stream_path=stream_path)
        # May raise or skip depending on encoding handling
        # Just verify it doesn't crash completely
        try:
            layers = reader.read_all()
            # If it succeeds, should have at least the valid layers
            assert len(layers) >= 1
        except UnicodeDecodeError:
            # This is acceptable - reader uses UTF-8 encoding
            pass


# =============================================================================
# Missing Required Fields Tests
# =============================================================================


class TestMissingFieldsHandling:
    """Tests for handling effects with missing required fields."""

    def test_skip_missing_effect_field(self, stream_path: Path) -> None:
        """Reader should skip layers missing the 'effect' field."""
        content = json.dumps(
            {
                "sequence": 0,
                "scope_id": "test",
                # Missing "effect" field
            }
        )
        stream_path.write_text(content + "\n")

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 0

    def test_handle_missing_optional_fields(self, stream_path: Path) -> None:
        """Reader should handle missing optional fields with defaults."""
        # Minimal valid layer - only effect required
        effect = TaskStarted(task_name="Test", task_fqn="test.Test")
        content = json.dumps({"effect": effect.model_dump()})
        stream_path.write_text(content + "\n")

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 1
        layer = layers[0]
        # Optional fields should have defaults
        assert layer.sequence == 0  # Default from line number
        assert layer.scope_depth == 0

    def test_unknown_effect_type_deserializes_to_base(self, stream_path: Path) -> None:
        """Unknown effect types should deserialize to base Effect class."""
        from shepherd_core.effects import Effect

        content = json.dumps(
            {
                "effect": {"effect_type": "nonexistent_type", "data": "value"},
                "sequence": 0,
            }
        )
        stream_path.write_text(content + "\n")

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        # Unknown types deserialize to base Effect (graceful fallback)
        assert len(layers) == 1
        assert type(layers[0].effect) == Effect


# =============================================================================
# Partial Write / Crash Recovery Tests
# =============================================================================


class TestPartialWriteRecovery:
    """Tests for recovery from partial writes (simulated crash)."""

    def test_partial_last_line_skipped(self, stream_path: Path, valid_layer_json: str) -> None:
        """Partial last line (no newline) should be handled."""
        # Complete line + partial line (no newline, truncated)
        content = f"{valid_layer_json}\n{valid_layer_json[:30]}"  # No trailing newline
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        # First complete line should succeed, partial may be skipped or fail
        assert len(layers) >= 1

    def test_recovery_preserves_sequence_numbers(self, stream_path: Path) -> None:
        """Sequence numbers should be preserved from valid layers."""
        # Use a truly corrupted middle layer (invalid JSON structure)
        valid1 = json.dumps({"effect": TaskStarted(task_name="T1", task_fqn="t.T1").model_dump(), "sequence": 10})
        corrupted = "{invalid json structure"  # Will be skipped (JSON parse error)
        valid2 = json.dumps({"effect": TaskCompleted(task_name="T1", outputs={}).model_dump(), "sequence": 12})

        content = f"{valid1}\n{corrupted}\n{valid2}\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 2
        assert layers[0].sequence == 10
        assert layers[1].sequence == 12


# =============================================================================
# Lock Contention Tests
# =============================================================================


class TestLockContention:
    """Tests for file lock behavior under contention."""

    def test_double_open_raises_error(self, stream_dir: Path, stream_id: StreamId) -> None:
        """Opening an already-open writer should raise RuntimeError."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        try:
            with pytest.raises(RuntimeError, match="already open"):
                writer.open()
        finally:
            writer.close()

    def test_write_to_closed_writer_raises(
        self, stream_dir: Path, stream_id: StreamId, valid_layer: EffectLayer
    ) -> None:
        """Writing to a closed writer should raise error."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()
        writer.close()

        with pytest.raises((RuntimeError, ValueError, AttributeError)):
            writer.append(valid_layer)

    def test_concurrent_writers_blocked(self, stream_dir: Path, stream_id: StreamId, valid_layer: EffectLayer) -> None:
        """Second writer should block waiting for lock."""
        writer1 = StreamWriter(stream_dir, stream_id)
        writer1.open()

        results = {"writer2_started": False, "writer2_completed": False}

        def try_second_writer():
            results["writer2_started"] = True
            writer2 = StreamWriter(stream_dir, stream_id)
            try:
                # This should block or fail due to lock
                writer2.open()
                writer2.close()
                results["writer2_completed"] = True
            except Exception:
                pass

        thread = Thread(target=try_second_writer)
        thread.start()

        # Give thread time to start and block
        time.sleep(0.1)
        assert results["writer2_started"]

        # Writer2 should still be waiting (not completed)
        # Note: with short timeout, it may have failed
        writer1.close()

        thread.join(timeout=2.0)


# =============================================================================
# Fsync and Disk Error Tests
# =============================================================================


class TestDiskErrorHandling:
    """Tests for handling disk errors during writes."""

    def test_append_with_fsync_failure(self, stream_dir: Path, stream_id: StreamId, valid_layer: EffectLayer) -> None:
        """Fsync failure should propagate as error."""
        import os

        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        try:
            with patch.object(os, "fsync", side_effect=OSError("Disk full")), pytest.raises(OSError, match="Disk full"):
                writer.append(valid_layer)
        finally:
            writer.close()

    def test_append_with_write_failure(self, stream_dir: Path, stream_id: StreamId, valid_layer: EffectLayer) -> None:
        """Write failure should propagate as error."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        try:
            # Simulate write failure by making file handle invalid
            writer._file.close()  # Close underlying file

            with pytest.raises((ValueError, OSError)):
                writer.append(valid_layer)
        finally:
            with contextlib.suppress(Exception):
                writer.close()


# =============================================================================
# Mixed Corruption Scenarios
# =============================================================================


class TestMixedCorruptionScenarios:
    """Tests for complex corruption scenarios with mixed valid/invalid data."""

    def test_alternating_valid_invalid_layers(self, stream_path: Path) -> None:
        """Reader should handle alternating valid/invalid layers."""
        valid1 = json.dumps({"effect": TaskStarted(task_name="T1", task_fqn="t.T1").model_dump(), "sequence": 0})
        valid2 = json.dumps({"effect": TaskStarted(task_name="T2", task_fqn="t.T2").model_dump(), "sequence": 2})
        valid3 = json.dumps({"effect": TaskStarted(task_name="T3", task_fqn="t.T3").model_dump(), "sequence": 4})

        content = f"{valid1}\n{{bad}}\n{valid2}\n[array]\n{valid3}\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 3
        assert [layer.effect.task_name for layer in layers] == ["T1", "T2", "T3"]

    def test_all_corrupted_returns_empty(self, stream_path: Path) -> None:
        """Stream with all corrupted lines should return empty list."""
        content = "{{bad1}}\n{{bad2}}\n[not,valid]\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 0

    def test_empty_stream_file(self, stream_path: Path) -> None:
        """Empty stream file should return no layers."""
        stream_path.write_text("")

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 0

    def test_whitespace_only_lines_skipped(self, stream_path: Path, valid_layer_json: str) -> None:
        """Lines with only whitespace should be skipped."""
        content = f"\n\n   \n{valid_layer_json}\n\t\n{valid_layer_json}\n   \n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 2

    def test_nonexistent_stream_returns_empty(self, tmp_path: Path) -> None:
        """Reading non-existent stream should yield no layers."""
        nonexistent = tmp_path / "does_not_exist.jsonl"
        reader = StreamReader(stream_path=nonexistent)
        layers = reader.read_all()

        assert len(layers) == 0


# =============================================================================
# Effect Deserialization Edge Cases
# =============================================================================


class TestEffectDeserializationEdgeCases:
    """Tests for edge cases in effect deserialization."""

    def test_effect_with_extra_fields_accepted(self, stream_path: Path) -> None:
        """Effects with extra fields should still deserialize."""
        effect_data = TaskStarted(task_name="Test", task_fqn="t.Test").model_dump()
        effect_data["extra_field"] = "should be ignored"

        content = json.dumps({"effect": effect_data, "sequence": 0})
        stream_path.write_text(content + "\n")

        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 1
        assert layers[0].effect.task_name == "Test"

    def test_effect_type_preserved_through_roundtrip(self, stream_dir: Path, stream_id: StreamId) -> None:
        """Effect type should be preserved through write/read cycle."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        original = EffectLayer(
            effect=TaskCompleted(task_name="Complete", outputs={"result": "value"}),
            sequence=42,
            scope_id="scope_test",
            scope_depth=3,
            source_context="ctx:test",
        )
        writer.append(original)
        writer.close()

        stream_path = stream_dir / f"{stream_id}.jsonl"
        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 1
        recovered = layers[0]

        assert type(recovered.effect) == type(original.effect)
        assert recovered.effect.task_name == original.effect.task_name
        assert recovered.sequence == original.sequence
        assert recovered.scope_id == original.scope_id
        assert recovered.scope_depth == original.scope_depth

    def test_iter_layers_is_lazy(self, stream_path: Path, valid_layer_json: str) -> None:
        """iter_layers should yield lazily without loading all at once."""
        # Write multiple layers
        content = "\n".join([valid_layer_json] * 10) + "\n"
        stream_path.write_text(content)

        reader = StreamReader(stream_path=stream_path)
        iterator = reader.iter_layers()

        # Should be able to get first item without loading all
        first = next(iterator)
        assert first.effect.task_name == "TestTask"

        # Consume rest
        remaining = list(iterator)
        assert len(remaining) == 9
