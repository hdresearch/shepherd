"""Tests for SimpleWorkspace materialization support.

Covers:
- SimpleWorkspaceMaterializationIntent creation and immutability
- SimpleWorkspaceMaterializer operations (create, modify, delete)
- Rollback on failure
- Protocol methods: has_pending_changes, materialization_intent(), with_materialized()
- Registry integration
- Full flow integration tests
"""

from datetime import datetime
from pathlib import Path

import pytest
from shepherd_runtime.materialization import (
    MaterializationResult,
    get_materializer,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory with test files."""
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    # Create some test files
    (workspace_path / "existing.txt").write_text("original content\n")
    (workspace_path / "subdir").mkdir()
    (workspace_path / "subdir" / "nested.txt").write_text("nested file\n")

    return workspace_path


@pytest.fixture
def create_delta():
    """Create a FileDelta for creating a new file."""
    from shepherd_contexts.simple_workspace.delta import FileDelta

    return FileDelta.create("new_file.txt", b"new file content\n")


@pytest.fixture
def modify_delta():
    """Create a FileDelta for modifying an existing file."""
    from shepherd_contexts.simple_workspace.delta import FileDelta

    return FileDelta.modify(
        "existing.txt",
        b"original content\n",
        b"modified content\n",
    )


@pytest.fixture
def delete_delta():
    """Create a FileDelta for deleting a file."""
    from shepherd_contexts.simple_workspace.delta import FileDelta

    return FileDelta.delete("existing.txt", old_hash="dummy_hash")


@pytest.fixture
def sample_changeset(create_delta, modify_delta):
    """Create a sample changeset with multiple deltas."""
    from shepherd_contexts.simple_workspace.delta import FileChangeset

    return FileChangeset(
        deltas=(create_delta, modify_delta),
        source_step="test_step",
    )


# =============================================================================
# Tests: Intent Creation
# =============================================================================


class TestSimpleWorkspaceMaterializationIntent:
    """Tests for SimpleWorkspaceMaterializationIntent creation and manipulation."""

    def test_create_intent_with_changesets(self, temp_workspace: Path, sample_changeset) -> None:
        """Intent should hold changesets and target path."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
        )

        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(sample_changeset,),
        )

        assert intent.context_type == "SimpleWorkspace"
        assert intent.context_id == "simple-workspace:test"
        assert intent.target_path == temp_workspace
        assert len(intent.changesets) == 1
        assert intent.changesets[0] == sample_changeset

    def test_create_intent_empty_changesets(self, temp_workspace: Path) -> None:
        """Intent can be created with empty changesets."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
        )

        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(),
        )

        assert intent.changesets == ()

    def test_intent_is_frozen(self, temp_workspace: Path, sample_changeset) -> None:
        """Intent should be immutable."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
        )

        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(sample_changeset,),
        )

        with pytest.raises(AttributeError):
            intent.changesets = ()  # type: ignore


# =============================================================================
# Tests: Materializer Operations
# =============================================================================


class TestSimpleWorkspaceMaterializer:
    """Tests for SimpleWorkspaceMaterializer execution."""

    def test_materialize_empty_changesets_succeeds(self, temp_workspace: Path) -> None:
        """Materializing empty changesets should succeed immediately."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(),
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert result.paths_affected == ()

    def test_materialize_creates_file(self, temp_workspace: Path, create_delta) -> None:
        """Materializing should create new files."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        changeset = FileChangeset(deltas=(create_delta,))
        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        # Before: file doesn't exist
        new_file = temp_workspace / "new_file.txt"
        assert not new_file.exists()

        result = materializer.materialize(intent)

        assert result.success is True
        assert "new_file.txt" in result.paths_affected

        # File was created
        assert new_file.exists()
        assert new_file.read_text() == "new file content\n"

    def test_materialize_modifies_file(self, temp_workspace: Path, modify_delta) -> None:
        """Materializing should modify existing files."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        changeset = FileChangeset(deltas=(modify_delta,))
        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        # Before: original content
        existing_file = temp_workspace / "existing.txt"
        assert existing_file.read_text() == "original content\n"

        result = materializer.materialize(intent)

        assert result.success is True
        assert "existing.txt" in result.paths_affected

        # File was modified
        assert existing_file.read_text() == "modified content\n"

    def test_materialize_deletes_file(self, temp_workspace: Path, delete_delta) -> None:
        """Materializing should delete files."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        changeset = FileChangeset(deltas=(delete_delta,))
        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        # Before: file exists
        existing_file = temp_workspace / "existing.txt"
        assert existing_file.exists()

        result = materializer.materialize(intent)

        assert result.success is True
        assert "existing.txt" in result.paths_affected

        # File was deleted
        assert not existing_file.exists()

    def test_materialize_multiple_changesets(self, temp_workspace: Path, create_delta, modify_delta) -> None:
        """Materializing should apply multiple changesets in order."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        changeset1 = FileChangeset(deltas=(create_delta,))
        changeset2 = FileChangeset(deltas=(modify_delta,))
        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset1, changeset2),
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert "new_file.txt" in result.paths_affected
        assert "existing.txt" in result.paths_affected

        # Both changes applied
        assert (temp_workspace / "new_file.txt").read_text() == "new file content\n"
        assert (temp_workspace / "existing.txt").read_text() == "modified content\n"

    def test_materialize_creates_parent_directories(self, temp_workspace: Path) -> None:
        """Materializing should create parent directories for new files."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        nested_delta = FileDelta.create(
            "deep/nested/dir/file.txt",
            b"deeply nested content\n",
        )
        changeset = FileChangeset(deltas=(nested_delta,))
        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        # Before: directories don't exist
        assert not (temp_workspace / "deep").exists()

        result = materializer.materialize(intent)

        assert result.success is True
        assert (temp_workspace / "deep" / "nested" / "dir" / "file.txt").exists()
        assert (temp_workspace / "deep" / "nested" / "dir" / "file.txt").read_text() == "deeply nested content\n"


# =============================================================================
# Tests: Rollback
# =============================================================================


class TestSimpleWorkspaceMaterializerRollback:
    """Tests for rollback functionality."""

    def test_can_rollback_returns_false(self) -> None:
        """SimpleWorkspaceMaterializer should not support post-materialize rollback."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializer,
        )

        materializer = SimpleWorkspaceMaterializer()
        assert materializer.can_rollback() is False

    def test_rollback_is_noop(self, temp_workspace: Path, sample_changeset) -> None:
        """Rollback should be a no-op (backup is transient)."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(sample_changeset,),
        )

        result = materializer.materialize(intent)
        assert result.success is True

        # Rollback doesn't raise and doesn't undo changes
        materializer.rollback(intent, result)

        # Changes still present
        assert (temp_workspace / "new_file.txt").exists()


# =============================================================================
# Tests: Protocol Methods
# =============================================================================


class TestSimpleWorkspaceProtocol:
    """Tests for Materializable protocol methods on SimpleWorkspace."""

    def test_has_pending_changes_empty(self, temp_workspace: Path) -> None:
        """has_pending_changes should be False when no changesets."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        workspace = SimpleWorkspace.from_path(temp_workspace)

        assert workspace.has_pending_changes is False

    def test_has_pending_changes_with_changesets(self, temp_workspace: Path, sample_changeset) -> None:
        """has_pending_changes should be True when changesets exist."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        workspace = SimpleWorkspace.from_path(temp_workspace)
        workspace_with_changes = workspace.model_copy(update={"pending_changesets": (sample_changeset,)})

        assert workspace_with_changes.has_pending_changes is True

    def test_materialization_intent_returns_intent(self, temp_workspace: Path, sample_changeset) -> None:
        """materialization_intent() should return correct intent."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
        )

        workspace = SimpleWorkspace.from_path(temp_workspace)
        workspace_with_changes = workspace.model_copy(update={"pending_changesets": (sample_changeset,)})

        intent = workspace_with_changes.materialization_intent()

        assert isinstance(intent, SimpleWorkspaceMaterializationIntent)
        assert intent.context_id == workspace_with_changes.context_id
        assert intent.target_path == Path(temp_workspace)
        assert intent.changesets == (sample_changeset,)

    def test_with_materialized_clears_changesets(self, temp_workspace: Path, sample_changeset) -> None:
        """with_materialized() should clear pending changesets."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        workspace = SimpleWorkspace.from_path(temp_workspace)
        workspace_with_changes = workspace.model_copy(update={"pending_changesets": (sample_changeset,)})

        result = MaterializationResult.ok(paths_affected=("new_file.txt",))
        workspace_after = workspace_with_changes.with_materialized(result)

        assert workspace_after.pending_changesets == ()
        assert workspace_after.has_pending_changes is False

    def test_with_materialized_is_pure(self, temp_workspace: Path, sample_changeset) -> None:
        """with_materialized() should be pure - not modify original."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        workspace = SimpleWorkspace.from_path(temp_workspace)
        workspace_with_changes = workspace.model_copy(update={"pending_changesets": (sample_changeset,)})

        result = MaterializationResult.ok()
        workspace_after = workspace_with_changes.with_materialized(result)

        # Original unchanged
        assert workspace_with_changes.pending_changesets == (sample_changeset,)
        # New instance has cleared changesets
        assert workspace_after.pending_changesets == ()


# =============================================================================
# Tests: Registry Integration
# =============================================================================


class TestMaterializerRegistry:
    """Tests for materializer registry integration."""

    def test_materializer_registered_on_import(self) -> None:
        """SimpleWorkspaceMaterializer should be registered when module is imported."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializer,
        )

        materializer = get_materializer("SimpleWorkspace")
        assert materializer is not None
        assert isinstance(materializer, SimpleWorkspaceMaterializer)

    def test_end_to_end_via_registry(self, temp_workspace: Path, sample_changeset) -> None:
        """Full flow through registry should work."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
        )

        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(sample_changeset,),
        )

        materializer = get_materializer("SimpleWorkspace")
        assert materializer is not None

        result = materializer.materialize(intent)

        assert result.success is True
        assert (temp_workspace / "new_file.txt").exists()


# =============================================================================
# Tests: Integration
# =============================================================================


class TestFullMaterializationFlow:
    """Integration tests for full materialization flow."""

    def test_workspace_to_materialization_flow(self, temp_workspace: Path, sample_changeset) -> None:
        """Test complete flow: workspace → intent → materialize → with_materialized."""
        from shepherd_contexts.simple_workspace import (
            SimpleWorkspace,
            SimpleWorkspaceMaterializer,
        )

        # 1. Create workspace with pending changes
        workspace = SimpleWorkspace.from_path(temp_workspace)
        workspace = workspace.model_copy(update={"pending_changesets": (sample_changeset,)})

        # 2. Create intent
        intent = workspace.materialization_intent()
        assert len(intent.changesets) == 1

        # 3. Materialize
        materializer = SimpleWorkspaceMaterializer()
        result = materializer.materialize(intent)
        assert result.success is True

        # 4. Update workspace state
        workspace = workspace.with_materialized(result)
        assert workspace.has_pending_changes is False

        # 5. Verify filesystem
        assert (temp_workspace / "new_file.txt").exists()
        assert (temp_workspace / "existing.txt").read_text() == "modified content\n"

    def test_multiple_changeset_materialization(self, temp_workspace: Path, create_delta, delete_delta) -> None:
        """Test materializing multiple changesets in sequence."""
        from shepherd_contexts.simple_workspace import (
            SimpleWorkspace,
            SimpleWorkspaceMaterializer,
        )
        from shepherd_contexts.simple_workspace.delta import FileChangeset

        # Create changesets for different operations
        create_changeset = FileChangeset(deltas=(create_delta,))
        delete_changeset = FileChangeset(deltas=(delete_delta,))

        # Setup workspace
        workspace = SimpleWorkspace.from_path(temp_workspace)
        workspace = workspace.model_copy(update={"pending_changesets": (create_changeset, delete_changeset)})

        # Materialize
        intent = workspace.materialization_intent()
        materializer = SimpleWorkspaceMaterializer()
        result = materializer.materialize(intent)

        assert result.success is True
        # Created new file
        assert (temp_workspace / "new_file.txt").exists()
        # Deleted existing file
        assert not (temp_workspace / "existing.txt").exists()


# =============================================================================
# Tests: Bug Fix Verification
# =============================================================================


class TestContextIdBugFix:
    """Tests verifying the context_id bug fix ([:3] slice removed)."""

    def test_context_id_hashes_all_entries(self) -> None:
        """context_id should hash ALL manifest entries, not just first 3."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace
        from shepherd_contexts.simple_workspace.manifest import FileEntry, FileManifest

        entries = tuple(FileEntry(path=f"file{i}.txt", size_bytes=i, mtime_ns=i) for i in range(5))
        manifest1 = FileManifest(entries=entries)

        # Same first 3, different 4th/5th
        different_entries = (
            *entries[:3],
            FileEntry(path="file3.txt", size_bytes=999, mtime_ns=999),
            FileEntry(path="file4.txt", size_bytes=999, mtime_ns=999),
        )
        manifest2 = FileManifest(entries=different_entries)

        ws1 = SimpleWorkspace(path="/test", base_manifest=manifest1)
        ws2 = SimpleWorkspace(path="/test", base_manifest=manifest2)

        # With bug fixed, context_id should differ
        assert ws1.context_id != ws2.context_id


# =============================================================================
# Tests: FileChangeset sha256
# =============================================================================


class TestFileChangesetSha256:
    """Tests for FileChangeset.sha256 auto-computation."""

    def test_sha256_computed_at_construction(self) -> None:
        """sha256 is computed when changeset has deltas."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        delta = FileDelta.create("file.txt", b"content")
        changeset = FileChangeset(deltas=(delta,))

        assert changeset.sha256 is not None
        assert len(changeset.sha256) == 64  # SHA-256 hex string

    def test_empty_changeset_has_no_sha256(self) -> None:
        """Empty changeset has sha256=None."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset

        changeset = FileChangeset(deltas=())

        assert changeset.sha256 is None

    def test_same_deltas_produce_same_sha256(self) -> None:
        """Identical deltas produce identical sha256."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        delta1 = FileDelta.create("file.txt", b"content")
        delta2 = FileDelta.create("file.txt", b"content")

        changeset1 = FileChangeset(deltas=(delta1,))
        changeset2 = FileChangeset(deltas=(delta2,))

        assert changeset1.sha256 == changeset2.sha256

    def test_delta_order_does_not_affect_sha256(self) -> None:
        """Delta order within changeset doesn't affect sha256 (sorted)."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        delta_a = FileDelta.create("a.txt", b"content a")
        delta_b = FileDelta.create("b.txt", b"content b")

        changeset1 = FileChangeset(deltas=(delta_a, delta_b))
        changeset2 = FileChangeset(deltas=(delta_b, delta_a))

        assert changeset1.sha256 == changeset2.sha256

    def test_created_at_excluded_from_sha256(self) -> None:
        """created_at NOT in sha256 (content vs instance identity)."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        delta = FileDelta.create("file.txt", b"content")

        changeset1 = FileChangeset(
            deltas=(delta,),
            created_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        changeset2 = FileChangeset(
            deltas=(delta,),
            created_at=datetime(2024, 12, 31, 23, 59, 59),
        )

        assert changeset1.sha256 == changeset2.sha256

    def test_sha256_golden_value(self) -> None:
        """Golden test for hash stability."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        # Create a delta with known values
        delta = FileDelta(
            path="test.txt",
            operation="create",
            encoding="full",
            content=b"hello",
            new_content_hash="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            new_size_bytes=5,
            new_mode=0o644,
            is_binary=False,
        )
        changeset = FileChangeset(deltas=(delta,))

        # This is the expected hash based on the algorithm:
        # path|operation|content_repr|old_content_hash|new_mode
        # "test.txt|create|2cf24dba5fb0...(content_hash)||420"
        expected = "f1fce5a7c9f6b78f67be7cef9fac881e311396e170c5d441edb4d39cee443f50"
        assert changeset.sha256 == expected

    def test_different_content_produces_different_sha256(self) -> None:
        """Different content should produce different sha256."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        delta1 = FileDelta.create("file.txt", b"content1")
        delta2 = FileDelta.create("file.txt", b"content2")

        changeset1 = FileChangeset(deltas=(delta1,))
        changeset2 = FileChangeset(deltas=(delta2,))

        assert changeset1.sha256 != changeset2.sha256

    def test_delete_operation_sha256(self) -> None:
        """Delete operations should produce valid sha256."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta

        delta = FileDelta.delete("file.txt", old_hash="abc123")
        changeset = FileChangeset(deltas=(delta,))

        assert changeset.sha256 is not None
        assert len(changeset.sha256) == 64
