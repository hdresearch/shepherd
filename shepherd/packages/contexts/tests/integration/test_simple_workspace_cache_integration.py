"""Integration tests for SimpleWorkspace cache key computation.

These tests verify the critical cross-sandbox cache hit behavior:
- Two workspaces with identical content at different paths produce the same cache key
- Effect replay produces identical cache keys
- Capability changes produce different cache keys

This addresses Phase 5 items from DESIGN-simpleworkspace-content-hash.md:
"Verify cache system correctly calls state_hash() (not falling back to context_id)"
"""

from __future__ import annotations

from datetime import datetime

import pytest
from shepherd_contexts.simple_workspace import SimpleWorkspace
from shepherd_contexts.simple_workspace.delta import FileChangeset, FileDelta
from shepherd_contexts.simple_workspace.effects import SimpleWorkspaceChangesetCaptured
from shepherd_contexts.simple_workspace.manifest import FileEntry, FileManifest
from shepherd_runtime.cache import CachePolicy, ExecutionKey
from shepherd_runtime.scope import Scope
from shepherd_runtime.task.metadata import FieldInfo, TaskMetadata
from shepherd_tests import MockProvider

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_manifest() -> FileManifest:
    """A manifest with content hashes for testing."""
    return FileManifest(
        entries=(
            FileEntry(
                path="file.txt",
                size_bytes=100,
                mtime_ns=123456789,
                content_hash="a" * 64,
            ),
            FileEntry(
                path="src/main.py",
                size_bytes=500,
                mtime_ns=123456790,
                content_hash="b" * 64,
            ),
        ),
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


@pytest.fixture
def sample_changeset() -> FileChangeset:
    """A changeset for testing."""
    return FileChangeset(
        deltas=(
            FileDelta(
                path="new.txt",
                operation="create",
                new_content_hash="c" * 64,
                new_size_bytes=50,
            ),
        ),
        created_at=datetime(2024, 1, 1, 12, 30, 0),
    )


@pytest.fixture
def task_metadata() -> TaskMetadata:
    """Task metadata with SimpleWorkspace context."""
    return TaskMetadata(
        name="TestTask",
        docstring="A test task",
        inputs={"prompt": FieldInfo(name="prompt", inner_type=str, marker_type="input")},
        outputs={"result": FieldInfo(name="result", inner_type=str, marker_type="output")},
        contexts={"workspace": FieldInfo(name="workspace", inner_type=SimpleWorkspace, marker_type="context")},
    )


# =============================================================================
# Tests: Cross-Sandbox Cache Hit
# =============================================================================


class TestCrossSandboxCacheHit:
    """Test that identical content at different paths produces same cache key.

    This is the core value proposition of state_hash(): enabling cache hits
    when the same logical workspace content exists at different filesystem
    locations (e.g., different sandbox directories).
    """

    def test_same_content_different_path_same_cache_key(
        self,
        sample_manifest: FileManifest,
        task_metadata: TaskMetadata,
    ) -> None:
        """Two workspaces with same content at different paths should produce
        the same cache key, enabling cross-sandbox cache hits.
        """
        # Create two workspaces with identical content but different paths
        ws1 = SimpleWorkspace(
            path="/tmp/sandbox-1/workspace",
            base_manifest=sample_manifest,
        )
        ws2 = SimpleWorkspace(
            path="/tmp/sandbox-2/workspace",
            base_manifest=sample_manifest,
        )

        # Verify content identity
        assert ws1.content_hash == ws2.content_hash
        assert ws1.state_hash() == ws2.state_hash()

        # Verify resource identity is different (important for effect routing)
        assert ws1.context_id != ws2.context_id
        assert ws1 != ws2

        # Now verify cache keys are the same
        inputs = {"prompt": "test prompt"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        # Cache keys MUST match for cross-sandbox cache hits
        assert key1.contexts_hash == key2.contexts_hash, (
            "contexts_hash should match for same content at different paths. "
            "This indicates state_hash() is being used correctly."
        )
        assert key1.key == key2.key, "Full cache key should match for same content at different paths."

    def test_same_content_with_changesets_different_path_same_cache_key(
        self,
        sample_manifest: FileManifest,
        sample_changeset: FileChangeset,
        task_metadata: TaskMetadata,
    ) -> None:
        """Workspaces with same changesets at different paths should produce
        same cache key.
        """
        ws1 = SimpleWorkspace(
            path="/tmp/sandbox-1/workspace",
            base_manifest=sample_manifest,
            pending_changesets=(sample_changeset,),
        )
        ws2 = SimpleWorkspace(
            path="/tmp/sandbox-2/workspace",
            base_manifest=sample_manifest,
            pending_changesets=(sample_changeset,),
        )

        # Verify content hashes match
        assert ws1.content_hash == ws2.content_hash

        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.key == key2.key

    def test_different_content_different_cache_key(
        self,
        sample_manifest: FileManifest,
        sample_changeset: FileChangeset,
        task_metadata: TaskMetadata,
    ) -> None:
        """Different content should produce different cache keys."""
        ws1 = SimpleWorkspace(
            path="/tmp/sandbox/workspace",
            base_manifest=sample_manifest,
        )
        ws2 = SimpleWorkspace(
            path="/tmp/sandbox/workspace",
            base_manifest=sample_manifest,
            pending_changesets=(sample_changeset,),
        )

        # Verify content hashes differ
        assert ws1.content_hash != ws2.content_hash

        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.contexts_hash != key2.contexts_hash
        assert key1.key != key2.key


# =============================================================================
# Tests: Capability-Based Cache Differentiation
# =============================================================================


class TestCapabilityCacheDifferentiation:
    """Test that different capabilities produce different cache keys.

    This is important because capabilities affect LLM behavior (e.g.,
    a read-only workspace won't offer write tools).
    """

    def test_different_capabilities_produce_different_cache_keys(
        self,
        sample_manifest: FileManifest,
        task_metadata: TaskMetadata,
    ) -> None:
        """Read-only vs read-write should produce different cache keys."""
        ws_readonly = SimpleWorkspace(
            path="/tmp/sandbox/workspace",
            base_manifest=sample_manifest,
            capabilities=frozenset({"read"}),
        )
        ws_readwrite = SimpleWorkspace(
            path="/tmp/sandbox/workspace",
            base_manifest=sample_manifest,
            capabilities=frozenset({"read", "write"}),
        )

        # content_hash should be the same (capabilities not included)
        assert ws_readonly.content_hash == ws_readwrite.content_hash

        # state_hash should differ (capabilities included)
        assert ws_readonly.state_hash() != ws_readwrite.state_hash()

        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws_readonly)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws_readwrite)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.contexts_hash != key2.contexts_hash, "Different capabilities should produce different contexts_hash"
        assert key1.key != key2.key

    def test_same_capabilities_different_path_same_cache_key(
        self,
        sample_manifest: FileManifest,
        task_metadata: TaskMetadata,
    ) -> None:
        """Same capabilities at different paths should produce same cache key."""
        ws1 = SimpleWorkspace(
            path="/tmp/sandbox-1/workspace",
            base_manifest=sample_manifest,
            capabilities=frozenset({"read", "write", "bash"}),
        )
        ws2 = SimpleWorkspace(
            path="/tmp/sandbox-2/workspace",
            base_manifest=sample_manifest,
            capabilities=frozenset({"read", "write", "bash"}),
        )

        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.key == key2.key


# =============================================================================
# Tests: Effect Replay Cache Consistency
# =============================================================================


class TestEffectReplayCacheConsistency:
    """Test that effect replay produces identical cache keys.

    When resuming from persisted effects, the reconstructed workspace
    should produce the same cache key as the original, enabling cache hits.
    """

    def test_effect_replay_at_different_path_same_cache_key(
        self,
        sample_manifest: FileManifest,
        task_metadata: TaskMetadata,
    ) -> None:
        """Replaying effects should produce identical cache keys."""
        # Original workspace
        ws_original = SimpleWorkspace(
            path="/tmp/sandbox-original/workspace",
            base_manifest=sample_manifest,
        )

        # Create an effect with a changeset
        changeset = FileChangeset(
            deltas=(
                FileDelta(
                    path="new_file.txt",
                    operation="create",
                    new_content_hash="d" * 64,
                    new_size_bytes=200,
                ),
            ),
            created_at=datetime(2024, 1, 1, 13, 0, 0),
        )
        effect = SimpleWorkspaceChangesetCaptured(
            context_id=ws_original.context_id,
            changeset=changeset,
        )

        # Apply effect to get modified workspace
        ws_after = ws_original.apply_effect(effect)
        assert len(ws_after.pending_changesets) == 1

        # Simulate resume: fresh workspace at DIFFERENT path
        ws_fresh = SimpleWorkspace(
            path="/tmp/sandbox-resumed/workspace",  # Different path!
            base_manifest=sample_manifest,
        )

        # Replay the same effect
        ws_replayed = ws_fresh.apply_effect(effect)
        assert len(ws_replayed.pending_changesets) == 1

        # Verify content and state hashes match
        assert ws_replayed.content_hash == ws_after.content_hash
        assert ws_replayed.state_hash() == ws_after.state_hash()

        # Verify cache keys match
        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws_after)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws_replayed)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.key == key2.key, "Effect replay at different path should produce same cache key"

    def test_multiple_effects_replayed_same_cache_key(
        self,
        sample_manifest: FileManifest,
        task_metadata: TaskMetadata,
    ) -> None:
        """Multiple effects replayed should produce same cache key."""
        ws_original = SimpleWorkspace(
            path="/tmp/sandbox-1/workspace",
            base_manifest=sample_manifest,
        )

        # Create multiple effects
        effects = []
        for i in range(3):
            changeset = FileChangeset(
                deltas=(
                    FileDelta(
                        path=f"file{i}.txt",
                        operation="create",
                        new_content_hash=f"{chr(ord('e') + i)}" * 64,
                        new_size_bytes=100 + i * 10,
                    ),
                ),
                created_at=datetime(2024, 1, 1, 14, i, 0),
            )
            effect = SimpleWorkspaceChangesetCaptured(
                context_id=ws_original.context_id,
                changeset=changeset,
            )
            effects.append(effect)

        # Apply all effects to original
        ws_after = ws_original
        for effect in effects:
            ws_after = ws_after.apply_effect(effect)

        # Replay on fresh workspace at different path
        ws_fresh = SimpleWorkspace(
            path="/tmp/sandbox-2/workspace",
            base_manifest=sample_manifest,
        )
        ws_replayed = ws_fresh
        for effect in effects:
            ws_replayed = ws_replayed.apply_effect(effect)

        # Verify cache keys match
        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws_after)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws_replayed)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.key == key2.key


# =============================================================================
# Tests: Fallback Behavior Verification
# =============================================================================


class TestFallbackBehavior:
    """Test that state_hash() is used, not the context_id fallback.

    The cache system in key.py has a duck-typing check:
    - If context has state_hash(), use it
    - Otherwise, fall back to context_id

    These tests verify SimpleWorkspace uses state_hash (not the fallback).
    """

    def test_state_hash_is_used_not_context_id_fallback(
        self,
        sample_manifest: FileManifest,
        task_metadata: TaskMetadata,
    ) -> None:
        """Verify SimpleWorkspace uses state_hash, not context_id fallback.

        If the cache system fell back to context_id, these would produce
        different cache keys. They should produce the same key.
        """
        ws1 = SimpleWorkspace(
            path="/sandbox-1/workspace",
            base_manifest=sample_manifest,
        )
        ws2 = SimpleWorkspace(
            path="/sandbox-2/workspace",
            base_manifest=sample_manifest,
        )

        # context_id includes path, so they differ
        assert ws1.context_id != ws2.context_id, "Precondition: context_ids differ"

        # But cache keys should be the same (uses state_hash, not context_id)
        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        # This assertion proves state_hash is being used
        assert key1.contexts_hash == key2.contexts_hash, (
            "contexts_hash should be equal because state_hash() is used. "
            "If this fails, the cache system may be falling back to context_id."
        )


# =============================================================================
# Tests: Cache Policy Interaction
# =============================================================================


class TestCachePolicyInteraction:
    """Test how cache policies interact with state_hash."""

    def test_inputs_only_policy_ignores_workspace_state(
        self,
        sample_manifest: FileManifest,
        sample_changeset: FileChangeset,
        task_metadata: TaskMetadata,
    ) -> None:
        """INPUTS_ONLY policy should ignore workspace state entirely."""
        ws1 = SimpleWorkspace(
            path="/tmp/workspace",
            base_manifest=sample_manifest,
        )
        ws2 = SimpleWorkspace(
            path="/tmp/workspace",
            base_manifest=sample_manifest,
            pending_changesets=(sample_changeset,),
        )

        # Different content
        assert ws1.content_hash != ws2.content_hash

        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.INPUTS_ONLY)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.INPUTS_ONLY)

        # With INPUTS_ONLY, contexts_hash should be all zeros
        assert key1.contexts_hash == "0" * 16
        assert key2.contexts_hash == "0" * 16
        assert key1.key == key2.key

    def test_strict_and_relaxed_use_state_hash(
        self,
        sample_manifest: FileManifest,
        task_metadata: TaskMetadata,
    ) -> None:
        """Both STRICT and RELAXED policies should use state_hash."""
        ws1 = SimpleWorkspace(
            path="/sandbox-1/workspace",
            base_manifest=sample_manifest,
        )
        ws2 = SimpleWorkspace(
            path="/sandbox-2/workspace",
            base_manifest=sample_manifest,
        )

        inputs = {"prompt": "test"}

        # Test STRICT
        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key_strict_1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key_strict_2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key_strict_1.contexts_hash == key_strict_2.contexts_hash

        # Test RELAXED
        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key_relaxed_1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.RELAXED)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key_relaxed_2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.RELAXED)

        assert key_relaxed_1.contexts_hash == key_relaxed_2.contexts_hash


# =============================================================================
# Tests: Empty Workspace Edge Cases
# =============================================================================


class TestEmptyWorkspaceEdgeCases:
    """Test cache behavior for edge cases with empty workspaces."""

    def test_empty_manifest_same_cache_key_different_paths(
        self,
        task_metadata: TaskMetadata,
    ) -> None:
        """Empty workspaces at different paths should have same cache key."""
        ws1 = SimpleWorkspace(
            path="/tmp/empty-1",
            base_manifest=FileManifest(entries=()),
        )
        ws2 = SimpleWorkspace(
            path="/tmp/empty-2",
            base_manifest=FileManifest(entries=()),
        )

        assert ws1.content_hash == ws2.content_hash

        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.key == key2.key

    def test_none_manifest_same_cache_key_different_paths(
        self,
        task_metadata: TaskMetadata,
    ) -> None:
        """Workspaces with None manifest at different paths should have same cache key."""
        ws1 = SimpleWorkspace(
            path="/tmp/none-1",
            base_manifest=None,
        )
        ws2 = SimpleWorkspace(
            path="/tmp/none-2",
            base_manifest=None,
        )

        assert ws1.content_hash == ws2.content_hash

        inputs = {"prompt": "test"}

        with Scope(root=True) as scope1:
            scope1.register_provider("default", MockProvider(), default=True)
            scope1.bind("workspace", ws1)
            key1 = ExecutionKey.compute(task_metadata, inputs, scope1, CachePolicy.STRICT)

        with Scope(root=True) as scope2:
            scope2.register_provider("default", MockProvider(), default=True)
            scope2.bind("workspace", ws2)
            key2 = ExecutionKey.compute(task_metadata, inputs, scope2, CachePolicy.STRICT)

        assert key1.key == key2.key
