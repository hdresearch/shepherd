"""Unit tests for drift detection in SimpleWorkspaceMaterializer.

Drift detection ensures that files haven't been modified externally
between when changesets were created and when they are materialized.

Tests:
- No drift allows materialization to proceed
- Drift (file modified) detected and fails
- Missing file detected as drift
- Drift detection preserves original on failure
"""

import hashlib
from pathlib import Path

import pytest

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory with test files."""
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    # Create test files with known content
    (workspace_path / "existing.txt").write_text("original content\n")
    (workspace_path / "another.txt").write_text("another file\n")
    (workspace_path / "subdir").mkdir()
    (workspace_path / "subdir" / "nested.txt").write_text("nested file\n")

    return workspace_path


@pytest.fixture
def original_hash(temp_workspace: Path) -> str:
    """Get the hash of existing.txt before any modifications."""
    content = (temp_workspace / "existing.txt").read_bytes()
    return hashlib.sha256(content).hexdigest()


# =============================================================================
# Tests: _check_drift() Method
# =============================================================================


class TestCheckDriftMethod:
    """Tests for SimpleWorkspaceMaterializer._check_drift()."""

    def test_no_drift_returns_none(self, temp_workspace: Path, original_hash: str) -> None:
        """When file matches expected hash, _check_drift returns None."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializer,
        )

        materializer = SimpleWorkspaceMaterializer()
        file_path = temp_workspace / "existing.txt"

        result = materializer._check_drift(file_path, original_hash)

        assert result is None

    def test_drift_detected_when_content_differs(self, temp_workspace: Path, original_hash: str) -> None:
        """When file content changed, _check_drift returns error message."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializer,
        )

        materializer = SimpleWorkspaceMaterializer()
        file_path = temp_workspace / "existing.txt"

        # Modify the file externally
        file_path.write_text("modified content externally\n")

        result = materializer._check_drift(file_path, original_hash)

        assert result is not None
        assert "Drift detected" in result
        assert "modified externally" in result

    def test_drift_detected_when_file_deleted(self, temp_workspace: Path, original_hash: str) -> None:
        """When file was deleted, _check_drift returns error message."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializer,
        )

        materializer = SimpleWorkspaceMaterializer()
        file_path = temp_workspace / "existing.txt"

        # Delete the file externally
        file_path.unlink()

        result = materializer._check_drift(file_path, original_hash)

        assert result is not None
        assert "Drift detected" in result
        assert "deleted externally" in result

    def test_drift_detection_handles_read_error(self, temp_workspace: Path) -> None:
        """When file can't be read, _check_drift returns error message."""
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializer,
        )

        materializer = SimpleWorkspaceMaterializer()

        # Use a directory path (can't be read as file)
        dir_path = temp_workspace / "subdir"

        result = materializer._check_drift(dir_path, "some_hash")

        assert result is not None
        assert "Drift detection failed" in result or "Drift detected" in result


# =============================================================================
# Tests: Drift Detection in Materialization
# =============================================================================


class TestDriftDetectionInMaterialize:
    """Tests for drift detection during materialize()."""

    def test_no_drift_allows_materialization(self, temp_workspace: Path, original_hash: str) -> None:
        """When no drift, materialization should succeed."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        # Create a modify delta with correct old_content_hash
        modify_delta = FileDelta.modify(
            "existing.txt",
            b"original content\n",
            b"new content\n",
        )
        changeset = FileChangeset(deltas=(modify_delta,))

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert (temp_workspace / "existing.txt").read_text() == "new content\n"

    def test_drift_detected_fails_materialization(self, temp_workspace: Path, original_hash: str) -> None:
        """When drift detected, materialization should fail."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        # Create a modify delta with old content hash
        modify_delta = FileDelta.modify(
            "existing.txt",
            b"original content\n",
            b"new content\n",
        )
        changeset = FileChangeset(deltas=(modify_delta,))

        # Modify the file externally AFTER creating the delta
        (temp_workspace / "existing.txt").write_text("externally modified!\n")

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        result = materializer.materialize(intent)

        assert result.success is False
        assert result.error is not None
        assert "Drift detected" in result.error

    def test_drift_detection_preserves_original_on_failure(self, temp_workspace: Path) -> None:
        """When drift fails, files should remain in their pre-attempt state."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        # Create a file first
        create_delta = FileDelta.create("new_file.txt", b"new file content\n")

        # Then modify existing with wrong hash (will trigger drift)
        modify_delta = FileDelta.modify(
            "existing.txt",
            b"original content\n",
            b"modified content\n",
        )

        # Put create before modify - create should succeed but then modify fails
        changeset = FileChangeset(deltas=(create_delta, modify_delta))

        # Modify existing.txt externally to trigger drift
        (temp_workspace / "existing.txt").write_text("externally modified!\n")

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        result = materializer.materialize(intent)

        assert result.success is False
        assert "Drift detected" in result.error

        # Original file should be unchanged (still externally modified)
        assert (temp_workspace / "existing.txt").read_text() == "externally modified!\n"

        # New file might exist briefly but we don't guarantee rollback of creates
        # The key is that existing.txt wasn't touched

    def test_missing_file_detected_as_drift(self, temp_workspace: Path) -> None:
        """When file is deleted externally, modify should fail with drift error."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        # Create a modify delta for existing file
        modify_delta = FileDelta.modify(
            "existing.txt",
            b"original content\n",
            b"new content\n",
        )
        changeset = FileChangeset(deltas=(modify_delta,))

        # Delete the file externally
        (temp_workspace / "existing.txt").unlink()

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        result = materializer.materialize(intent)

        assert result.success is False
        assert result.error is not None
        assert "Drift detected" in result.error
        assert "deleted externally" in result.error

    def test_create_operation_no_drift_check(self, temp_workspace: Path) -> None:
        """Create operations should not trigger drift detection."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        # Create delta (no old_content_hash)
        create_delta = FileDelta.create("brand_new.txt", b"brand new content\n")
        changeset = FileChangeset(deltas=(create_delta,))

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert (temp_workspace / "brand_new.txt").exists()
        assert (temp_workspace / "brand_new.txt").read_text() == "brand new content\n"

    def test_delete_operation_no_drift_check(self, temp_workspace: Path) -> None:
        """Delete operations should not trigger drift detection."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        # Delete delta (has old_content_hash but operation is delete, not modify)
        delete_delta = FileDelta.delete("existing.txt", old_hash="some_old_hash")
        changeset = FileChangeset(deltas=(delete_delta,))

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        result = materializer.materialize(intent)

        assert result.success is True
        assert not (temp_workspace / "existing.txt").exists()

    def test_modify_without_old_hash_skips_drift_check(self, temp_workspace: Path) -> None:
        """Modify without old_content_hash should skip drift check."""
        from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
        from shepherd_contexts.simple_workspace.materializer import (
            SimpleWorkspaceMaterializationIntent,
            SimpleWorkspaceMaterializer,
        )

        # Manually create a modify delta without old_content_hash
        # (This is an edge case - normally modify() always sets it)
        modify_delta = FileDelta(
            path="existing.txt",
            operation="modify",
            encoding="full",
            content=b"new content\n",
            new_content_hash="abc123",
            old_content_hash=None,  # No hash = no drift check
        )
        changeset = FileChangeset(deltas=(modify_delta,))

        # Modify file externally - normally would trigger drift
        (temp_workspace / "existing.txt").write_text("externally modified!\n")

        materializer = SimpleWorkspaceMaterializer()
        intent = SimpleWorkspaceMaterializationIntent(
            context_id="simple-workspace:test",
            target_path=temp_workspace,
            changesets=(changeset,),
        )

        result = materializer.materialize(intent)

        # Should succeed because no drift check performed
        assert result.success is True
        assert (temp_workspace / "existing.txt").read_bytes() == b"new content\n"
