"""Unit tests for project identification and metadata.

Tests:
- ProjectId hash computation
- Git root detection
- ProjectMetadata serialization
- Path verification
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from shepherd_runtime.persistence import ProjectId, ProjectMetadata

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    project_path = tmp_path / "my_project"
    project_path.mkdir()
    return project_path


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    repo_path = tmp_path / "git_repo"
    repo_path.mkdir()

    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True, timeout=30)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True, timeout=30)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )

    return repo_path


# =============================================================================
# Tests: ProjectId
# =============================================================================


class TestProjectId:
    """Tests for ProjectId class."""

    def test_from_path_creates_valid_id(self, temp_project: Path) -> None:
        """from_path should create a valid ProjectId."""
        project_id = ProjectId.from_path(temp_project)

        assert project_id.canonical_path == str(temp_project)
        assert len(project_id.hash) == 16  # Truncated SHA256
        assert project_id.hash.isalnum()

    def test_hash_is_deterministic(self, temp_project: Path) -> None:
        """Same path should produce same hash."""
        id1 = ProjectId.from_path(temp_project)
        id2 = ProjectId.from_path(temp_project)

        assert id1.hash == id2.hash
        assert id1.canonical_path == id2.canonical_path

    def test_different_paths_produce_different_hashes(self, tmp_path: Path) -> None:
        """Different paths should produce different hashes."""
        path1 = tmp_path / "project1"
        path2 = tmp_path / "project2"
        path1.mkdir()
        path2.mkdir()

        id1 = ProjectId.from_path(path1)
        id2 = ProjectId.from_path(path2)

        assert id1.hash != id2.hash

    def test_relative_path_resolved_to_absolute(self, temp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Relative paths should be resolved to absolute."""
        monkeypatch.chdir(temp_project.parent)

        project_id = ProjectId.from_path(Path(temp_project.name))

        assert Path(project_id.canonical_path).is_absolute()
        assert project_id.canonical_path == str(temp_project)

    def test_git_root_used_as_canonical_path(self, git_project: Path) -> None:
        """For git repos, the repo root should be the canonical path."""
        # Create a subdirectory
        subdir = git_project / "src" / "module"
        subdir.mkdir(parents=True)

        # Create ProjectId from subdirectory
        project_id = ProjectId.from_path(subdir)

        # Should use git root, not the subdirectory
        assert project_id.canonical_path == str(git_project)

    def test_non_git_directory_uses_resolved_path(self, temp_project: Path) -> None:
        """Non-git directories should use the resolved path."""
        project_id = ProjectId.from_path(temp_project)

        assert project_id.canonical_path == str(temp_project)

    def test_repr_shows_hash_and_path(self, temp_project: Path) -> None:
        """Repr should show hash and canonical path."""
        project_id = ProjectId.from_path(temp_project)
        repr_str = repr(project_id)

        assert project_id.hash in repr_str
        assert project_id.canonical_path in repr_str


class TestProjectIdGitRoot:
    """Tests for git root detection."""

    def test_find_git_root_returns_repo_root(self, git_project: Path) -> None:
        """_find_git_root should return the repo root."""
        root = ProjectId._find_git_root(git_project)

        assert root == git_project

    def test_find_git_root_from_subdirectory(self, git_project: Path) -> None:
        """_find_git_root should work from subdirectories."""
        subdir = git_project / "src"
        subdir.mkdir()

        root = ProjectId._find_git_root(subdir)

        assert root == git_project

    def test_find_git_root_returns_none_for_non_repo(self, temp_project: Path) -> None:
        """_find_git_root should return None for non-git directories."""
        root = ProjectId._find_git_root(temp_project)

        assert root is None

    def test_find_git_root_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        """_find_git_root should return None for nonexistent paths."""
        root = ProjectId._find_git_root(tmp_path / "nonexistent")

        assert root is None


# =============================================================================
# Tests: ProjectMetadata
# =============================================================================


class TestProjectMetadata:
    """Tests for ProjectMetadata class."""

    def test_create_with_defaults(self) -> None:
        """Creating metadata should set timestamps."""
        before = datetime.now(timezone.utc)
        metadata = ProjectMetadata(canonical_path="/test/path")
        after = datetime.now(timezone.utc)

        assert metadata.canonical_path == "/test/path"
        assert before <= metadata.created_at <= after
        assert before <= metadata.last_accessed <= after

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        """Serialization should roundtrip correctly."""
        original = ProjectMetadata(canonical_path="/test/path")

        data = original.to_dict()
        restored = ProjectMetadata.from_dict(data)

        assert restored.canonical_path == original.canonical_path
        assert restored.created_at == original.created_at
        assert restored.last_accessed == original.last_accessed

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Metadata should persist to disk correctly."""
        path = tmp_path / "project.json"
        original = ProjectMetadata(canonical_path="/test/path")

        original.save(path)
        loaded = ProjectMetadata.load(path)

        assert loaded.canonical_path == original.canonical_path
        assert loaded.created_at == original.created_at

    def test_with_access_update(self) -> None:
        """with_access_update should update last_accessed."""
        original = ProjectMetadata(canonical_path="/test/path")
        import time

        time.sleep(0.01)  # Ensure time passes

        updated = original.with_access_update()

        assert updated.canonical_path == original.canonical_path
        assert updated.created_at == original.created_at
        assert updated.last_accessed > original.last_accessed

    def test_to_dict_format(self) -> None:
        """to_dict should produce expected format."""
        metadata = ProjectMetadata(canonical_path="/test/path")
        data = metadata.to_dict()

        assert "canonical_path" in data
        assert "created_at" in data
        assert "last_accessed" in data
        assert data["canonical_path"] == "/test/path"
        # Timestamps should be ISO format strings
        assert isinstance(data["created_at"], str)
        assert "T" in data["created_at"]  # ISO format
