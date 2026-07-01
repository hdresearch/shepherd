"""Unit tests for scope persistence integration.

Tests:
- Persistence initialization
- Effects persisted on emit
- Child scope effects persisted via parent
- Stream closed on scope exit
- Persistence disabled by default
"""

from pathlib import Path

import pytest
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import DiffPatch, TaskCompleted, TaskStarted
from shepherd_runtime.persistence import PersistenceManager, ProjectId, StreamReader
from shepherd_runtime.scope import Scope

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    project = tmp_path / "my_project"
    project.mkdir()
    return project


@pytest.fixture
def shepherd_dir(tmp_path: Path) -> Path:
    """Get the shepherd storage directory for this test."""
    return tmp_path / ".shepherd"


# =============================================================================
# Tests: Persistence Initialization
# =============================================================================


class TestPersistenceInitialization:
    """Tests for persistence initialization."""

    def test_persistence_enabled_with_project_path(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Persistence should be enabled when project_path is provided."""
        # Monkeypatch home to use tmp_path
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as scope:
            assert scope._persistence_manager.manager is not None

    def test_persistence_disabled_by_default(self) -> None:
        """Persistence should be disabled when no project_path."""
        with Scope() as scope:
            assert scope._persistence_manager.manager is None

    def test_persistence_can_be_explicitly_disabled(self, project_path: Path) -> None:
        """Persistence can be disabled even with project_path."""
        with Scope(project_path=project_path, persistence=False) as scope:
            assert scope._persistence_manager.manager is None

    def test_nested_scope_with_project_path_requires_root(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nested scopes cannot configure their own project path unless root=True."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with (
            Scope(project_path=project_path) as scope,
            pytest.raises(ValueError, match="root=True"),
            Scope(project_path=project_path),
        ):
            pass

    def test_nested_scope_with_explicit_persistence_requires_root(self) -> None:
        """Nested scopes cannot explicitly enable persistence without root=True."""
        with Scope() as scope, pytest.raises(ValueError, match="root=True"), Scope(persistence=True):
            pass

    def test_persistence_creates_project_directory(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Persistence should create project directory structure."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as scope:
            pass  # Just enter and exit

        # Check directory was created
        shepherd_dir = tmp_path / ".shepherd"
        projects_dir = shepherd_dir / "projects"
        assert projects_dir.exists()

        # Should have exactly one project directory
        project_dirs = list(projects_dir.iterdir())
        assert len(project_dirs) == 1


# =============================================================================
# Tests: Effect Persistence
# =============================================================================


class TestEffectPersistence:
    """Tests for persisting effects on emit."""

    def test_emit_persists_effects(self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """emit() should persist effects to disk."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        effect = TaskStarted(task_name="Test", task_fqn="test.Test")

        with Scope(project_path=project_path) as scope:
            scope.emit(effect)
            stream_path = scope._persistence_manager.manager.streams_dir

        # Read back and verify
        jsonl_files = list(stream_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1

        reader = StreamReader(jsonl_files[0])
        layers = reader.read_all()

        assert len(layers) == 1
        assert layers[0].effect.task_name == "Test"

    def test_multiple_effects_persisted(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple effects should all be persisted."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as scope:
            scope.emit(TaskStarted(task_name="Task1", task_fqn="test.Task1"))
            scope.emit(TaskCompleted(task_name="Task1", task_fqn="test.Task1"))
            scope.emit(TaskStarted(task_name="Task2", task_fqn="test.Task2"))
            stream_path = scope._persistence_manager.manager.streams_dir

        # Read back
        jsonl_files = list(stream_path.glob("*.jsonl"))
        reader = StreamReader(jsonl_files[0])
        layers = reader.read_all()

        assert len(layers) == 3

    def test_complex_effects_persisted(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Complex effects with nested data should persist correctly."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        effect = WorkspacePatchCaptured(
            binding_name="workspace",
            patch=DiffPatch(
                patch="diff --git a/test.py b/test.py\n+new line",
                files_changed=("test.py", "other.py"),
            ),
        )

        with Scope(project_path=project_path) as scope:
            scope.emit(effect)
            stream_path = scope._persistence_manager.manager.streams_dir

        # Read back and verify nested data
        jsonl_files = list(stream_path.glob("*.jsonl"))
        reader = StreamReader(jsonl_files[0])
        layers = reader.read_all()

        assert len(layers) == 1
        restored = layers[0].effect
        assert restored.binding_name == "workspace"
        assert restored.patch.files_changed == ("test.py", "other.py")

    def test_effect_layer_metadata_persisted(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EffectLayer metadata (scope_id, scope_depth) should be persisted."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as scope:
            scope.emit(TaskStarted(task_name="Test", task_fqn="test.Test"))
            scope_id = scope.id
            stream_path = scope._persistence_manager.manager.streams_dir

        # Read back and check metadata
        jsonl_files = list(stream_path.glob("*.jsonl"))
        reader = StreamReader(jsonl_files[0])
        layers = reader.read_all()

        assert len(layers) == 1
        assert layers[0].scope_id == scope_id
        assert layers[0].scope_depth == 0


# =============================================================================
# Tests: Child Scope Effect Propagation
# =============================================================================


class TestChildScopePersistence:
    """Tests for child scope effects being persisted via parent."""

    def test_child_scope_effects_persisted_via_parent(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effects from child scope should be persisted by root scope."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as root:
            child = root.child()
            child.emit(TaskStarted(task_name="ChildTask", task_fqn="test.ChildTask"))
            stream_path = root._persistence_manager.manager.streams_dir

        # Read back from root's stream
        jsonl_files = list(stream_path.glob("*.jsonl"))
        reader = StreamReader(jsonl_files[0])
        layers = reader.read_all()

        assert len(layers) == 1
        assert layers[0].effect.task_name == "ChildTask"

    def test_auto_nested_scope_uses_child_depth_and_parent_persistence(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Implicitly nested scopes should behave exactly like child()."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as root:
            with Scope() as child:
                assert child._parent_proxy is root
                assert child._persistence_manager.manager is None
                child.emit(TaskStarted(task_name="NestedTask", task_fqn="test.NestedTask"))

            stream_path = root._persistence_manager.manager.streams_dir

        jsonl_files = list(stream_path.glob("*.jsonl"))
        reader = StreamReader(jsonl_files[0])
        layers = reader.read_all()

        assert len(layers) == 1
        assert layers[0].scope_id == child.id
        assert layers[0].scope_depth == 1

    def test_child_scope_preserves_depth_metadata(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Child scope effects should preserve their depth metadata."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as root:
            root.emit(TaskStarted(task_name="RootTask", task_fqn="test.RootTask"))

            child = root.child()
            child.emit(TaskStarted(task_name="ChildTask", task_fqn="test.ChildTask"))

            grandchild = child.child()
            grandchild.emit(TaskStarted(task_name="GrandchildTask", task_fqn="test.GrandchildTask"))

            stream_path = root._persistence_manager.manager.streams_dir

        # Read back and check depth metadata
        jsonl_files = list(stream_path.glob("*.jsonl"))
        reader = StreamReader(jsonl_files[0])
        layers = reader.read_all()

        assert len(layers) == 3
        assert layers[0].scope_depth == 0  # Root
        assert layers[1].scope_depth == 1  # Child
        assert layers[2].scope_depth == 2  # Grandchild

    def test_child_scope_no_own_persistence(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Child scopes should not have their own persistence."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as root:
            child = root.child()
            assert child._persistence_manager.manager is None


# =============================================================================
# Tests: Stream Lifecycle
# =============================================================================


class TestStreamLifecycle:
    """Tests for stream open/close lifecycle."""

    def test_stream_closed_on_scope_exit(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stream should be closed when scope exits."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as scope:
            scope.emit(TaskStarted(task_name="Test", task_fqn="test.Test"))

        # After exit, verify stream was closed by checking index
        project_id = ProjectId.from_path(project_path)
        manager = PersistenceManager(tmp_path / ".shepherd", project_id)
        manager.initialize()

        info = manager.get_stream_info()
        assert len(info) == 1

        # The stream should be closed (current_stream_id should be None)
        from shepherd_runtime.persistence import StreamIndex

        index = StreamIndex.load(manager.index_path)
        assert index.current_stream_id is None

    def test_stream_closed_on_exception(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stream should be closed even when scope exits with exception."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        try:
            with Scope(project_path=project_path) as scope:
                scope.emit(TaskStarted(task_name="Test", task_fqn="test.Test"))
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Stream should still be closed
        project_id = ProjectId.from_path(project_path)
        manager = PersistenceManager(tmp_path / ".shepherd", project_id)
        manager.initialize()

        from shepherd_runtime.persistence import StreamIndex

        index = StreamIndex.load(manager.index_path)
        assert index.current_stream_id is None


# =============================================================================
# Tests: No Persistence Mode
# =============================================================================


class TestNoPersistence:
    """Tests for scopes without persistence."""

    def test_emit_works_without_persistence(self) -> None:
        """emit() should work normally without persistence."""
        with Scope() as scope:
            scope.emit(TaskStarted(task_name="Test", task_fqn="test.Test"))
            scope.emit(TaskCompleted(task_name="Test", task_fqn="test.Test"))

            assert len(scope.effects) == 2

    def test_child_scope_works_without_persistence(self) -> None:
        """Child scopes should work normally without persistence."""
        with Scope() as root:
            child = root.child()
            child.emit(TaskStarted(task_name="Child", task_fqn="test.Child"))

            # Effect should propagate to root
            assert len(root.effects) == 1


# =============================================================================
# Tests: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_scope_stream_closed(
        self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty scope (no effects) should still close stream properly."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with Scope(project_path=project_path) as scope:
            pass  # No effects emitted

        # Stream should exist but be empty
        project_id = ProjectId.from_path(project_path)
        manager = PersistenceManager(tmp_path / ".shepherd", project_id)
        manager.initialize()

        layers = manager.read_latest_stream()
        assert layers == []

    def test_multiple_scope_sessions(self, project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple scope sessions should create separate streams."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Session 1
        with Scope(project_path=project_path) as scope:
            scope.emit(TaskStarted(task_name="Session1", task_fqn="test.Session1"))

        # Session 2
        with Scope(project_path=project_path) as scope:
            scope.emit(TaskStarted(task_name="Session2", task_fqn="test.Session2"))

        # Should have 2 streams
        project_id = ProjectId.from_path(project_path)
        manager = PersistenceManager(tmp_path / ".shepherd", project_id)
        manager.initialize()

        info = manager.get_stream_info()
        assert len(info) == 2
