"""Tests for context routing contract.

These tests verify that contexts trust scope-level routing and do NOT re-validate
context_id internally. This is a prerequisite for cache effect replay, where the
context_id may differ between original execution and replay.

The contract:
- Scope routes effects by binding_name (stable) or context_id (semantic)
- Once an effect reaches a context, the context MUST process it
- Contexts should NOT reject effects due to context_id mismatch

See DESIGN-effect-replay.md for full details.
"""

from shepherd_contexts.kvstore.effects import KeyDeleted, KeySet
from shepherd_contexts.kvstore.store import KVStoreContext
from shepherd_contexts.simple_workspace.context import SimpleWorkspace
from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
from shepherd_contexts.simple_workspace.effects import SimpleWorkspaceChangesetCaptured
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_contexts.workspace.ref import WorkspaceRef
from shepherd_core.effects import DiffPatch


class TestWorkspaceRefRoutingContract:
    """Verify WorkspaceRef trusts scope routing (accepts mismatched context_id)."""

    def test_processes_effect_with_different_context_id(self):
        """WorkspaceRef should process effect even if context_id differs.

        This is the core prerequisite for cache replay: the cached effect
        has the original sandbox's context_id, but we're replaying into
        a different sandbox with a different context_id.
        """
        # Create workspace with one context_id
        workspace = WorkspaceRef(
            path="/repo",
            base_commit="a" * 40,
        )
        original_context_id = workspace.context_id

        # Create effect with DIFFERENT context_id (simulating cache replay)
        patch = DiffPatch(
            patch="diff --git a/file.txt b/file.txt\n+new line",
            files_changed=("file.txt",),
        )
        effect = WorkspacePatchCaptured(
            context_id="workspace:/different/sandbox:bbbbbbbb",  # Different!
            binding_name="workspace",
            patch=patch,
            files_changed=("file.txt",),
            patch_hash=patch.sha256 or "",
            patch_size_bytes=len(patch.patch),
        )

        # Effect should be processed despite context_id mismatch
        new_workspace = workspace.apply_effect(effect)

        assert len(new_workspace.pending_patches) == 1
        assert new_workspace.pending_patches[0] == patch
        # Context ID should remain unchanged
        assert new_workspace.context_id == original_context_id

    def test_processes_effect_with_matching_context_id(self):
        """WorkspaceRef should still work with matching context_id."""
        workspace = WorkspaceRef(
            path="/repo",
            base_commit="a" * 40,
        )

        patch = DiffPatch(
            patch="diff content",
            files_changed=("file.txt",),
        )
        effect = WorkspacePatchCaptured(
            context_id=workspace.context_id,  # Matching
            binding_name="workspace",
            patch=patch,
            files_changed=("file.txt",),
            patch_hash=patch.sha256 or "",
            patch_size_bytes=len(patch.patch),
        )

        new_workspace = workspace.apply_effect(effect)
        assert len(new_workspace.pending_patches) == 1

    def test_ignores_unrelated_effect_types(self):
        """WorkspaceRef should ignore effects it doesn't handle."""
        workspace = WorkspaceRef(
            path="/repo",
            base_commit="a" * 40,
        )

        # Apply an unrelated effect type
        effect = KeySet(key="foo", new_value="bar", context_id="kvstore:test")

        new_workspace = workspace.apply_effect(effect)
        assert new_workspace is workspace  # Unchanged


class TestKVStoreContextRoutingContract:
    """Verify KVStoreContext trusts scope routing (accepts mismatched context_id)."""

    def test_key_set_with_different_context_id(self):
        """KVStoreContext should process KeySet even if context_id differs."""
        store = KVStoreContext(data={"existing": "value"})

        # Create effect with different context_id
        effect = KeySet(
            key="new_key",
            new_value="new_value",
            context_id="kvstore:different_id",  # Different!
        )

        new_store = store.apply_effect(effect)

        # Key point: effect was processed despite mismatched context_id
        assert new_store.data["new_key"] == "new_value"
        assert new_store.data["existing"] == "value"
        # Note: context_id may change since it's computed from data hash
        # (unless frozen_context_id is set). That's expected behavior.

    def test_key_deleted_with_different_context_id(self):
        """KVStoreContext should process KeyDeleted even if context_id differs."""
        store = KVStoreContext(data={"to_delete": "value", "keep": "this"})

        effect = KeyDeleted(
            key="to_delete",
            context_id="kvstore:different_id",  # Different!
        )

        new_store = store.apply_effect(effect)

        assert "to_delete" not in new_store.data
        assert new_store.data["keep"] == "this"

    def test_processes_effect_with_matching_context_id(self):
        """KVStoreContext should still work with matching context_id."""
        store = KVStoreContext(data={})

        effect = KeySet(
            key="test",
            new_value="test_value",  # Must be string
            context_id=store.context_id,  # Matching
        )

        new_store = store.apply_effect(effect)
        assert new_store.data["test"] == "test_value"


class TestSimpleWorkspaceRoutingContract:
    """Verify SimpleWorkspace trusts scope routing (accepts mismatched context_id)."""

    def test_changeset_with_different_context_id(self):
        """SimpleWorkspace should process changeset even if context_id differs."""
        workspace = SimpleWorkspace(path="/workspace")
        original_context_id = workspace.context_id

        # Create changeset effect with different context_id
        changeset = FileChangeset(
            deltas=(
                FileDelta(
                    path="test.txt",
                    operation="create",
                    content=b"hello",
                    encoding="full",
                ),
            ),
        )
        effect = SimpleWorkspaceChangesetCaptured(
            context_id="simple_workspace:/different/path",  # Different!
            changeset=changeset,
            files_changed=("test.txt",),
        )

        new_workspace = workspace.apply_effect(effect)

        assert len(new_workspace.pending_changesets) == 1
        assert new_workspace.pending_changesets[0] == changeset
        assert new_workspace.context_id == original_context_id

    def test_processes_effect_with_matching_context_id(self):
        """SimpleWorkspace should still work with matching context_id."""
        workspace = SimpleWorkspace(path="/workspace")

        changeset = FileChangeset(
            deltas=(
                FileDelta(
                    path="test.txt",
                    operation="create",
                    content=b"hello",
                    encoding="full",
                ),
            ),
        )
        effect = SimpleWorkspaceChangesetCaptured(
            context_id=workspace.context_id,  # Matching
            changeset=changeset,
            files_changed=("test.txt",),
        )

        new_workspace = workspace.apply_effect(effect)
        assert len(new_workspace.pending_changesets) == 1

    def test_ignores_empty_changeset(self):
        """SimpleWorkspace should ignore effects with empty changesets."""
        workspace = SimpleWorkspace(path="/workspace")

        effect = SimpleWorkspaceChangesetCaptured(
            context_id="simple_workspace:/any",
            changeset=None,
            files_changed=(),
        )

        new_workspace = workspace.apply_effect(effect)
        assert new_workspace is workspace  # Unchanged


class TestSessionStateRoutingContract:
    """Verify SessionState already follows the routing contract.

    SessionState is the reference implementation - it explicitly documents
    that it trusts scope routing. These tests verify that behavior is preserved.
    """

    def test_session_created_with_different_context_id(self):
        """SessionState should process SessionCreated regardless of context_id."""
        from shepherd_contexts.session.effects import SessionCreated
        from shepherd_contexts.session.state import SessionState

        session = SessionState()
        original_context_id = session.context_id

        effect = SessionCreated(
            session_id="sess_new_123",
            transcript_path="/path/to/transcript",
            context_id="session:different_id",  # Different!
        )

        new_session = session.apply_effect(effect)

        assert new_session.session_id == "sess_new_123"
        assert new_session.transcript_path == "/path/to/transcript"
        # context_id changes because it's derived from session_id
        assert new_session.context_id != original_context_id
