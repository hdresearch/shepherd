"""Unit tests for PersistenceManager.

Tests:
- Project initialization
- Stream lifecycle
- Multiple streams and continuation
- Reading stream chains
"""

from pathlib import Path

import pytest
from shepherd_core.effects import TaskCompleted, TaskStarted
from shepherd_core.scope.stream import EffectLayer
from shepherd_runtime.persistence import PersistenceConfig, PersistenceManager, ProjectId

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    """Create a temporary base directory for persistence."""
    return tmp_path / ".shepherd"


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    project = tmp_path / "my_project"
    project.mkdir()
    return project


@pytest.fixture
def project_id(project_path: Path) -> ProjectId:
    """Create a ProjectId for the test project."""
    return ProjectId.from_path(project_path)


@pytest.fixture
def manager(base_dir: Path, project_id: ProjectId) -> PersistenceManager:
    """Create an initialized PersistenceManager."""
    mgr = PersistenceManager(base_dir, project_id)
    mgr.initialize()
    return mgr


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
        scope_id="scope_test",
        scope_depth=0,
    )


# =============================================================================
# Tests: Initialization
# =============================================================================


class TestManagerInitialization:
    """Tests for PersistenceManager initialization."""

    def test_initialize_creates_directories(self, base_dir: Path, project_id: ProjectId) -> None:
        """initialize() should create project directories."""
        manager = PersistenceManager(base_dir, project_id)

        manager.initialize()

        assert manager.project_dir.exists()
        assert manager.streams_dir.exists()

    def test_initialize_creates_project_metadata(self, base_dir: Path, project_id: ProjectId) -> None:
        """initialize() should create project.json."""
        manager = PersistenceManager(base_dir, project_id)

        manager.initialize()

        assert manager.project_metadata_path.exists()

    def test_initialize_idempotent(self, base_dir: Path, project_id: ProjectId) -> None:
        """initialize() should be safe to call multiple times."""
        manager = PersistenceManager(base_dir, project_id)

        manager.initialize()
        manager.initialize()  # Should not raise

        assert manager.project_dir.exists()

    def test_paths_use_project_hash(self, base_dir: Path, project_id: ProjectId) -> None:
        """Paths should use project hash for directory name."""
        manager = PersistenceManager(base_dir, project_id)

        assert project_id.hash in str(manager.project_dir)


# =============================================================================
# Tests: Stream Lifecycle
# =============================================================================


class TestStreamLifecycle:
    """Tests for stream start, append, and close."""

    def test_start_stream_creates_file(self, manager: PersistenceManager) -> None:
        """start_stream() should create the stream file."""
        stream_id = manager.start_stream()

        stream_path = manager.streams_dir / f"{stream_id.value}.jsonl"
        assert stream_path.exists()

    def test_start_stream_updates_index(self, manager: PersistenceManager) -> None:
        """start_stream() should update stream index."""
        stream_id = manager.start_stream()
        manager.close_stream()

        info = manager.get_stream_info()
        assert str(stream_id) in info

    def test_append_layer_writes_to_stream(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """append_layer() should write to the stream."""
        stream_id = manager.start_stream()

        index = manager.append_layer(sample_layer)

        assert index == 0
        manager.close_stream()

        # Verify by reading back
        layers = manager.read_stream(str(stream_id))
        assert len(layers) == 1

    def test_close_stream_finalizes(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """close_stream() should finalize the stream."""
        manager.start_stream()
        manager.append_layer(sample_layer)
        manager.append_layer(sample_layer)

        metadata = manager.close_stream()

        assert metadata is not None
        assert metadata.effect_count == 2
        assert metadata.closed_at is not None

    def test_close_stream_clears_current(self, manager: PersistenceManager) -> None:
        """close_stream() should clear current_stream_id."""
        manager.start_stream()
        manager.close_stream()

        # Reload index to verify persisted state
        from shepherd_runtime.persistence import StreamIndex

        index = StreamIndex.load(manager.index_path)

        assert index.current_stream_id is None

    def test_start_stream_while_open_raises(self, manager: PersistenceManager) -> None:
        """start_stream() with open stream should raise."""
        manager.start_stream()

        with pytest.raises(RuntimeError, match="already open"):
            manager.start_stream()

        manager.close_stream()

    def test_append_without_stream_raises(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """append_layer() without stream should raise."""
        with pytest.raises(RuntimeError, match="No active stream"):
            manager.append_layer(sample_layer)


# =============================================================================
# Tests: Stream Continuation
# =============================================================================


class TestStreamContinuation:
    """Tests for stream continuation (resume)."""

    def test_start_stream_with_continues_from(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """start_stream() should record continues_from."""
        # First stream
        stream1_id = manager.start_stream()
        manager.append_layer(sample_layer)
        manager.close_stream()

        # Second stream continuing from first
        stream2_id = manager.start_stream(continues_from=str(stream1_id))
        manager.close_stream()

        info = manager.get_stream_info()
        stream2_meta = info[str(stream2_id)]

        assert stream2_meta.continues_from == str(stream1_id)

    def test_read_stream_chain(self, manager: PersistenceManager) -> None:
        """read_stream_chain() should follow continues_from."""
        # First stream
        stream1_id = manager.start_stream()
        manager.append_layer(
            EffectLayer(
                effect=TaskStarted(task_name="Task1", task_fqn="test.Task1"),
                sequence=0,
            )
        )
        manager.close_stream()

        # Second stream continuing from first
        stream2_id = manager.start_stream(continues_from=str(stream1_id))
        manager.append_layer(
            EffectLayer(
                effect=TaskCompleted(task_name="Task1", task_fqn="test.Task1"),
                sequence=1,
            )
        )
        manager.close_stream()

        # Read chain
        layers = manager.read_stream_chain(str(stream2_id))

        assert len(layers) == 2
        # Oldest first
        assert layers[0].effect.effect_type == "task_started"
        assert layers[1].effect.effect_type == "task_completed"

    def test_read_stream_chain_default_latest(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """read_stream_chain() should default to latest stream."""
        stream1_id = manager.start_stream()
        manager.append_layer(sample_layer)
        manager.close_stream()

        stream2_id = manager.start_stream(continues_from=str(stream1_id))
        manager.append_layer(sample_layer)
        manager.close_stream()

        # Read without specifying stream ID
        layers = manager.read_stream_chain()

        assert len(layers) == 2


# =============================================================================
# Tests: Read Operations
# =============================================================================


class TestReadOperations:
    """Tests for reading streams."""

    def test_read_stream_by_id(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """read_stream() should read specific stream."""
        stream_id = manager.start_stream()
        manager.append_layer(sample_layer)
        manager.close_stream()

        layers = manager.read_stream(str(stream_id))

        assert len(layers) == 1

    def test_read_latest_stream(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """read_latest_stream() should read most recent stream."""
        # Create multiple streams
        manager.start_stream()
        manager.append_layer(sample_layer)
        manager.close_stream()

        import time

        time.sleep(0.01)

        manager.start_stream()
        manager.append_layer(sample_layer)
        manager.append_layer(sample_layer)
        manager.close_stream()

        # Should read the second stream
        layers = manager.read_latest_stream()

        assert len(layers) == 2

    def test_read_latest_stream_no_streams(self, manager: PersistenceManager) -> None:
        """read_latest_stream() with no streams should return empty list."""
        layers = manager.read_latest_stream()

        assert layers == []

    def test_get_stream_info(self, manager: PersistenceManager, sample_layer: EffectLayer) -> None:
        """get_stream_info() should return all stream metadata."""
        stream1_id = manager.start_stream()
        manager.append_layer(sample_layer)
        manager.close_stream()

        stream2_id = manager.start_stream()
        manager.close_stream()

        info = manager.get_stream_info()

        assert len(info) == 2
        assert str(stream1_id) in info
        assert str(stream2_id) in info


# =============================================================================
# Tests: PersistenceConfig
# =============================================================================


class TestPersistenceConfig:
    """Tests for PersistenceConfig."""

    def test_default_values(self) -> None:
        """Default config should have sensible defaults."""
        config = PersistenceConfig()

        assert config.enabled is True
        assert config.base_dir == Path.home() / ".shepherd"

    def test_custom_base_dir(self, tmp_path: Path) -> None:
        """Config should accept custom base_dir."""
        config = PersistenceConfig(base_dir=tmp_path / "custom")

        assert config.base_dir == tmp_path / "custom"

    def test_disabled_config(self) -> None:
        """Config should support disabled state."""
        config = PersistenceConfig(enabled=False)

        assert config.enabled is False


# =============================================================================
# Tests: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_manager_not_initialized_raises(self, base_dir: Path, project_id: ProjectId) -> None:
        """Operations before initialize() should raise."""
        manager = PersistenceManager(base_dir, project_id)

        with pytest.raises(RuntimeError, match="not initialized"):
            manager.start_stream()

    def test_close_without_open_returns_none(self, manager: PersistenceManager) -> None:
        """close_stream() without open stream should return None."""
        result = manager.close_stream()

        assert result is None

    def test_read_nonexistent_stream(self, manager: PersistenceManager) -> None:
        """read_stream() for nonexistent stream should return empty list."""
        layers = manager.read_stream("nonexistent_stream_id")

        assert layers == []

    def test_multiple_managers_same_project(
        self, base_dir: Path, project_id: ProjectId, sample_layer: EffectLayer
    ) -> None:
        """Multiple managers for same project should see each other's streams."""
        # First manager writes
        manager1 = PersistenceManager(base_dir, project_id)
        manager1.initialize()
        stream_id = manager1.start_stream()
        manager1.append_layer(sample_layer)
        manager1.close_stream()

        # Second manager reads
        manager2 = PersistenceManager(base_dir, project_id)
        manager2.initialize()
        layers = manager2.read_stream(str(stream_id))

        assert len(layers) == 1
