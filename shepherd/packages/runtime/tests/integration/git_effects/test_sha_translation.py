"""Tests for SHA Translation (Spike E2 Validation).

These tests validate the MaterializationContext SHA translation mechanism.
All tests should PASS - this is self-contained logic.

Design Decisions Validated:
- D3: Accept that replayed commits have different SHAs
- D12: Maintain sha_map during materialization for parent SHA translation
"""

from __future__ import annotations

from .fixtures import MaterializationContext


class TestMaterializationContextBasics:
    """Basic MaterializationContext functionality."""

    def test_empty_context_has_no_mappings(self) -> None:
        """New context has empty sha_map."""
        ctx = MaterializationContext()
        assert len(ctx.sha_map) == 0

    def test_record_commit_adds_mapping(self) -> None:
        """record_commit adds to sha_map."""
        ctx = MaterializationContext()
        ctx.record_commit(original="abc123", materialized="xyz789")

        assert "abc123" in ctx.sha_map
        assert ctx.sha_map["abc123"] == "xyz789"

    def test_translate_known_sha(self) -> None:
        """translate_sha returns mapped value for known SHA."""
        ctx = MaterializationContext()
        ctx.record_commit("abc123", "xyz789")

        assert ctx.translate_sha("abc123") == "xyz789"

    def test_translate_unknown_sha_passthrough(self) -> None:
        """translate_sha passes through unknown SHAs unchanged.

        This is important for base commits that already exist in the repo.
        """
        ctx = MaterializationContext()

        # Unknown SHA passes through
        unknown = "0123456789abcdef0123456789abcdef01234567"
        assert ctx.translate_sha(unknown) == unknown

    def test_multiple_commits(self) -> None:
        """Can track multiple commit translations."""
        ctx = MaterializationContext()

        ctx.record_commit("commit1", "new1")
        ctx.record_commit("commit2", "new2")
        ctx.record_commit("commit3", "new3")

        assert ctx.translate_sha("commit1") == "new1"
        assert ctx.translate_sha("commit2") == "new2"
        assert ctx.translate_sha("commit3") == "new3"


class TestParentShaTranslation:
    """Tests for parent SHA translation (E2 core scenario)."""

    def test_translate_single_parent(self) -> None:
        """Single parent SHA is translated correctly."""
        ctx = MaterializationContext()
        ctx.record_commit("parent_original", "parent_materialized")

        parents = ("parent_original",)
        translated = ctx.translate_parents(parents)

        assert translated == ("parent_materialized",)

    def test_translate_multiple_parents(self) -> None:
        """Merge commits with multiple parents are translated.

        This is the key E2 scenario: when replaying a merge commit,
        both parent references need translation.
        """
        ctx = MaterializationContext()
        ctx.record_commit("parent_a_orig", "parent_a_new")
        ctx.record_commit("parent_b_orig", "parent_b_new")

        parents = ("parent_a_orig", "parent_b_orig")
        translated = ctx.translate_parents(parents)

        assert translated == ("parent_a_new", "parent_b_new")

    def test_translate_mixed_parents(self) -> None:
        """Mix of known and unknown parents.

        This happens when merging a new branch into an existing base.
        The base commit exists in the repo (unknown), but the feature
        branch commit was created in sandbox (known).
        """
        ctx = MaterializationContext()
        ctx.record_commit("feature_commit", "feature_new")

        # First parent is existing base (unknown), second is feature (known)
        parents = ("existing_base_abc123", "feature_commit")
        translated = ctx.translate_parents(parents)

        assert translated == ("existing_base_abc123", "feature_new")


class TestReplayScenario:
    """End-to-end replay scenario from E2 spike."""

    def test_commit_chain_replay(self) -> None:
        """Simulate replaying a chain of commits.

        Original execution:
        1. base123 (exists in repo)
        2. abc123 created (parent: base123)
        3. def456 created (parent: abc123)

        Replay:
        1. base123 exists
        2. Create commit → xyz789 (parent: base123)
        3. Create commit → uvw012 (parent should be xyz789, not abc123!)
        """
        ctx = MaterializationContext()

        # Simulate replaying first new commit
        # Original SHA was abc123, but replay creates xyz789
        ctx.record_commit("abc123", "xyz789")

        # Now replaying second commit - it references abc123 as parent
        # We need to translate to xyz789
        second_commit_parents = ("abc123",)
        translated = ctx.translate_parents(second_commit_parents)

        assert translated == ("xyz789",)

        # Record the second commit's translation too
        ctx.record_commit("def456", "uvw012")

        # Verify full chain
        assert ctx.translate_sha("abc123") == "xyz789"
        assert ctx.translate_sha("def456") == "uvw012"

    def test_base_commit_not_translated(self) -> None:
        """Base commits that exist in repo are not translated.

        D3: We accept different SHAs for NEW commits, but existing
        commits (like base_commit) should not be translated.
        """
        ctx = MaterializationContext()

        base_commit = "existing_base_commit_sha_40_chars_long"

        # First commit's parent is the base (not translated)
        parents = (base_commit,)
        translated = ctx.translate_parents(parents)

        assert translated == (base_commit,)  # Unchanged


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_parents(self) -> None:
        """Empty parent tuple (root commit) works."""
        ctx = MaterializationContext()

        translated = ctx.translate_parents(())
        assert translated == ()

    def test_overwrite_mapping(self) -> None:
        """Recording same original SHA twice overwrites."""
        ctx = MaterializationContext()

        ctx.record_commit("abc123", "first")
        ctx.record_commit("abc123", "second")

        assert ctx.translate_sha("abc123") == "second"

    def test_sha_map_is_mutable(self) -> None:
        """sha_map can be modified directly if needed."""
        ctx = MaterializationContext()

        ctx.sha_map["direct"] = "access"
        assert ctx.translate_sha("direct") == "access"
