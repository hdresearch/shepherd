"""Integration tests for WorkspaceRef cache key computation.

These tests verify the critical cross-sandbox cache hit behavior:
- Two workspaces with identical content at different paths produce the same cache key
- Effect replay produces identical cache keys
- Capability changes produce different cache keys

This addresses Phase 1.5 item #21 from DESIGN-content-hash-implementation.md:
"Verify cache system correctly calls state_hash() (not falling back to context_id)"
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from shepherd_contexts.workspace import WorkspaceRef
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import DiffPatch
from shepherd_runtime.cache import CachePolicy, ExecutionKey
from shepherd_runtime.scope import Scope
from shepherd_runtime.task.metadata import FieldInfo, TaskMetadata
from shepherd_tests import MockProvider

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_commit() -> str:
    """A valid 40-char SHA for unit tests that don't need a real repo."""
    return "a" * 40


@pytest.fixture
def sample_patch() -> DiffPatch:
    """A sample patch for testing."""
    return DiffPatch(
        patch="diff --git a/file.txt b/file.txt\n+new line",
        files_changed=("file.txt",),
    )


@pytest.fixture
def task_metadata() -> TaskMetadata:
    """Create test task metadata with a workspace context field."""
    return TaskMetadata(
        name="TestTask",
        docstring="A test task",
        inputs={"prompt": FieldInfo(name="prompt", inner_type=str, marker_type="input")},
        outputs={"result": FieldInfo(name="result", inner_type=str, marker_type="output")},
        contexts={"workspace": FieldInfo(name="workspace", inner_type=WorkspaceRef, marker_type="context")},
    )


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
        sample_commit: str,
        task_metadata: TaskMetadata,
    ) -> None:
        """Two workspaces with same content at different paths should produce
        the same cache key, enabling cross-sandbox cache hits.
        """
        # Create two workspaces with identical content but different paths
        ws1 = WorkspaceRef(path="/tmp/sandbox-1/repo", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/tmp/sandbox-2/repo", base_commit=sample_commit)

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

    def test_same_content_with_patches_different_path_same_cache_key(
        self,
        sample_commit: str,
        sample_patch: DiffPatch,
        task_metadata: TaskMetadata,
    ) -> None:
        """Workspaces with same patches at different paths should produce
        same cache key.
        """
        ws1 = WorkspaceRef(
            path="/tmp/sandbox-1/repo",
            base_commit=sample_commit,
            pending_patches=(sample_patch,),
        )
        ws2 = WorkspaceRef(
            path="/tmp/sandbox-2/repo",
            base_commit=sample_commit,
            pending_patches=(sample_patch,),
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

    def test_different_content_different_cache_key(
        self,
        sample_commit: str,
        sample_patch: DiffPatch,
        task_metadata: TaskMetadata,
    ) -> None:
        """Different content should produce different cache keys."""
        ws1 = WorkspaceRef(path="/tmp/sandbox/repo", base_commit=sample_commit)
        ws2 = WorkspaceRef(
            path="/tmp/sandbox/repo",
            base_commit=sample_commit,
            pending_patches=(sample_patch,),
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

    def test_different_capabilities_different_cache_key(
        self,
        sample_commit: str,
        task_metadata: TaskMetadata,
    ) -> None:
        """Read-only vs read-write should produce different cache keys."""
        ws_readonly = WorkspaceRef(
            path="/tmp/sandbox/repo",
            base_commit=sample_commit,
            capabilities=frozenset({"read"}),
        )
        ws_readwrite = WorkspaceRef(
            path="/tmp/sandbox/repo",
            base_commit=sample_commit,
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

    def test_same_capabilities_same_cache_key(
        self,
        sample_commit: str,
        task_metadata: TaskMetadata,
    ) -> None:
        """Same capabilities at different paths should produce same cache key."""
        ws1 = WorkspaceRef(
            path="/tmp/sandbox-1/repo",
            base_commit=sample_commit,
            capabilities=frozenset({"read", "write", "bash"}),
        )
        ws2 = WorkspaceRef(
            path="/tmp/sandbox-2/repo",
            base_commit=sample_commit,
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

    def test_effect_replay_preserves_cache_key(
        self,
        sample_commit: str,
        task_metadata: TaskMetadata,
    ) -> None:
        """Replaying effects should produce identical cache keys."""
        # Original workspace
        ws_original = WorkspaceRef(
            path="/tmp/sandbox-original/repo",
            base_commit=sample_commit,
        )

        # Create an effect
        patch = DiffPatch(
            patch="diff --git a/new.txt b/new.txt\n+content",
            files_changed=("new.txt",),
        )
        effect = WorkspacePatchCaptured(
            context_id=ws_original.context_id,
            files_changed=("new.txt",),
            patch_hash=patch.sha256 or "",
            patch_size_bytes=len(patch.patch),
            patch=patch,
        )

        # Apply effect to get modified workspace
        ws_after = ws_original.apply_effect(effect)
        assert len(ws_after.pending_patches) == 1

        # Simulate resume: fresh workspace at DIFFERENT path
        ws_fresh = WorkspaceRef(
            path="/tmp/sandbox-resumed/repo",  # Different path!
            base_commit=sample_commit,
        )

        # Replay the same effect
        ws_replayed = ws_fresh.apply_effect(effect)
        assert len(ws_replayed.pending_patches) == 1

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

    def test_multiple_effects_replay_preserves_cache_key(
        self,
        sample_commit: str,
        task_metadata: TaskMetadata,
    ) -> None:
        """Multiple effects replayed should produce same cache key."""
        ws_original = WorkspaceRef(
            path="/tmp/sandbox-1/repo",
            base_commit=sample_commit,
        )

        # Create multiple effects
        effects = []
        for i in range(3):
            patch = DiffPatch(
                patch=f"diff content {i}",
                files_changed=(f"file{i}.txt",),
            )
            effect = WorkspacePatchCaptured(
                context_id=ws_original.context_id,
                files_changed=(f"file{i}.txt",),
                patch_hash=patch.sha256 or "",
                patch_size_bytes=len(patch.patch),
                patch=patch,
            )
            effects.append(effect)

        # Apply all effects to original
        ws_after = ws_original
        for effect in effects:
            ws_after = ws_after.apply_effect(effect)

        # Replay on fresh workspace at different path
        ws_fresh = WorkspaceRef(
            path="/tmp/sandbox-2/repo",
            base_commit=sample_commit,
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
    """Test the fallback to context_id when state_hash is not available.

    These tests verify that the cache system correctly falls back to
    context_id for contexts without state_hash(), and that WorkspaceRef
    does NOT use this fallback (it has state_hash).
    """

    def test_workspace_uses_state_hash_not_context_id(
        self,
        sample_commit: str,
        task_metadata: TaskMetadata,
    ) -> None:
        """Verify WorkspaceRef uses state_hash, not context_id fallback.

        If the cache system fell back to context_id, these would produce
        different cache keys. They should produce the same key.
        """
        ws1 = WorkspaceRef(
            path="/sandbox-1/repo",
            base_commit=sample_commit,
        )
        ws2 = WorkspaceRef(
            path="/sandbox-2/repo",
            base_commit=sample_commit,
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
# Tests: Real Git Repository
# =============================================================================


class TestRealGitRepository:
    """Integration tests with actual git repositories."""

    def test_from_path_same_repo_different_instances(
        self,
        git_repo: Path,
        task_metadata: TaskMetadata,
    ) -> None:
        """Multiple from_path calls on same repo produce same cache key."""
        ws1 = WorkspaceRef.from_path(str(git_repo))
        ws2 = WorkspaceRef.from_path(str(git_repo))

        # Should have same base_commit (same repo)
        assert ws1.base_commit == ws2.base_commit
        assert ws1.content_hash == ws2.content_hash
        assert ws1.state_hash() == ws2.state_hash()

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
# Tests: Cache Policy Interaction
# =============================================================================


class TestCachePolicyInteraction:
    """Test how cache policies interact with state_hash."""

    def test_inputs_only_policy_ignores_workspace_state(
        self,
        sample_commit: str,
        sample_patch: DiffPatch,
        task_metadata: TaskMetadata,
    ) -> None:
        """INPUTS_ONLY policy should ignore workspace state entirely."""
        ws1 = WorkspaceRef(path="/repo", base_commit=sample_commit)
        ws2 = WorkspaceRef(
            path="/repo",
            base_commit=sample_commit,
            pending_patches=(sample_patch,),
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
        sample_commit: str,
        task_metadata: TaskMetadata,
    ) -> None:
        """Both STRICT and RELAXED policies should use state_hash."""
        ws1 = WorkspaceRef(path="/sandbox-1/repo", base_commit=sample_commit)
        ws2 = WorkspaceRef(path="/sandbox-2/repo", base_commit=sample_commit)

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
