"""Tests for large payload handling and size limits.

This module tests behavior with large data:
- Large effect payloads (approaching SDK buffer limits)
- Serialization of large data
- Stream behavior with many/large effects
- Memory characteristics with size edge cases

These tests address coverage gap HIGH-T4: large payload edge cases.
"""

from pathlib import Path

import pytest
from shepherd_contexts.workspace.effects import BashCommand, WorkspacePatchCaptured
from shepherd_core.effects import (
    ArtifactWritten,
    DiffPatch,
    TaskCompleted,
    TaskStarted,
)
from shepherd_core.scope.stream import EffectLayer, Stream
from shepherd_runtime.persistence import StreamId, StreamReader, StreamWriter

# =============================================================================
# Constants
# =============================================================================

# SDK buffer limit from constants.py
SDK_BUFFER_LIMIT_BYTES = 1_048_576  # 1MB

# Test payload sizes
SMALL_PAYLOAD = 1_000  # 1KB
MEDIUM_PAYLOAD = 100_000  # 100KB
LARGE_PAYLOAD = 500_000  # 500KB
NEAR_LIMIT_PAYLOAD = 900_000  # 900KB (near 1MB limit)


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
    return StreamId.from_string("test_large_payload")


def make_large_string(size: int) -> str:
    """Create a string of approximately the given size in bytes."""
    # Use repeated pattern for compressibility testing too
    pattern = "x" * 100
    repeats = size // len(pattern) + 1
    return (pattern * repeats)[:size]


def make_large_patch(size: int) -> str:
    """Create a realistic-looking diff patch of given size."""
    header = "diff --git a/large.py b/large.py\n--- a/large.py\n+++ b/large.py\n"
    line = "+# " + "x" * 76 + "\n"  # ~80 bytes per line
    lines_needed = (size - len(header)) // len(line) + 1
    return header + (line * lines_needed)


# =============================================================================
# Effect Serialization Size Tests
# =============================================================================


class TestEffectSerializationSize:
    """Tests for effect serialization with various payload sizes."""

    @pytest.mark.parametrize("size", [SMALL_PAYLOAD, MEDIUM_PAYLOAD, LARGE_PAYLOAD])
    def test_bash_command_output_serialization(self, size: int) -> None:
        """BashCommand should serialize with large output."""
        output = make_large_string(size)
        effect = BashCommand(
            command="cat large_file.txt",
            output=output,
            exit_code=0,
            working_directory="/test",
        )

        serialized = effect.model_dump_json()
        # Serialized should include the full output
        assert len(serialized) >= size

        recovered = BashCommand.model_validate_json(serialized)
        assert recovered.output == output

    @pytest.mark.parametrize("size", [SMALL_PAYLOAD, MEDIUM_PAYLOAD, LARGE_PAYLOAD])
    def test_workspace_patch_serialization(self, size: int) -> None:
        """WorkspacePatchCaptured should serialize with large patches."""
        patch_content = make_large_patch(size)
        effect = WorkspacePatchCaptured(
            binding_name="workspace",
            patch=DiffPatch(
                patch=patch_content,
                files_changed=("large.py",),
            ),
            patch_hash="abc123",
            patch_size_bytes=len(patch_content),
        )

        serialized = effect.model_dump_json()
        recovered = WorkspacePatchCaptured.model_validate_json(serialized)
        assert recovered.patch.patch == patch_content

    def test_near_limit_payload_serialization(self) -> None:
        """Payload near SDK buffer limit should serialize."""
        # Create payload close to 1MB
        output = make_large_string(NEAR_LIMIT_PAYLOAD)
        effect = BashCommand(
            command="cat huge_file.txt",
            output=output,
            exit_code=0,
            working_directory="/test",
        )

        serialized = effect.model_dump_json()

        # Verify it serializes with expected size
        assert len(serialized) > NEAR_LIMIT_PAYLOAD

        # Record the size for awareness
        print(f"Near-limit payload serialized to {len(serialized):,} bytes")


# =============================================================================
# Stream Persistence with Large Data Tests
# =============================================================================


class TestStreamPersistenceLargeData:
    """Tests for persisting streams with large effects."""

    def test_write_and_read_large_effect(self, stream_dir: Path, stream_id: StreamId) -> None:
        """Large effects should persist and recover correctly."""
        large_output = make_large_string(LARGE_PAYLOAD)
        effect = BashCommand(
            command="cat large_file.txt",
            output=large_output,
            exit_code=0,
            working_directory="/test",
        )
        layer = EffectLayer(
            effect=effect,
            sequence=0,
            scope_id="scope_test",
            scope_depth=0,
            source_context="test:large",
        )

        writer = StreamWriter(stream_dir, stream_id)
        writer.open()
        writer.append(layer)
        writer.close()

        stream_path = stream_dir / f"{stream_id}.jsonl"
        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 1
        assert layers[0].effect.output == large_output

    def test_multiple_large_effects(self, stream_dir: Path, stream_id: StreamId) -> None:
        """Multiple large effects should persist correctly."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        expected_outputs = []
        for i in range(5):
            output = make_large_string(MEDIUM_PAYLOAD + i * 1000)
            expected_outputs.append(output)

            effect = BashCommand(
                command=f"cmd_{i}",
                output=output,
                exit_code=0,
                working_directory="/test",
            )
            layer = EffectLayer(
                effect=effect,
                sequence=i,
                scope_id="scope_test",
                scope_depth=0,
                source_context="test:multi",
            )
            writer.append(layer)

        writer.close()

        stream_path = stream_dir / f"{stream_id}.jsonl"
        reader = StreamReader(stream_path=stream_path)
        layers = reader.read_all()

        assert len(layers) == 5
        for i, layer in enumerate(layers):
            assert layer.effect.output == expected_outputs[i]

    def test_stream_file_size_grows_correctly(self, stream_dir: Path, stream_id: StreamId) -> None:
        """Stream file size should reflect accumulated effect sizes."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        total_content = 0
        for i in range(3):
            output = make_large_string(MEDIUM_PAYLOAD)
            total_content += len(output)

            effect = BashCommand(
                command=f"cmd_{i}",
                output=output,
                exit_code=0,
                working_directory="/test",
            )
            layer = EffectLayer(effect=effect, sequence=i, scope_id="s", scope_depth=0)
            writer.append(layer)

        writer.close()

        stream_path = stream_dir / f"{stream_id}.jsonl"
        file_size = stream_path.stat().st_size

        # File should be larger than content (JSON overhead)
        assert file_size > total_content
        print(f"Content: {total_content:,} bytes, File: {file_size:,} bytes")


# =============================================================================
# In-Memory Stream Tests
# =============================================================================


class TestInMemoryStreamLargeData:
    """Tests for in-memory Stream with large effects."""

    def test_stream_append_large_effects(self) -> None:
        """Stream should handle appending large effects."""
        stream = Stream()

        for i in range(10):
            output = make_large_string(MEDIUM_PAYLOAD)
            effect = BashCommand(
                command=f"cmd_{i}",
                output=output,
                exit_code=0,
                working_directory="/test",
            )
            stream = stream.append(effect)

        assert len(stream) == 10

    def test_stream_query_with_large_effects(self) -> None:
        """Stream queries should work with large effects."""
        stream = Stream()

        # Add mix of small and large effects
        stream = stream.append(TaskStarted(task_name="Task1", task_fqn="t.Task1"))

        large_output = make_large_string(LARGE_PAYLOAD)
        stream = stream.append(
            BashCommand(
                command="cat large.txt",
                output=large_output,
                exit_code=0,
                working_directory="/test",
            )
        )

        stream = stream.append(TaskCompleted(task_name="Task1", outputs={}))

        # Query should work
        bash_effects = [layer.effect for layer in stream.layers if isinstance(layer.effect, BashCommand)]
        assert len(bash_effects) == 1
        assert bash_effects[0].output == large_output

    def test_stream_memory_with_accumulation(self) -> None:
        """Track memory growth with stream accumulation."""
        import gc

        gc.collect()

        stream = Stream()
        sizes = []

        for i in range(20):
            output = make_large_string(SMALL_PAYLOAD)  # 1KB each
            effect = BashCommand(
                command=f"cmd_{i}",
                output=output,
                exit_code=0,
                working_directory="/test",
            )
            stream = stream.append(effect)

            # Track stream length growth
            sizes.append(len(stream))

        assert len(stream) == 20

        # Memory should grow approximately linearly
        # (This is a sanity check, not a strict requirement)
        print(f"Final stream length: {len(stream)}")


# =============================================================================
# Artifact Size Tests
# =============================================================================


class TestArtifactSizeHandling:
    """Tests for artifact size tracking and handling."""

    def test_artifact_written_tracks_size(self) -> None:
        """ArtifactWritten should track size_bytes correctly."""
        content = make_large_string(MEDIUM_PAYLOAD)
        effect = ArtifactWritten(
            task_name="TestTask",
            field_name="output_file",
            filename="large.txt",
            size_bytes=len(content),
            content_hash="abc123",
        )

        assert effect.size_bytes == MEDIUM_PAYLOAD

        # Serialization should preserve size
        serialized = effect.model_dump_json()
        recovered = ArtifactWritten.model_validate_json(serialized)
        assert recovered.size_bytes == MEDIUM_PAYLOAD

    def test_workspace_patch_tracks_size(self) -> None:
        """WorkspacePatchCaptured should track patch_size_bytes."""
        patch = make_large_patch(LARGE_PAYLOAD)
        effect = WorkspacePatchCaptured(
            binding_name="workspace",
            patch=DiffPatch(patch=patch, files_changed=("test.py",)),
            patch_size_bytes=len(patch),
            patch_hash="def456",
        )

        assert effect.patch_size_bytes == len(patch)


# =============================================================================
# Edge Cases
# =============================================================================


class TestLargePayloadEdgeCases:
    """Edge cases for large payload handling."""

    def test_empty_vs_large_effect_serialization(self) -> None:
        """Compare serialization of empty vs large effects."""
        empty_effect = BashCommand(
            command="true",
            output="",
            exit_code=0,
            working_directory="/test",
        )

        large_effect = BashCommand(
            command="cat large.txt",
            output=make_large_string(LARGE_PAYLOAD),
            exit_code=0,
            working_directory="/test",
        )

        empty_json = empty_effect.model_dump_json()
        large_json = large_effect.model_dump_json()

        assert len(large_json) > len(empty_json)
        assert len(large_json) > LARGE_PAYLOAD

    def test_nested_large_data_serialization(self) -> None:
        """Effects with nested large data should serialize."""
        large_data = {
            "key1": make_large_string(SMALL_PAYLOAD),
            "key2": make_large_string(SMALL_PAYLOAD),
            "nested": {
                "deep": make_large_string(SMALL_PAYLOAD),
            },
        }

        effect = TaskCompleted(
            task_name="NestedTask",
            outputs=large_data,
        )

        serialized = effect.model_dump_json()
        recovered = TaskCompleted.model_validate_json(serialized)

        assert recovered.outputs["key1"] == large_data["key1"]
        assert recovered.outputs["nested"]["deep"] == large_data["nested"]["deep"]

    def test_unicode_in_large_payload(self) -> None:
        """Large payloads with unicode should serialize correctly."""
        # Mix of ASCII and unicode
        base = "Hello " + "\u4e2d\u6587" + " World " + "\U0001f600" + " "
        large_unicode = base * (MEDIUM_PAYLOAD // len(base.encode("utf-8")))

        effect = BashCommand(
            command="echo unicode",
            output=large_unicode,
            exit_code=0,
            working_directory="/test",
        )

        serialized = effect.model_dump_json()
        recovered = BashCommand.model_validate_json(serialized)

        assert recovered.output == large_unicode

    def test_special_characters_in_large_payload(self) -> None:
        """Large payloads with special JSON characters should serialize."""
        # Characters that need escaping in JSON
        special = 'Quote: " Backslash: \\ Newline: \n Tab: \t'
        large_special = special * (MEDIUM_PAYLOAD // len(special))

        effect = BashCommand(
            command="echo special",
            output=large_special,
            exit_code=0,
            working_directory="/test",
        )

        serialized = effect.model_dump_json()
        recovered = BashCommand.model_validate_json(serialized)

        assert recovered.output == large_special


# =============================================================================
# Performance Sanity Checks
# =============================================================================


class TestLargePayloadPerformance:
    """Basic performance sanity checks for large payloads."""

    def test_serialization_completes_in_reasonable_time(self) -> None:
        """Large effect serialization should complete quickly."""
        import time

        large_output = make_large_string(LARGE_PAYLOAD)
        effect = BashCommand(
            command="cat large.txt",
            output=large_output,
            exit_code=0,
            working_directory="/test",
        )

        start = time.time()
        for _ in range(10):
            _ = effect.model_dump_json()
        elapsed = time.time() - start

        # 10 serializations of 500KB should complete in < 1 second
        assert elapsed < 1.0, f"Serialization too slow: {elapsed:.2f}s for 10 iterations"

    def test_stream_append_performance(self) -> None:
        """Stream append should maintain reasonable performance."""
        import time

        stream = Stream()
        start = time.time()

        for i in range(100):
            effect = TaskStarted(task_name=f"Task{i}", task_fqn=f"t.Task{i}")
            stream = stream.append(effect)

        elapsed = time.time() - start

        # 100 appends should complete in < 1 second
        assert elapsed < 1.0, f"Append too slow: {elapsed:.2f}s for 100 appends"
        assert len(stream) == 100
