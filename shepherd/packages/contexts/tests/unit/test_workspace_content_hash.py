"""Tests for WorkspaceRef.content_hash and content_equals().

These tests verify the content-addressable identity feature for caching:
- content_hash is computed from base_commit + pending_patches
- content_hash is independent of filesystem path
- content_equals() compares logical content
- Hash is stable and deterministic
"""

import subprocess
from pathlib import Path

import pytest
from shepherd_contexts.workspace import WorkspaceRef
from shepherd_core.effects import DiffPatch

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    repo_path = tmp_path / "repo"
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
        ["git", "config", "user.name", "Test"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return repo_path


@pytest.fixture
def sample_commit() -> str:
    """A valid 40-char SHA for unit tests that don't need a real repo."""
    return "a" * 40


# =============================================================================
# Tests: content_hash Property
# =============================================================================


class TestContentHash:
    """Tests for content_hash property."""

    def test_same_content_same_hash(self, git_repo: Path) -> None:
        """Identical workspaces produce identical hashes."""
        ws1 = WorkspaceRef.from_path(str(git_repo))
        ws2 = WorkspaceRef.from_path(str(git_repo))
        assert ws1.content_hash == ws2.content_hash

    def test_different_path_same_content_hash(self, sample_commit: str) -> None:
        """Path doesn't affect content_hash."""
        ws1 = WorkspaceRef(path="/path/a", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/path/b", base_commit=sample_commit)
        assert ws1.content_hash == ws2.content_hash

    def test_patch_changes_hash(self, sample_commit: str) -> None:
        """Adding a patch changes the hash."""
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        patch = DiffPatch(patch="diff content", files_changed=("file.txt",))
        ws2 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(patch,),
        )
        assert ws1.content_hash != ws2.content_hash

    def test_patch_order_matters(self, sample_commit: str) -> None:
        """Different patch order produces different hash."""
        patch_a = DiffPatch(patch="diff A", files_changed=("a.txt",))
        patch_b = DiffPatch(patch="diff B", files_changed=("b.txt",))

        ws1 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(patch_a, patch_b),
        )
        ws2 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(patch_b, patch_a),
        )
        assert ws1.content_hash != ws2.content_hash

    def test_empty_patch_ignored(self, sample_commit: str) -> None:
        """Empty patches don't change hash."""
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        empty_patch = DiffPatch(patch="", files_changed=())
        ws2 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(empty_patch,),
        )
        assert ws1.content_hash == ws2.content_hash

    def test_hash_is_deterministic(self, git_repo: Path) -> None:
        """Same workspace produces same hash across calls."""
        ws = WorkspaceRef.from_path(str(git_repo))
        hash1 = ws.content_hash
        hash2 = ws.content_hash
        assert hash1 == hash2

    def test_hash_length(self, sample_commit: str) -> None:
        """Hash is 12 hex characters."""
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        assert len(ws.content_hash) == 12
        assert all(c in "0123456789abcdef" for c in ws.content_hash)

    def test_hash_stability_golden(self, sample_commit: str) -> None:
        """Golden test: verify hash algorithm stability across versions.

        This test ensures the hash algorithm doesn't accidentally change.
        If this test fails after code changes, the cache format has changed
        and existing caches will be invalidated!

        Pre-computed: hashlib.sha256(("a"*40).encode("utf-8")).hexdigest()[:12]
        >>> import hashlib
        >>> hashlib.sha256(("a" * 40).encode("utf-8")).hexdigest()[:12]
        'e33cdf9c7f71'
        """
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        expected = "e33cdf9c7f71"
        assert ws.content_hash == expected

    def test_model_copy_updates_content_hash(self, sample_commit: str) -> None:
        """Verify content_hash reflects updated state after model_copy().

        This works because content_hash is a @property that recomputes
        on each access, NOT because model_copy triggers validators (it doesn't).
        """
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        original_hash = ws1.content_hash

        # Add a patch via model_copy
        patch = DiffPatch(patch="new content", files_changed=("file.txt",))
        ws2 = ws1.model_copy(update={"pending_patches": (patch,)})

        # content_hash MUST be different (property recomputes from new state)
        assert ws2.content_hash != original_hash
        assert ws2.pending_patches == (patch,)


# =============================================================================
# Tests: content_equals() Method
# =============================================================================


class TestContentEquals:
    """Tests for content_equals() method."""

    def test_content_equals_same_path(self, sample_commit: str) -> None:
        """content_equals returns True for identical workspaces."""
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        assert ws1.content_equals(ws2)

    def test_content_equals_different_path(self, sample_commit: str) -> None:
        """content_equals returns True when only path differs."""
        ws1 = WorkspaceRef(path="/path/a", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/path/b", base_commit=sample_commit)
        assert ws1.content_equals(ws2)

    def test_content_equals_different_content(self, sample_commit: str) -> None:
        """content_equals returns False for different content."""
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        patch = DiffPatch(patch="diff", files_changed=("f.txt",))
        ws2 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(patch,),
        )
        assert not ws1.content_equals(ws2)


# =============================================================================
# Tests: Resource Identity vs Content Identity
# =============================================================================


class TestResourceIdentity:
    """Tests for resource identity (__eq__ and __hash__)."""

    def test_different_path_not_equal(self, sample_commit: str) -> None:
        """Workspaces at different paths are NOT equal (resource identity)."""
        ws1 = WorkspaceRef(path="/path/a", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/path/b", base_commit=sample_commit)
        # Same content, but different resources
        assert ws1.content_hash == ws2.content_hash  # Content equal
        assert ws1 != ws2  # Resource NOT equal

    def test_same_path_same_content_equal(self, sample_commit: str) -> None:
        """Workspaces at same path with same content are equal."""
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        assert ws1 == ws2

    def test_resource_tracking_preserves_both(self, sample_commit: str) -> None:
        """Dict keyed by WorkspaceRef preserves both resources."""
        ws1 = WorkspaceRef(path="/sandbox-1/", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/sandbox-2/", base_commit=sample_commit)

        # Resource tracking: each sandbox tracked separately
        modifications: dict[WorkspaceRef, list[str]] = {}
        modifications[ws1] = ["auth.py"]
        modifications[ws2] = ["readme.md"]

        # Both entries preserved (different paths = different keys)
        assert len(modifications) == 2
        assert modifications[ws1] == ["auth.py"]
        assert modifications[ws2] == ["readme.md"]

    def test_content_hash_cache_deduplicates(self, sample_commit: str) -> None:
        """Cache keyed by content_hash deduplicates same content."""
        ws1 = WorkspaceRef(path="/sandbox-1/", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/sandbox-2/", base_commit=sample_commit)

        # Content-based cache: same content = same key
        cache: dict[str, str] = {}
        cache[ws1.content_hash] = "result1"
        cache[ws2.content_hash] = "result2"  # Overwrites (same content_hash)

        # Only one entry (same content_hash)
        assert len(cache) == 1
        assert cache[ws1.content_hash] == "result2"

    def test_set_membership_by_resource(self, sample_commit: str) -> None:
        """Set of WorkspaceRef uses resource identity."""
        ws1 = WorkspaceRef(path="/sandbox-1/", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/sandbox-2/", base_commit=sample_commit)

        # Both kept in set (different paths)
        workspace_set = {ws1, ws2}
        assert len(workspace_set) == 2

    def test_set_membership_by_content(self, sample_commit: str) -> None:
        """Set of content_hash strings deduplicates by content."""
        ws1 = WorkspaceRef(path="/sandbox-1/", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/sandbox-2/", base_commit=sample_commit)

        # Deduplicated by content
        content_set = {ws1.content_hash, ws2.content_hash}
        assert len(content_set) == 1


# =============================================================================
# Tests: state_hash() Method
# =============================================================================


class TestStateHash:
    """Tests for state_hash() method (cache key computation)."""

    def test_same_content_different_path_same_hash(self, sample_commit: str) -> None:
        """Same content at different paths should produce same state_hash."""
        ws1 = WorkspaceRef(path="/sandbox-1/", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/sandbox-2/", base_commit=sample_commit)
        assert ws1.state_hash() == ws2.state_hash()

    def test_different_capabilities_different_hash(self, sample_commit: str) -> None:
        """Different capabilities should produce different state_hash."""
        ws_ro = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            capabilities=frozenset({"read"}),
        )
        ws_rw = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            capabilities=frozenset({"read", "write"}),
        )
        assert ws_ro.state_hash() != ws_rw.state_hash()

    def test_state_hash_uses_content_hash(self, sample_commit: str) -> None:
        """state_hash should incorporate content_hash."""
        patch = DiffPatch(patch="diff content", files_changed=("f.txt",))
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        ws2 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(patch,),
        )
        # Different content → different state_hash
        assert ws1.state_hash() != ws2.state_hash()

    def test_state_hash_length(self, sample_commit: str) -> None:
        """state_hash should be 16 hex characters."""
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        assert len(ws.state_hash()) == 16
        assert all(c in "0123456789abcdef" for c in ws.state_hash())

    def test_content_hash_unchanged_by_capabilities(self, sample_commit: str) -> None:
        """Capabilities should NOT affect content_hash (only state_hash)."""
        ws_ro = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            capabilities=frozenset({"read"}),
        )
        ws_rw = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            capabilities=frozenset({"read", "write"}),
        )
        # Same content_hash (capabilities excluded)
        assert ws_ro.content_hash == ws_rw.content_hash
        # Different state_hash (capabilities included)
        assert ws_ro.state_hash() != ws_rw.state_hash()

    def test_state_hash_deterministic(self, sample_commit: str) -> None:
        """state_hash should be deterministic across calls."""
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        assert ws.state_hash() == ws.state_hash()

    def test_state_hash_accepts_hashing_scope(self, sample_commit: str) -> None:
        """state_hash should accept HashingScope parameter (for API compatibility)."""
        from shepherd_runtime.cache import HashingScope

        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        # Both should produce the same result (scope is ignored for WorkspaceRef)
        assert ws.state_hash(HashingScope.FULL) == ws.state_hash(HashingScope.TRACKED_ONLY)
        assert ws.state_hash(None) == ws.state_hash(HashingScope.FULL)

    def test_state_hash_stability_golden(self, sample_commit: str) -> None:
        """Golden test: verify state_hash algorithm stability across versions.

        This test ensures the state_hash algorithm doesn't accidentally change.
        If this test fails after code changes, the cache format has changed
        and existing caches will be invalidated!

        Pre-computed:
        >>> content_hash = "e33cdf9c7f71"  # From content_hash golden test
        >>> caps_str = "read,write"  # Default capabilities, sorted
        >>> combined = f"{content_hash}|caps:{caps_str}"
        >>> hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
        '9c4f5a693900a59e'
        """
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        expected = "9c4f5a693900a59e"
        assert ws.state_hash() == expected


# =============================================================================
# Tests: base_commit Validation
# =============================================================================


class TestBaseCommitValidation:
    """Tests for base_commit validation."""

    def test_rejects_head(self) -> None:
        """base_commit must be a full SHA, not 'HEAD'."""
        with pytest.raises(ValueError, match="must be a full 40-char SHA"):
            WorkspaceRef(path="/repo", base_commit="HEAD")

    def test_rejects_short_sha(self) -> None:
        """base_commit must be a full SHA, not abbreviated."""
        with pytest.raises(ValueError, match="must be a full 40-char SHA"):
            WorkspaceRef(path="/repo", base_commit="abc123")

    def test_rejects_branch_name(self) -> None:
        """base_commit must be a full SHA, not a branch name."""
        with pytest.raises(ValueError, match="must be a full 40-char SHA"):
            WorkspaceRef(path="/repo", base_commit="main")

    def test_accepts_valid_sha(self, sample_commit: str) -> None:
        """Valid 40-char SHA is accepted."""
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        assert ws.base_commit == sample_commit

    def test_rejects_uppercase_sha(self) -> None:
        """SHA must be lowercase hex."""
        with pytest.raises(ValueError, match="must be a full 40-char SHA"):
            WorkspaceRef(path="/repo", base_commit="A" * 40)


# =============================================================================
# Tests: Path Coercion
# =============================================================================


class TestPathCoercion:
    """Tests for Path object coercion."""

    def test_path_object_accepted(self, sample_commit: str) -> None:
        """Path objects are coerced to str via @field_validator(mode='before')."""
        ws = WorkspaceRef(path=Path("/test/repo"), base_commit=sample_commit)
        assert ws.path == "/test/repo"
        assert isinstance(ws.path, str)

    def test_string_path_unchanged(self, sample_commit: str) -> None:
        """String paths are passed through unchanged."""
        ws = WorkspaceRef(path="/test/repo", base_commit=sample_commit)
        assert ws.path == "/test/repo"


# =============================================================================
# Tests: Serialization
# =============================================================================


class TestSerialization:
    """Tests for serialization behavior."""

    def test_content_hash_in_model_dump(self, sample_commit: str) -> None:
        """content_hash is included in model_dump() via @computed_field."""
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        data = ws.model_dump()

        assert "content_hash" in data
        assert data["content_hash"] == ws.content_hash

    def test_serialization_roundtrip_preserves_hash(self, sample_commit: str) -> None:
        """Verify content_hash matches after serialization round-trip.

        Note: content_hash is a @computed_field included in model_dump().
        However, since we use extra="forbid", we must exclude it when
        round-tripping (it's recomputed on deserialization anyway).
        """
        patch = DiffPatch(patch="some diff", files_changed=("file.txt",))
        ws1 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(patch,),
        )

        # Round-trip through dict (excluding computed field for deserialization)
        data = ws1.model_dump(exclude={"content_hash"})
        ws2 = WorkspaceRef.model_validate(data)

        # content_hash must match (same inputs → same hash)
        assert ws2.content_hash == ws1.content_hash

    def test_content_hash_included_in_dump_for_debugging(self, sample_commit: str) -> None:
        """content_hash IS included in model_dump() for debugging/inspection."""
        ws = WorkspaceRef(path="/repo", base_commit=sample_commit)
        data = ws.model_dump()

        # Verify it's included (useful for logging, debugging)
        assert "content_hash" in data
        assert data["content_hash"] == ws.content_hash

    def test_extra_fields_rejected(self, sample_commit: str) -> None:
        """Unknown fields are rejected due to extra='forbid'."""
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            WorkspaceRef(
                path="/repo",
                base_commit=sample_commit,
                unknown_field="value",  # type: ignore
            )

    def test_from_serialized_roundtrip(self, sample_commit: str) -> None:
        """from_serialized() handles model_dump() output correctly."""
        patch = DiffPatch(patch="some diff", files_changed=("file.txt",))
        ws1 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(patch,),
            frozen_context_id="test-id",
        )

        # Round-trip using from_serialized (handles content_hash automatically)
        data = ws1.model_dump()
        assert "content_hash" in data  # Verify it's included in dump

        ws2 = WorkspaceRef.from_serialized(data)

        # All fields preserved
        assert ws2.path == ws1.path
        assert ws2.base_commit == ws1.base_commit
        assert ws2.pending_patches == ws1.pending_patches
        assert ws2.frozen_context_id == ws1.frozen_context_id
        assert ws2.content_hash == ws1.content_hash

    def test_from_serialized_without_content_hash(self, sample_commit: str) -> None:
        """from_serialized() works even if content_hash is already excluded."""
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)

        # Manually exclude content_hash (already excluded scenario)
        data = ws1.model_dump(exclude={"content_hash"})
        assert "content_hash" not in data

        ws2 = WorkspaceRef.from_serialized(data)
        assert ws2.content_hash == ws1.content_hash

    def test_model_validate_rejects_content_hash(self, sample_commit: str) -> None:
        """Verify model_validate() rejects content_hash (extra='forbid').

        This test documents why from_serialized() is needed.
        """
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        data = ws1.model_dump()  # Includes content_hash

        # Direct model_validate fails due to extra="forbid"
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            WorkspaceRef.model_validate(data)


# =============================================================================
# Tests: from_path() Error Handling
# =============================================================================


class TestFromPathErrorHandling:
    """Tests for from_path() error cases."""

    def test_non_git_directory_raises(self, tmp_path: Path) -> None:
        """from_path() raises ValueError for non-git directories."""
        with pytest.raises(ValueError, match="Cannot resolve HEAD"):
            WorkspaceRef.from_path(tmp_path)

    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        """from_path() raises ValueError for non-existent paths."""
        nonexistent = tmp_path / "does_not_exist"
        with pytest.raises(ValueError, match="does not exist"):
            WorkspaceRef.from_path(nonexistent)

    def test_valid_git_repo_works(self, git_repo: Path) -> None:
        """from_path() succeeds for valid git repos."""
        ws = WorkspaceRef.from_path(git_repo)
        assert len(ws.base_commit) == 40
        assert ws.frozen_context_id is not None
