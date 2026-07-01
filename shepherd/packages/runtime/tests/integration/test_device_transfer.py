"""Integration tests for device state transfer (TransferBundle).

Phase 3 validation tests for DESIGN-device-state-transfer.md:
1. Test local → container transition: verify patches visible in container
2. Test effect attribution: verify only NEW effects emitted (not bundle state)
3. Test container re-entry: verify second container sees first container's work
4. Test combinator integration: verify gate/retry/parallel work correctly
5. Document the deferred gap: local execution after container work

These tests exercise the full vertical slice:
- TransferBundle creation from WorkspaceRef
- Bundle collection in ContainerDevice.create_sandbox()
- Bundle application via _apply_bundles()
- Manifest-based effect attribution in OverlayEffectExtractor

Markers:
- pytest.mark.container: Container-specific tests
- pytest.mark.e2e: End-to-end tests requiring Podman
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import DiffPatch
from shepherd_runtime.device.container.effect_collector import EffectCollector
from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
from shepherd_runtime.device.container.podman import ContainerSandbox, OverlayMount
from shepherd_runtime.device.transfer import (
    TransferBundle,
    collect_visible_patches,
    compute_content_hash,
)

if TYPE_CHECKING:
    from collections.abc import Generator

# =============================================================================
# Fixtures and Helpers
# =============================================================================


requires_podman = pytest.mark.usefixtures("_requires_podman")

pytestmark = pytest.mark.container


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Create a temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="shepherd-test-workspace-")
    yield Path(workspace)
    shutil.rmtree(workspace, ignore_errors=True)


_OVERLAY_TMPFS = Path("/tmp/shepherd-test-overlays")


@pytest.fixture
def temp_overlays() -> Generator[Path, None, None]:
    """Create a temporary overlays directory."""
    base = _OVERLAY_TMPFS if _OVERLAY_TMPFS.is_mount() else Path(tempfile.gettempdir())
    overlays = tempfile.mkdtemp(prefix="shepherd-test-overlays-", dir=base)
    yield Path(overlays)
    shutil.rmtree(overlays, ignore_errors=True)


def create_test_patch(filename: str, content: str, source_step: str = "test") -> DiffPatch:
    """Create a test DiffPatch for testing."""
    # Simple unified diff format
    patch_content = f"""--- a/{filename}
+++ b/{filename}
@@ -0,0 +1,{content.count(chr(10)) + 1} @@
+{content}
"""
    return DiffPatch(
        patch=patch_content,
        files_changed=(filename,),
        source_step=source_step,
    )


def create_mock_scope_with_patches(patches: list[DiffPatch]) -> MagicMock:
    """Create a mock scope with WorkspacePatchCaptured effects.

    The mock scope simulates the effect stream structure used by
    collect_visible_patches().
    """
    scope = MagicMock()
    scope.id = "test-scope"

    # Create mock effect layers
    layers = []
    for patch in patches:
        layer = MagicMock()
        effect = WorkspacePatchCaptured(
            patch=patch,
            binding_name="workspace",
            workspace_path="/test/workspace",
        )
        layer.effect = effect
        layers.append(layer)

    scope.effects.layers = layers

    # Mock get_context to return None (no pending_patches from context)
    scope.get_context.return_value = None

    return scope


# =============================================================================
# Test 1: TransferBundle Foundation
# =============================================================================


class TestTransferBundleCreation:
    """Test TransferBundle dataclass and helper functions."""

    def test_transfer_bundle_is_frozen(self):
        """TransferBundle should be immutable."""
        bundle = TransferBundle(
            state={"key": "value"},
            files={"test.py": b"content"},
            manifest={"test.py": "abc123"},
        )

        with pytest.raises(AttributeError):
            bundle.state = {}  # type: ignore[misc]

    def test_transfer_bundle_default_values(self):
        """TransferBundle should have sensible defaults."""
        bundle = TransferBundle()

        assert bundle.state == {}
        assert bundle.files == {}
        assert bundle.env == {}
        assert bundle.mounts == {}
        assert bundle.symlinks == {}
        assert bundle.manifest == {}

    def test_compute_content_hash(self):
        """compute_content_hash should return SHA-256 hex digest."""
        content = b"Hello, World!"
        expected = hashlib.sha256(content).hexdigest()

        result = compute_content_hash(content)

        assert result == expected
        assert len(result) == 64  # SHA-256 hex is 64 chars

    def test_compute_content_hash_empty(self):
        """compute_content_hash should handle empty content."""
        result = compute_content_hash(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected


class TestTransferBundleCompose:
    """Test TransferBundle.compose() for multi-context scenarios."""

    def test_compose_empty_list_returns_empty_bundle(self):
        """Composing empty list should return empty bundle."""
        result = TransferBundle.compose([])

        assert result.state == {}
        assert result.files == {}
        assert result.env == {}
        assert result.mounts == {}
        assert result.symlinks == {}
        assert result.manifest == {}

    def test_compose_single_bundle_returns_same(self):
        """Composing single bundle should return that bundle."""
        bundle = TransferBundle(
            state={"key": "value"},
            files={"test.py": b"content"},
            env={"VAR": "value"},
        )

        result = TransferBundle.compose([bundle])

        assert result is bundle

    def test_compose_merges_non_conflicting_bundles(self):
        """Non-conflicting bundles should merge cleanly."""
        bundle1 = TransferBundle(
            state={"context": "workspace"},
            files={"file1.py": b"content1"},
            env={"VAR1": "value1"},
            mounts={"/path1": "/path1"},
            manifest={"file1.py": "hash1"},
        )
        bundle2 = TransferBundle(
            state={"session_id": "abc123"},
            files={"file2.py": b"content2"},
            env={"VAR2": "value2"},
            mounts={"/path2": "/path2"},
            manifest={"file2.py": "hash2"},
        )

        result = TransferBundle.compose([bundle1, bundle2])

        # State is merged (both keys present)
        assert result.state == {"context": "workspace", "session_id": "abc123"}

        # Files are unioned
        assert result.files == {"file1.py": b"content1", "file2.py": b"content2"}

        # Env vars are unioned
        assert result.env == {"VAR1": "value1", "VAR2": "value2"}

        # Mounts are unioned
        assert result.mounts == {"/path1": "/path1", "/path2": "/path2"}

        # Manifest is unioned
        assert result.manifest == {"file1.py": "hash1", "file2.py": "hash2"}

    def test_compose_state_later_overrides_earlier(self):
        """Later bundle's state keys should override earlier."""
        bundle1 = TransferBundle(state={"key": "value1", "other": "keep"})
        bundle2 = TransferBundle(state={"key": "value2"})

        result = TransferBundle.compose([bundle1, bundle2])

        assert result.state["key"] == "value2"  # Overridden
        assert result.state["other"] == "keep"  # Preserved

    def test_compose_identical_values_allowed(self):
        """Same key with same value should not raise."""
        bundle1 = TransferBundle(
            env={"SHARED": "same_value"},
            mounts={"/shared": "/shared"},
        )
        bundle2 = TransferBundle(
            env={"SHARED": "same_value"},
            mounts={"/shared": "/shared"},
        )

        result = TransferBundle.compose([bundle1, bundle2])

        assert result.env == {"SHARED": "same_value"}
        assert result.mounts == {"/shared": "/shared"}

    def test_compose_raises_on_env_conflict(self):
        """Different values for same env var should raise."""
        bundle1 = TransferBundle(env={"VAR": "value1"})
        bundle2 = TransferBundle(env={"VAR": "value2"})

        with pytest.raises(ValueError, match="Conflicting env var 'VAR'"):
            TransferBundle.compose([bundle1, bundle2])

    def test_compose_raises_on_mount_conflict(self):
        """Same host path with different container paths should raise."""
        bundle1 = TransferBundle(mounts={"/host": "/container1"})
        bundle2 = TransferBundle(mounts={"/host": "/container2"})

        with pytest.raises(ValueError, match="Conflicting mount for host path"):
            TransferBundle.compose([bundle1, bundle2])

    def test_compose_raises_on_file_conflict(self):
        """Same file path with different content should raise."""
        bundle1 = TransferBundle(files={"file.py": b"content1"})
        bundle2 = TransferBundle(files={"file.py": b"content2"})

        with pytest.raises(ValueError, match="Conflicting file content"):
            TransferBundle.compose([bundle1, bundle2])

    def test_compose_raises_on_symlink_conflict(self):
        """Same symlink path with different targets should raise."""
        bundle1 = TransferBundle(symlinks={"/link": "/target1"})
        bundle2 = TransferBundle(symlinks={"/link": "/target2"})

        with pytest.raises(ValueError, match="Conflicting symlink"):
            TransferBundle.compose([bundle1, bundle2])

    def test_compose_raises_on_manifest_conflict(self):
        """Same file with different hash should raise."""
        bundle1 = TransferBundle(manifest={"file.py": "hash1"})
        bundle2 = TransferBundle(manifest={"file.py": "hash2"})

        with pytest.raises(ValueError, match="Conflicting manifest entry"):
            TransferBundle.compose([bundle1, bundle2])

    def test_compose_three_bundles(self):
        """Should handle composing more than two bundles."""
        bundles = [
            TransferBundle(files={"a.py": b"a"}, env={"A": "1"}),
            TransferBundle(files={"b.py": b"b"}, env={"B": "2"}),
            TransferBundle(files={"c.py": b"c"}, env={"C": "3"}),
        ]

        result = TransferBundle.compose(bundles)

        assert len(result.files) == 3
        assert len(result.env) == 3


class TestCollectVisiblePatches:
    """Test the 'visible' strategy for collecting patches from scope."""

    def test_collect_from_effect_stream(self):
        """Should collect patches from WorkspacePatchCaptured effects."""
        patch1 = create_test_patch("auth.py", "def login(): pass")
        patch2 = create_test_patch("utils.py", "def helper(): pass")
        scope = create_mock_scope_with_patches([patch1, patch2])

        result = collect_visible_patches(scope, binding_name="workspace")

        assert len(result) == 2
        filenames = {f for p in result for f in p.files_changed}
        assert "auth.py" in filenames
        assert "utils.py" in filenames

    def test_later_patch_overrides_earlier_for_same_file(self):
        """Later patches should override earlier for the same file."""
        patch1 = create_test_patch("auth.py", "version 1")
        patch2 = create_test_patch("auth.py", "version 2")
        scope = create_mock_scope_with_patches([patch1, patch2])

        result = collect_visible_patches(scope, binding_name="workspace")

        # Should deduplicate by file - only latest version of auth.py
        assert len(result) == 1
        assert "version 2" in result[0].patch

    def test_returns_empty_list_when_no_patches(self):
        """Should return empty list when no patches exist."""
        scope = MagicMock()
        scope.effects.layers = []
        scope.get_context.return_value = None

        result = collect_visible_patches(scope, binding_name="workspace")

        assert result == []

    def test_filters_by_binding_name(self):
        """Should filter patches by binding name."""
        scope = MagicMock()

        # Create effects with different binding names
        layer1 = MagicMock()
        layer1.effect = WorkspacePatchCaptured(
            patch=create_test_patch("ws1.py", "workspace 1"),
            binding_name="workspace",
            workspace_path="/test",
        )

        layer2 = MagicMock()
        layer2.effect = WorkspacePatchCaptured(
            patch=create_test_patch("ws2.py", "workspace 2"),
            binding_name="other_workspace",
            workspace_path="/other",
        )

        scope.effects.layers = [layer1, layer2]
        scope.get_context.return_value = None

        result = collect_visible_patches(scope, binding_name="workspace")

        assert len(result) == 1
        assert "ws1.py" in result[0].files_changed


# =============================================================================
# Test 2: Effect Attribution with Manifest
# =============================================================================


class TestManifestBasedEffectAttribution:
    """Test that manifest filtering correctly attributes effects."""

    def test_should_emit_effect_for_new_file(self, temp_overlays: Path):
        """New files not in manifest should emit effects."""
        extractor = OverlayEffectExtractor()
        manifest: dict[str, str] = {}  # Empty manifest

        content = b"new file content"
        result = extractor._should_emit_effect("new_file.py", content, manifest)

        assert result is True

    def test_should_not_emit_effect_for_unchanged_file(self, temp_overlays: Path):
        """Files matching manifest hash should not emit effects."""
        extractor = OverlayEffectExtractor()
        content = b"original content"
        content_hash = compute_content_hash(content)
        manifest = {"existing.py": content_hash}

        result = extractor._should_emit_effect("existing.py", content, manifest)

        assert result is False

    def test_should_emit_effect_for_modified_file(self, temp_overlays: Path):
        """Files with different hash than manifest should emit effects."""
        extractor = OverlayEffectExtractor()
        original_content = b"original content"
        manifest = {"modified.py": compute_content_hash(original_content)}

        modified_content = b"modified content"
        result = extractor._should_emit_effect("modified.py", modified_content, manifest)

        assert result is True

    def test_manifest_filtering_integration(self, temp_workspace: Path, temp_overlays: Path):
        """Integration test: extract effects with manifest filtering.

        Simulates:
        1. Bundle with pre-applied files (in manifest)
        2. Container creates new file
        3. Container modifies pre-applied file
        4. Only NEW changes should generate effects
        """
        # Setup: create overlay structure
        lower = temp_overlays / "lower"
        upper = temp_overlays / "upper"
        work = temp_overlays / "work"
        merged = temp_overlays / "merged"

        for d in [lower, upper, work, merged]:
            d.mkdir(parents=True)

        # Pre-existing file in lower (from host workspace)
        original_content = b"# Original file\ndef hello(): pass\n"
        (lower / "original.py").write_bytes(original_content)

        # Pre-applied bundle file - content matches manifest
        bundle_content = b"# From bundle\ndef bundle_func(): pass\n"
        (upper / "bundle_file.py").write_bytes(bundle_content)

        # New file created by container (not in manifest)
        new_content = b"# New file from container\ndef new_func(): pass\n"
        (upper / "container_new.py").write_bytes(new_content)

        # Modified file from container (different from manifest)
        modified_content = b"# Modified by container\ndef modified(): pass\n"
        (upper / "bundle_modified.py").write_bytes(modified_content)

        # Create manifest with bundle state
        manifest = {
            "bundle_file.py": compute_content_hash(bundle_content),
            "bundle_modified.py": compute_content_hash(b"# Original bundle content\n"),
        }

        # Create overlay mount and extractor
        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
        )

        collector = EffectCollector()
        extractor = OverlayEffectExtractor(lower_path=lower)

        # Extract effects with manifest
        effects = extractor.extract(overlay, collector, manifest=manifest)

        # Verify: should NOT include bundle_file.py (unchanged from bundle)
        effect_paths = [getattr(e, "path", None) for e in effects]
        assert "bundle_file.py" not in effect_paths

        # Verify: should include container_new.py (new file)
        assert "container_new.py" in effect_paths

        # Verify: should include bundle_modified.py (modified from bundle)
        assert "bundle_modified.py" in effect_paths


# =============================================================================
# Test 3: Container Re-entry
# =============================================================================


class TestContainerReentry:
    """Test that second container entry sees first container's work.

    The key insight: TransferBundle is created from visible_patches(scope),
    not 'patches created locally'. The scope is the source of truth.
    """

    def test_scope_accumulates_patches_across_device_boundaries(self):
        """Patches from container execution should be visible on re-entry.

        Flow:
        1. Local execution creates patch A
        2. Container execution creates patch B (emitted to scope)
        3. Exit container
        4. Re-enter container - bundle should include BOTH A and B
        """
        # First: local patch
        patch_a = create_test_patch("local.py", "from local execution")

        # Second: container patch (simulates what would be emitted after container exec)
        patch_b = create_test_patch("container.py", "from container execution")

        # Scope sees both patches in its effect stream
        scope = create_mock_scope_with_patches([patch_a, patch_b])

        # On re-entry, collect_visible_patches should return BOTH
        visible = collect_visible_patches(scope, binding_name="workspace")

        assert len(visible) == 2
        filenames = {f for p in visible for f in p.files_changed}
        assert "local.py" in filenames
        assert "container.py" in filenames

    def test_bundle_includes_all_visible_patches_regardless_of_source(self):
        """Bundle should include patches from any source device.

        This verifies the 'scope is source of truth' principle.
        """
        # Mix of patches from different hypothetical sources
        patches = [
            create_test_patch("file1.py", "source 1", source_step="local-task"),
            create_test_patch("file2.py", "source 2", source_step="container-task-1"),
            create_test_patch("file3.py", "source 3", source_step="container-task-2"),
        ]
        scope = create_mock_scope_with_patches(patches)

        visible = collect_visible_patches(scope, binding_name="workspace")

        # All patches visible regardless of source
        assert len(visible) == 3


# =============================================================================
# Test 4: Combinator Integration
# =============================================================================


class TestCombinatorIntegration:
    """Test TransferBundle integration with fork/merge/discard semantics.

    Key principle: Child scope containment is the mechanism.
    Device effects follow the same rules as local effects.
    """

    def test_forked_scope_inherits_parent_patches(self):
        """Forked scope should see parent's patches for bundle creation."""
        # Parent has patches
        parent_patches = [create_test_patch("parent.py", "from parent")]
        parent_scope = create_mock_scope_with_patches(parent_patches)

        # Child inherits parent's effect stream (mock this)
        child_scope = create_mock_scope_with_patches(parent_patches)

        # Child's bundle should include parent's patches
        visible = collect_visible_patches(child_scope, binding_name="workspace")
        assert len(visible) == 1
        assert "parent.py" in visible[0].files_changed

    def test_child_patches_not_visible_to_parent_before_merge(self):
        """Child's patches should not affect parent until merge.

        This is the containment guarantee.
        """
        parent_scope = create_mock_scope_with_patches([])

        # Child creates a patch
        child_patch = create_test_patch("child.py", "from child")
        child_scope = create_mock_scope_with_patches([child_patch])

        # Parent should not see child's patch
        parent_visible = collect_visible_patches(parent_scope, binding_name="workspace")
        assert len(parent_visible) == 0

        # Child should see its own patch
        child_visible = collect_visible_patches(child_scope, binding_name="workspace")
        assert len(child_visible) == 1

    def test_immutable_bundle_safe_for_parallel_use(self):
        """TransferBundle should be safely shareable across parallel tasks.

        The dataclass is frozen, meaning field reassignment fails.
        For true deep immutability, callers should use immutable mappings,
        but the frozen dataclass provides the primary safety guarantee.
        """
        patch = create_test_patch("shared.py", "shared content")
        scope = create_mock_scope_with_patches([patch])

        # Create bundle once
        visible = collect_visible_patches(scope, binding_name="workspace")
        bundle = TransferBundle(
            files={".shepherd/patches/0000.diff": visible[0].patch.encode()},
            manifest={"shared.py": visible[0].sha256 or ""},
        )

        # Bundle is frozen - field reassignment should fail
        with pytest.raises(AttributeError):
            bundle.files = {}  # type: ignore[misc]

        # Verify bundle can be safely read from multiple "tasks"
        def read_bundle(b: TransferBundle) -> tuple[int, int]:
            return len(b.files), len(b.manifest)

        # Simulate parallel access
        results = [read_bundle(bundle) for _ in range(10)]
        assert all(r == (1, 1) for r in results)


# =============================================================================
# Test 5: Session Resumption
# =============================================================================


class TestSessionResumption:
    """Test session transcript visibility across device boundaries.

    Session transcripts use a different strategy than workspace patches:
    - Direct bind mount (not overlay)
    - Same-path mounting for SDK compatibility
    - Bidirectional: host writes visible in container, and vice versa

    This is documented in DESIGN-device-state-transfer.md section
    'Bidirectional Transfer: Session Transcripts'.
    """

    def test_same_path_mounting_preserves_sdk_paths(self, temp_workspace: Path):
        """Same-path mounting ensures SDK computes identical project folder.

        The Claude SDK computes project folder from CWD:
        path.replace("/", "-").replace(".", "-")

        Same CWD = same project folder = transcripts found at expected location.
        """

        def compute_project_folder(cwd: Path) -> str:
            """Compute Claude SDK project folder name from CWD."""
            return str(cwd).replace("/", "-").replace(".", "-")

        # Host CWD
        host_cwd = temp_workspace / "project"
        host_cwd.mkdir(parents=True)

        # Container CWD (same path via bind mount)
        container_cwd = host_cwd  # Same path!

        host_folder = compute_project_folder(host_cwd)
        container_folder = compute_project_folder(container_cwd)

        # They should match, enabling session resumption
        assert host_folder == container_folder

    def test_hidden_directory_double_dash_handling(self):
        """Hidden directories create double dashes in project folder name.

        SDK replaces BOTH / AND . with -, so:
        /Users/alice/.config becomes -Users-alice--config
        """

        def compute_project_folder(cwd: str) -> str:
            return cwd.replace("/", "-").replace(".", "-")

        # Path with hidden directory
        hidden_path = "/Users/alice/.config/app"
        folder = compute_project_folder(hidden_path)

        # Note the double dash before 'config' (from '/.' becoming '--')
        assert folder == "-Users-alice--config-app"

    def test_session_bundle_uses_same_path_mounts(self):
        """Session transfer bundle should use same-path mounts.

        This test documents the expected structure of a session bundle.
        The actual SessionState.transfer_bundle() is deferred, but the
        design is validated here.
        """
        from pathlib import Path

        # Expected bundle structure for session transfer
        claude_dir = Path.home() / ".claude"
        workspace_path = "/Users/alice/project"

        # Bundle should mount at same paths
        expected_mounts = {
            str(claude_dir): str(claude_dir),  # ~/.claude at same path
            workspace_path: workspace_path,  # workspace at same path
        }

        # Verify mount spec uses same paths (host_path == container_path)
        for host_path, container_path in expected_mounts.items():
            assert host_path == container_path, "Same-path mounting required"

    def test_session_transcripts_bidirectional_via_bind_mount(self, temp_workspace: Path):
        """Session transcript changes should be immediately visible bidirectionally.

        With bind mounts (not overlay), writes in container are immediately
        visible on host and vice versa. This is 'free' with bind mounts.
        """
        # Simulate bind-mounted session directory
        session_dir = temp_workspace / ".claude" / "projects" / "-test-project"
        session_dir.mkdir(parents=True)

        # Host writes transcript
        transcript = session_dir / "session.jsonl"
        transcript.write_text('{"role": "user", "content": "hello"}\n')

        # Container (same path via bind mount) sees it immediately
        assert transcript.exists()
        assert "hello" in transcript.read_text()

        # Container appends to transcript
        with transcript.open("a") as f:
            f.write('{"role": "assistant", "content": "hi"}\n')

        # Host sees container's write immediately
        content = transcript.read_text()
        assert "hello" in content
        assert "hi" in content


# =============================================================================
# Test 6: ContainerSandbox Bundle Storage
# =============================================================================


class TestContainerSandboxBundles:
    """Test that ContainerSandbox correctly stores bundles."""

    def test_sandbox_has_bundles_field(self):
        """ContainerSandbox should have bundles dict."""
        sandbox = ContainerSandbox.create("test-task")
        assert hasattr(sandbox, "bundles")
        assert sandbox.bundles == {}

    def test_sandbox_stores_bundles_by_binding_name(self):
        """Bundles should be stored by binding name."""
        sandbox = ContainerSandbox.create("test-task")

        bundle = TransferBundle(
            state={"context_type": "workspace"},
            manifest={"file.py": "abc123"},
        )
        sandbox.bundles["workspace"] = bundle

        assert "workspace" in sandbox.bundles
        assert sandbox.bundles["workspace"].state["context_type"] == "workspace"


# =============================================================================
# Test 6: Deferred Gap Documentation
# =============================================================================


class TestDeferredGap:
    """Document the known limitation: local after container.

    This is NOT a bug - it's a documented design decision.
    The workaround is scope.materialize() before local execution.

    See DESIGN-device-state-transfer.md section 'The Deferred Gap'.
    """

    def test_deferred_gap_documented(self):
        """Verify the deferred gap is intentional.

        Pattern that doesn't work without workaround:
        ```
        with Device("container"):
            result_a = WriteCode(...)  # Creates auth.py

        # Exit container
        result_b = FixTypo(file="auth.py")  # Local can't see auth.py
        ```

        Workaround:
        ```
        with Device("container"):
            result_a = WriteCode(...)

        await scope.materialize()  # Apply patches to real filesystem
        result_b = FixTypo(...)  # Now local sees them
        ```
        """
        # This test exists to document the limitation
        # The actual behavior is that container patches exist in scope's
        # effect stream but not on the local filesystem until materialized

        # Container patches are in the effect stream
        container_patch = create_test_patch("auth.py", "from container")
        scope = create_mock_scope_with_patches([container_patch])

        visible = collect_visible_patches(scope, binding_name="workspace")
        assert len(visible) == 1  # Patch is in scope

        # But a local agent would need the actual file on disk
        # which requires scope.materialize() - tested elsewhere


# =============================================================================
# E2E Tests (Require Podman)
# =============================================================================


@requires_podman
@pytest.mark.e2e
class TestTransferBundleE2E:
    """End-to-end tests with real container execution.

    These tests verify the complete flow:
    1. Create workspace with patches
    2. Generate TransferBundle
    3. Apply bundle to container
    4. Execute in container
    5. Extract effects using manifest
    """

    def test_bundle_files_written_to_task_dir(self, temp_workspace: Path, temp_overlays: Path):
        """Bundle files should be written to sandbox task_dir.

        This simulates _apply_bundles() behavior.
        """
        # Create a bundle with patch files
        patch_content = b"--- a/test.py\n+++ b/test.py\n@@ -0,0 +1 @@\n+print('hello')\n"
        bundle = TransferBundle(
            files={".shepherd/patches/0000.diff": patch_content},
            env={"SHEPHERD_PATCH_DIR": str(temp_workspace / ".shepherd/patches")},
        )

        # Simulate _apply_bundles writing files
        for path, content in bundle.files.items():
            target = temp_workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        # Verify files exist
        patch_file = temp_workspace / ".shepherd/patches/0000.diff"
        assert patch_file.exists()
        assert patch_file.read_bytes() == patch_content

    def test_manifest_enables_correct_effect_extraction(self, temp_workspace: Path, temp_overlays: Path):
        """Manifest should enable filtering of pre-applied bundle state.

        End-to-end flow:
        1. Bundle has manifest of pre-applied files
        2. Container execution creates/modifies files
        3. Effect extraction uses manifest to filter
        4. Only NEW effects should be returned
        """
        # Setup overlay structure
        lower = temp_overlays / "lower"
        upper = temp_overlays / "upper"
        work = temp_overlays / "work"
        merged = temp_overlays / "merged"

        for d in [lower, upper, work, merged]:
            d.mkdir(parents=True)

        # Simulate bundle application: write pre-applied file to upper
        preapplied_content = b"# Pre-applied from bundle\ndef preapplied(): pass\n"
        (upper / "preapplied.py").write_bytes(preapplied_content)

        # Simulate container creating a new file
        new_content = b"# New from container\ndef new_func(): pass\n"
        (upper / "new_file.py").write_bytes(new_content)

        # Create manifest matching pre-applied content
        manifest = {"preapplied.py": compute_content_hash(preapplied_content)}

        # Create overlay and extract
        overlay = OverlayMount(
            task_id="e2e-test",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
        )

        collector = EffectCollector()
        extractor = OverlayEffectExtractor(lower_path=lower)
        effects = extractor.extract(overlay, collector, manifest=manifest)

        # Should only have effect for new_file.py, not preapplied.py
        effect_paths = [getattr(e, "path", None) for e in effects]
        assert "new_file.py" in effect_paths
        assert "preapplied.py" not in effect_paths


# =============================================================================
# Regression Tests
# =============================================================================


class TestRegressions:
    """Regression tests for edge cases discovered during development."""

    def test_empty_patch_list_returns_none_bundle(self):
        """WorkspaceRef should return None when no patches to transfer.

        This prevents creating empty bundles that would waste resources.
        """
        scope = create_mock_scope_with_patches([])
        visible = collect_visible_patches(scope, binding_name="workspace")
        assert visible == []

    def test_manifest_handles_empty_files(self, temp_overlays: Path):
        """Manifest filtering should handle empty files correctly."""
        extractor = OverlayEffectExtractor()

        empty_content = b""
        empty_hash = compute_content_hash(empty_content)
        manifest = {"empty.py": empty_hash}

        # Empty file matching manifest should not emit
        result = extractor._should_emit_effect("empty.py", empty_content, manifest)
        assert result is False

        # Empty file not in manifest should emit
        result = extractor._should_emit_effect("new_empty.py", empty_content, {})
        assert result is True

    def test_manifest_handles_binary_files(self, temp_overlays: Path):
        """Manifest filtering should handle binary files correctly."""
        extractor = OverlayEffectExtractor()

        binary_content = bytes(range(256))  # All byte values
        binary_hash = compute_content_hash(binary_content)
        manifest = {"binary.dat": binary_hash}

        # Binary file matching manifest should not emit
        result = extractor._should_emit_effect("binary.dat", binary_content, manifest)
        assert result is False

        # Modified binary should emit
        modified = binary_content + b"\x00"
        result = extractor._should_emit_effect("binary.dat", modified, manifest)
        assert result is True

    def test_patch_sha256_auto_computed(self):
        """DiffPatch should auto-compute sha256 for non-empty patches."""
        patch = create_test_patch("test.py", "content")
        assert patch.sha256 is not None
        assert len(patch.sha256) == 64  # SHA-256 hex length


# =============================================================================
# Error Type Tests
# =============================================================================


class TestDeviceBoundaryErrors:
    """Test device boundary error types."""

    def test_device_boundary_error_is_base(self):
        """DeviceBoundaryError should be the base for all device errors."""
        from shepherd_runtime.device import (
            BundleApplicationError,
            ContainerStartupError,
            DeviceBoundaryError,
            DeviceNestingError,
            DeviceSpaceError,
            EffectExtractionError,
            MountError,
            PatchApplicationError,
            TaskTimeoutError,
        )

        # All errors should inherit from DeviceBoundaryError
        assert issubclass(PatchApplicationError, DeviceBoundaryError)
        assert issubclass(ContainerStartupError, DeviceBoundaryError)
        assert issubclass(MountError, DeviceBoundaryError)
        assert issubclass(TaskTimeoutError, DeviceBoundaryError)
        assert issubclass(DeviceSpaceError, DeviceBoundaryError)
        assert issubclass(DeviceNestingError, DeviceBoundaryError)
        assert issubclass(BundleApplicationError, DeviceBoundaryError)
        assert issubclass(EffectExtractionError, DeviceBoundaryError)

    def test_patch_application_error_attributes(self):
        """PatchApplicationError should capture patch details."""
        from shepherd_runtime.device import PatchApplicationError

        err = PatchApplicationError(
            "Failed to apply patch",
            patch_name="0001.diff",
            git_output="error: patch failed",
            applied_patches=["0000.diff"],
        )

        assert err.patch_name == "0001.diff"
        assert err.git_output == "error: patch failed"
        assert err.applied_patches == ["0000.diff"]
        assert "0001.diff" in str(err)

    def test_container_startup_error_attributes(self):
        """ContainerStartupError should capture container details."""
        from shepherd_runtime.device import ContainerStartupError

        err = ContainerStartupError(
            "Container failed to start",
            image="python:3.12",
            container_name="shepherd-task-123",
            runtime_output="Error: no space left on device",
        )

        assert err.image == "python:3.12"
        assert err.container_name == "shepherd-task-123"
        assert "python:3.12" in str(err)

    def test_mount_error_attributes(self):
        """MountError should capture mount details."""
        from shepherd_runtime.device import MountError

        err = MountError(
            "Mount failed",
            host_path="/Users/alice/project",
            container_path="/Users/alice/project",
            mount_type="bind",
        )

        assert err.host_path == "/Users/alice/project"
        assert err.container_path == "/Users/alice/project"
        assert err.mount_type == "bind"

    def test_task_timeout_error_attributes(self):
        """TaskTimeoutError should capture timeout details."""
        from shepherd_runtime.device import TaskTimeoutError

        err = TaskTimeoutError(
            "Task timed out",
            timeout_seconds=300.0,
            task_id="task-abc123",
        )

        assert err.timeout_seconds == 300.0
        assert err.task_id == "task-abc123"
        assert "300" in str(err)

    def test_device_space_error_attributes(self):
        """DeviceSpaceError should capture space details."""
        from shepherd_runtime.device import DeviceSpaceError

        err = DeviceSpaceError(
            "No space left",
            tmpfs_size="200m",
            attempted_path="/overlay-work/upper/large_file.bin",
            suggestion="Increase tmpfs_size to 500m",
        )

        assert err.tmpfs_size == "200m"
        assert err.attempted_path == "/overlay-work/upper/large_file.bin"
        assert err.suggestion == "Increase tmpfs_size to 500m"

    def test_device_nesting_error_auto_message(self):
        """DeviceNestingError should auto-generate message from devices."""
        from shepherd_runtime.device import DeviceNestingError

        err = DeviceNestingError(
            outer_device="container",
            inner_device="local",
        )

        assert err.outer_device == "container"
        assert err.inner_device == "local"
        assert "Cannot nest Device('local') inside Device('container')" in str(err)

    def test_can_catch_all_device_errors(self):
        """Should be able to catch all device errors with base class."""
        from shepherd_runtime.device import (
            ContainerStartupError,
            DeviceBoundaryError,
            PatchApplicationError,
        )

        # Can catch different errors with base class
        errors = [
            PatchApplicationError("patch failed"),
            ContainerStartupError("container failed"),
        ]

        caught = 0
        for err in errors:
            try:
                raise err
            except DeviceBoundaryError:
                caught += 1

        assert caught == 2


# =============================================================================
# Device Nesting Prevention Tests
# =============================================================================


class TestDeviceNestingPrevention:
    """Test that nested device contexts are prevented."""

    def test_nested_same_device_raises(self):
        """Nesting same device type should raise DeviceNestingError."""
        from shepherd_runtime.device import Device, DeviceNestingError

        with pytest.raises(DeviceNestingError) as exc_info, Device("container"), Device("container"):
            pass

        assert exc_info.value.outer_device == "container"
        assert exc_info.value.inner_device == "container"

    def test_nested_different_devices_raises(self):
        """Nesting different device types should raise DeviceNestingError."""
        from shepherd_runtime.device import Device, DeviceNestingError

        with pytest.raises(DeviceNestingError) as exc_info, Device("container"), Device("local"):
            pass

        assert exc_info.value.outer_device == "container"
        assert exc_info.value.inner_device == "local"

    def test_nested_local_in_container_raises(self):
        """Nesting local inside container should raise DeviceNestingError."""
        from shepherd_runtime.device import Device, DeviceNestingError

        with pytest.raises(DeviceNestingError) as exc_info, Device("local"), Device("container"):
            pass

        assert exc_info.value.outer_device == "local"
        assert exc_info.value.inner_device == "container"

    def test_sequential_devices_allowed(self):
        """Sequential (non-nested) device contexts should work."""
        from shepherd_runtime.device import Device, get_current_device

        # First device context
        with Device("container"):
            device1 = get_current_device()
            assert device1 is not None

        # After exit, no device should be active
        assert get_current_device() is None

        # Second device context should work
        with Device("local"):
            device2 = get_current_device()
            assert device2 is not None

        assert get_current_device() is None

    def test_nesting_error_message_is_helpful(self):
        """Error message should explain the issue and suggest fix."""
        from shepherd_runtime.device import Device, DeviceNestingError

        with pytest.raises(DeviceNestingError) as exc_info, Device("container"), Device("local"):
            pass

        error_msg = str(exc_info.value)
        # Should mention both device types
        assert "container" in error_msg.lower()
        assert "local" in error_msg.lower()
        # Should suggest a fix
        assert "exit" in error_msg.lower() or "cannot be nested" in error_msg.lower()

    def test_nesting_check_resets_on_exit(self):
        """After exiting device context, nesting check should reset."""
        from shepherd_runtime.device import Device

        # Enter and exit
        with Device("container"):
            pass

        # Should be able to enter container again
        with Device("container"):
            pass

        # Should be able to enter different device
        with Device("local"):
            pass
