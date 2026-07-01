"""Unit tests for stream writer and reader.

Tests:
- StreamWriter append and crash safety
- StreamReader read and corrupted line handling
- Layer serialization roundtrip
"""

import json
from pathlib import Path
from typing import Literal

import pytest
from shepherd_contexts.kvstore.effects import KeySet
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import (
    KERNEL_EFFECT_REGISTRY,
    DiffPatch,
    Effect,
    TaskCompleted,
    TaskStarted,
)
from shepherd_core.scope.stream import EffectLayer
from shepherd_runtime.effects import compose_effect_registry
from shepherd_runtime.persistence import (
    StreamId,
    StreamReader,
    StreamWriter,
    layer_from_dict,
    layer_to_dict,
)

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
    return StreamId.from_string("test_stream_123")


@pytest.fixture
def sample_layer() -> EffectLayer:
    """Create a sample effect layer for testing."""
    effect = TaskStarted(
        task_name="TestTask",
        task_fqn="test.module.TestTask",
    )
    return EffectLayer(
        effect=effect,
        sequence=0,
        scope_id="scope_abc123",
        scope_depth=0,
        source_context="workspace:test",
    )


@pytest.fixture
def complex_layer() -> EffectLayer:
    """Create a complex effect layer with nested data."""
    effect = WorkspacePatchCaptured(
        binding_name="workspace",
        patch=DiffPatch(
            patch="diff --git a/test.py b/test.py\n+new line",
            files_changed=("test.py",),
        ),
    )
    return EffectLayer(
        effect=effect,
        sequence=5,
        scope_id="scope_xyz789",
        scope_depth=2,
        source_context="workspace:main",
    )


# =============================================================================
# Tests: StreamWriter
# =============================================================================


class TestStreamWriter:
    """Tests for StreamWriter class."""

    def test_open_creates_stream_file(self, stream_dir: Path, stream_id: StreamId) -> None:
        """open() should create the stream file."""
        writer = StreamWriter(stream_dir, stream_id)

        writer.open()
        try:
            assert writer.stream_path.exists()
        finally:
            writer.close()

    def test_open_creates_directory_if_needed(self, tmp_path: Path, stream_id: StreamId) -> None:
        """open() should create stream directory if it doesn't exist."""
        stream_dir = tmp_path / "nested" / "streams"
        writer = StreamWriter(stream_dir, stream_id)

        writer.open()
        try:
            assert stream_dir.exists()
            assert writer.stream_path.exists()
        finally:
            writer.close()

    def test_append_writes_layer(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """append() should write layer to file."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        index = writer.append(sample_layer)
        writer.close()

        assert index == 0
        content = writer.stream_path.read_text()
        assert "task_started" in content
        assert "TestTask" in content

    def test_append_returns_incrementing_index(
        self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer
    ) -> None:
        """append() should return incrementing indices."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        indices = []
        for _ in range(5):
            indices.append(writer.append(sample_layer))

        writer.close()

        assert indices == [0, 1, 2, 3, 4]

    def test_append_each_layer_on_separate_line(
        self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer
    ) -> None:
        """Each layer should be on a separate line (JSONL format)."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        writer.append(sample_layer)
        writer.append(sample_layer)
        writer.append(sample_layer)
        writer.close()

        lines = writer.stream_path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_append_valid_json_per_line(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """Each line should be valid JSON."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        writer.append(sample_layer)
        writer.close()

        line = writer.stream_path.read_text().strip()
        data = json.loads(line)

        assert "effect" in data
        assert "sequence" in data
        assert "scope_id" in data
        assert "scope_depth" in data

    def test_close_returns_metadata(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """close() should return metadata with effect count."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        writer.append(sample_layer)
        writer.append(sample_layer)
        metadata = writer.close()

        assert metadata.stream_id == str(stream_id)
        assert metadata.effect_count == 2
        assert metadata.closed_at is not None

    def test_context_manager_protocol(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """StreamWriter should work as context manager."""
        with StreamWriter(stream_dir, stream_id) as writer:
            writer.append(sample_layer)

        # File should exist after context exit
        stream_path = stream_dir / f"{stream_id.value}.jsonl"
        assert stream_path.exists()

    def test_append_to_closed_stream_raises(
        self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer
    ) -> None:
        """append() on closed stream should raise."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()
        writer.close()

        with pytest.raises(RuntimeError, match="closed"):
            writer.append(sample_layer)

    def test_append_without_open_raises(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """append() without open() should raise."""
        writer = StreamWriter(stream_dir, stream_id)

        with pytest.raises(RuntimeError, match="not open"):
            writer.append(sample_layer)

    def test_open_twice_raises(self, stream_dir: Path, stream_id: StreamId) -> None:
        """open() on already open stream should raise."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        try:
            with pytest.raises(RuntimeError, match="already open"):
                writer.open()
        finally:
            writer.close()

    def test_effect_count_property(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """effect_count should track number of appends."""
        writer = StreamWriter(stream_dir, stream_id)
        writer.open()

        assert writer.effect_count == 0
        writer.append(sample_layer)
        assert writer.effect_count == 1
        writer.append(sample_layer)
        assert writer.effect_count == 2

        writer.close()


# =============================================================================
# Tests: StreamReader
# =============================================================================


class TestStreamReader:
    """Tests for StreamReader class."""

    def test_read_all_returns_layers(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """read_all() should return all layers."""
        # Write some layers
        with StreamWriter(stream_dir, stream_id) as writer:
            writer.append(sample_layer)
            writer.append(sample_layer)

        # Read them back
        reader = StreamReader(writer.stream_path)
        layers = reader.read_all()

        assert len(layers) == 2
        assert layers[0].effect.task_name == "TestTask"

    def test_read_all_preserves_metadata(self, stream_dir: Path, stream_id: StreamId) -> None:
        """read_all() should preserve layer metadata."""
        layer = EffectLayer(
            effect=TaskStarted(task_name="Test", task_fqn="test.Test"),
            sequence=42,
            scope_id="scope_test",
            scope_depth=3,
            source_context="ctx:123",
        )

        with StreamWriter(stream_dir, stream_id) as writer:
            writer.append(layer)

        reader = StreamReader(writer.stream_path)
        layers = reader.read_all()

        assert len(layers) == 1
        assert layers[0].sequence == 42
        assert layers[0].scope_id == "scope_test"
        assert layers[0].scope_depth == 3
        assert layers[0].source_context == "ctx:123"

    def test_read_all_nonexistent_file(self, tmp_path: Path) -> None:
        """read_all() on nonexistent file should return empty list."""
        reader = StreamReader(tmp_path / "nonexistent.jsonl")

        layers = reader.read_all()

        assert layers == []

    def test_read_all_empty_file(self, stream_dir: Path, stream_id: StreamId) -> None:
        """read_all() on empty file should return empty list."""
        stream_path = stream_dir / f"{stream_id.value}.jsonl"
        stream_path.touch()

        reader = StreamReader(stream_path)
        layers = reader.read_all()

        assert layers == []

    def test_read_all_skips_corrupted_json(
        self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer
    ) -> None:
        """read_all() should skip corrupted JSON lines."""
        # Write valid layer
        with StreamWriter(stream_dir, stream_id) as writer:
            writer.append(sample_layer)
            stream_path = writer.stream_path

        # Append corrupted line directly
        with open(stream_path, "a") as f:
            f.write("this is not valid json\n")

        # Write another valid layer (manually to preserve file)
        with open(stream_path, "a") as f:
            layer_dict = layer_to_dict(sample_layer)
            layer_dict["sequence"] = 1
            f.write(json.dumps(layer_dict) + "\n")

        reader = StreamReader(stream_path)
        layers = reader.read_all()

        # Should get 2 valid layers, skipping the corrupted one
        assert len(layers) == 2

    def test_read_all_skips_malformed_layer(self, stream_dir: Path, stream_id: StreamId) -> None:
        """read_all() should skip layers missing required fields."""
        stream_path = stream_dir / f"{stream_id.value}.jsonl"

        # Write a line missing the 'effect' field
        with open(stream_path, "w") as f:
            f.write('{"sequence": 0, "scope_id": "test"}\n')

        reader = StreamReader(stream_path)
        layers = reader.read_all()

        assert layers == []

    def test_iter_layers_lazy(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """iter_layers() should yield layers lazily."""
        with StreamWriter(stream_dir, stream_id) as writer:
            for _i in range(100):
                writer.append(sample_layer)

        reader = StreamReader(writer.stream_path)

        # Only iterate first 5
        count = 0
        for _layer in reader.iter_layers():
            count += 1
            if count >= 5:
                break

        assert count == 5

    def test_count_effects(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """count_effects() should return number of effects."""
        with StreamWriter(stream_dir, stream_id) as writer:
            for _ in range(10):
                writer.append(sample_layer)

        reader = StreamReader(writer.stream_path)
        count = reader.count_effects()

        assert count == 10

    def test_count_effects_nonexistent_file(self, tmp_path: Path) -> None:
        """count_effects() on nonexistent file should return 0."""
        reader = StreamReader(tmp_path / "nonexistent.jsonl")

        count = reader.count_effects()

        assert count == 0

    def test_read_all_uses_explicit_registry(self, tmp_path: Path) -> None:
        """StreamReader should thread an explicit registry through layer decode."""

        class PersistenceOnlyEffect(Effect):
            effect_type: Literal["persistence_only_effect"] = "persistence_only_effect"
            payload: str = ""

        registry = KERNEL_EFFECT_REGISTRY.extend({"persistence_only_effect": PersistenceOnlyEffect})
        stream_path = tmp_path / "custom.jsonl"
        payload = {
            "effect": {"effect_type": "persistence_only_effect", "payload": "x"},
            "sequence": 0,
            "scope_id": None,
            "scope_depth": 0,
            "source_context": None,
        }
        stream_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        reader = StreamReader(stream_path, registry=registry)
        layers = reader.read_all()

        assert isinstance(layers[0].effect, PersistenceOnlyEffect)
        assert layers[0].effect.payload == "x"


# =============================================================================
# Tests: Serialization Utilities
# =============================================================================


class TestLayerSerialization:
    """Tests for layer_to_dict and layer_from_dict."""

    def test_simple_layer_roundtrip(self, sample_layer: EffectLayer) -> None:
        """Simple layer should roundtrip correctly."""
        data = layer_to_dict(sample_layer)
        restored = layer_from_dict(data)

        assert restored.sequence == sample_layer.sequence
        assert restored.scope_id == sample_layer.scope_id
        assert restored.scope_depth == sample_layer.scope_depth
        assert restored.source_context == sample_layer.source_context
        assert restored.effect.task_name == sample_layer.effect.task_name

    def test_complex_layer_roundtrip(self, complex_layer: EffectLayer) -> None:
        """Complex layer with nested data should roundtrip correctly."""
        data = layer_to_dict(complex_layer)
        restored = layer_from_dict(data, registry=compose_effect_registry())

        assert restored.sequence == complex_layer.sequence
        assert restored.scope_id == complex_layer.scope_id
        assert restored.effect.binding_name == complex_layer.effect.binding_name
        assert restored.effect.patch.files_changed == complex_layer.effect.patch.files_changed

    def test_layer_to_dict_format(self, sample_layer: EffectLayer) -> None:
        """layer_to_dict should produce expected format."""
        data = layer_to_dict(sample_layer)

        assert "effect" in data
        assert "sequence" in data
        assert "scope_id" in data
        assert "scope_depth" in data
        assert "source_context" in data
        assert data["effect"]["effect_type"] == "task_started"

    def test_layer_from_dict_handles_missing_optional(self) -> None:
        """layer_from_dict should handle missing optional fields."""
        data = {
            "effect": {
                "effect_type": "task_started",
                "task_name": "Test",
                "task_fqn": "test.Test",
            },
            # sequence, scope_id, scope_depth, source_context all missing
        }

        layer = layer_from_dict(data)

        assert layer.sequence == 0
        assert layer.scope_id is None
        assert layer.scope_depth == 0
        assert layer.source_context is None

    def test_layer_from_dict_decodes_contributorized_effects_with_runtime_registry(self) -> None:
        data = {
            "effect": {"effect_type": "key_set", "key": "alpha", "new_value": "beta"},
            "sequence": 0,
            "scope_id": None,
            "scope_depth": 0,
            "source_context": None,
        }

        restored = layer_from_dict(data, registry=compose_effect_registry())

        assert isinstance(restored.effect, KeySet)
        assert restored.effect.new_value == "beta"


# =============================================================================
# Tests: Write/Read Integration
# =============================================================================


class TestWriterReaderIntegration:
    """Integration tests for writer and reader together."""

    def test_write_then_read_multiple_effect_types(self, stream_dir: Path, stream_id: StreamId) -> None:
        """Should handle multiple effect types correctly."""
        effects = [
            TaskStarted(task_name="Task1", task_fqn="test.Task1"),
            TaskCompleted(task_name="Task1", task_fqn="test.Task1"),
            WorkspacePatchCaptured(
                binding_name="ws",
                patch=DiffPatch(patch="diff", files_changed=("a.py",)),
            ),
        ]

        # Write
        with StreamWriter(stream_dir, stream_id) as writer:
            for i, effect in enumerate(effects):
                layer = EffectLayer(effect=effect, sequence=i)
                writer.append(layer)

        # Read
        reader = StreamReader(stream_dir / f"{stream_id.value}.jsonl")
        layers = reader.read_all()

        assert len(layers) == 3
        assert layers[0].effect.effect_type == "task_started"
        assert layers[1].effect.effect_type == "task_completed"
        assert layers[2].effect.effect_type == "workspace_patch_captured"

    def test_large_stream_performance(self, stream_dir: Path, stream_id: StreamId, sample_layer: EffectLayer) -> None:
        """Should handle large streams efficiently."""
        num_effects = 1000

        # Write
        with StreamWriter(stream_dir, stream_id) as writer:
            for i in range(num_effects):
                layer = EffectLayer(
                    effect=sample_layer.effect,
                    sequence=i,
                    scope_id=sample_layer.scope_id,
                    scope_depth=sample_layer.scope_depth,
                )
                writer.append(layer)

        # Read via iterator (memory efficient)
        reader = StreamReader(stream_dir / f"{stream_id.value}.jsonl")
        count = sum(1 for _ in reader.iter_layers())

        assert count == num_effects
