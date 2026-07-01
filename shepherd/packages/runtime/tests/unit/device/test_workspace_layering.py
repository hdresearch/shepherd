"""Tests for workspace patch layering in container execution.

This module tests the overlay stacking functionality that enables Task B
to see Task A's file changes without explicit materialization.

See: PLAN-workspace-patch-layering.md
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shepherd_core.effects import ContainerExecutionCompleted
from shepherd_runtime.device.container.podman import ContainerSandbox, OverlayMount
from shepherd_runtime.scope import Scope


class TestOverlayMountMultiLayer:
    """Tests for multi-layer OverlayMount support."""

    def test_single_lower_layer(self) -> None:
        """Single lower layer works as before (backwards compatible)."""
        overlay = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=Path("/base/workspace"),
            upper=Path("/tmp/task-1/upper"),
            work=Path("/tmp/task-1/work"),
            merged=Path("/tmp/task-1/merged"),
        )

        assert overlay.lower_layers == [Path("/base/workspace")]
        assert overlay.mount_options == ("lowerdir=/base/workspace,upperdir=/tmp/task-1/upper,workdir=/tmp/task-1/work")

    def test_multiple_lower_layers(self) -> None:
        """Multiple lower layers are supported for stacking."""
        overlay = OverlayMount(
            task_id="task-2",
            context_name="workspace",
            lower=[
                Path("/tmp/task-1/upper"),  # Parent's upper (highest priority)
                Path("/base/workspace"),  # Base (lowest priority)
            ],
            upper=Path("/tmp/task-2/upper"),
            work=Path("/tmp/task-2/work"),
            merged=Path("/tmp/task-2/merged"),
        )

        assert overlay.lower_layers == [
            Path("/tmp/task-1/upper"),
            Path("/base/workspace"),
        ]
        # CRITICAL: Leftmost = highest priority in OverlayFS
        assert overlay.mount_options == (
            "lowerdir=/tmp/task-1/upper:/base/workspace,upperdir=/tmp/task-2/upper,workdir=/tmp/task-2/work"
        )

    def test_three_layer_stacking(self) -> None:
        """Three-layer stacking works correctly."""
        overlay = OverlayMount(
            task_id="task-3",
            context_name="workspace",
            lower=[
                Path("/tmp/task-2/upper"),  # Newest (highest priority)
                Path("/tmp/task-1/upper"),  # Older
                Path("/base/workspace"),  # Base (lowest priority)
            ],
            upper=Path("/tmp/task-3/upper"),
            work=Path("/tmp/task-3/work"),
            merged=Path("/tmp/task-3/merged"),
        )

        # Layer order: newest:older:oldest:base
        assert "lowerdir=/tmp/task-2/upper:/tmp/task-1/upper:/base/workspace" in overlay.mount_options


class TestContainerSandboxLayering:
    """Tests for ContainerSandbox parent tracking and layer methods."""

    def test_get_workspace_layers_no_parent(self) -> None:
        """Sandbox without parent returns only its own upper layer."""
        sandbox = ContainerSandbox.create("task-1")
        sandbox.overlays["workspace"] = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=Path("/base"),
            upper=Path("/tmp/task-1/upper"),
            work=Path("/tmp/task-1/work"),
            merged=Path("/tmp/task-1/merged"),
        )

        layers = sandbox.get_workspace_layers()
        assert layers == [Path("/tmp/task-1/upper")]

    def test_get_workspace_layers_with_parent(self) -> None:
        """Sandbox with parent includes parent's layers."""
        # Create parent sandbox
        parent = ContainerSandbox.create("task-1")
        parent.overlays["workspace"] = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=Path("/base"),
            upper=Path("/tmp/task-1/upper"),
            work=Path("/tmp/task-1/work"),
            merged=Path("/tmp/task-1/merged"),
        )

        # Create child sandbox with parent reference
        child = ContainerSandbox.create("task-2")
        child.parent_sandbox = parent
        child.overlays["workspace"] = OverlayMount(
            task_id="task-2",
            context_name="workspace",
            lower=[Path("/tmp/task-1/upper"), Path("/base")],
            upper=Path("/tmp/task-2/upper"),
            work=Path("/tmp/task-2/work"),
            merged=Path("/tmp/task-2/merged"),
        )

        layers = child.get_workspace_layers()
        # Should include child's upper, then parent's upper
        assert layers == [
            Path("/tmp/task-2/upper"),
            Path("/tmp/task-1/upper"),
        ]

    def test_get_workspace_layers_deep_chain(self) -> None:
        """Deep parent chain returns all layers in correct order."""
        # Create grandparent
        grandparent = ContainerSandbox.create("task-1")
        grandparent.overlays["workspace"] = OverlayMount(
            task_id="task-1",
            context_name="workspace",
            lower=Path("/base"),
            upper=Path("/tmp/task-1/upper"),
            work=Path("/tmp/task-1/work"),
            merged=Path("/tmp/task-1/merged"),
        )

        # Create parent
        parent = ContainerSandbox.create("task-2")
        parent.parent_sandbox = grandparent
        parent.overlays["workspace"] = OverlayMount(
            task_id="task-2",
            context_name="workspace",
            lower=[Path("/tmp/task-1/upper"), Path("/base")],
            upper=Path("/tmp/task-2/upper"),
            work=Path("/tmp/task-2/work"),
            merged=Path("/tmp/task-2/merged"),
        )

        # Create child
        child = ContainerSandbox.create("task-3")
        child.parent_sandbox = parent
        child.overlays["workspace"] = OverlayMount(
            task_id="task-3",
            context_name="workspace",
            lower=[Path("/tmp/task-2/upper"), Path("/tmp/task-1/upper"), Path("/base")],
            upper=Path("/tmp/task-3/upper"),
            work=Path("/tmp/task-3/work"),
            merged=Path("/tmp/task-3/merged"),
        )

        layers = child.get_workspace_layers()
        # Should be: newest, older, oldest
        assert layers == [
            Path("/tmp/task-3/upper"),
            Path("/tmp/task-2/upper"),
            Path("/tmp/task-1/upper"),
        ]

    def test_get_workspace_layers_no_workspace_overlay(self) -> None:
        """Sandbox without workspace overlay returns empty list."""
        sandbox = ContainerSandbox.create("task-1")
        # No workspace overlay added

        layers = sandbox.get_workspace_layers()
        assert layers == []

    def test_get_workspace_layers_prefers_fuse_accumulated_when_populated(self, tmp_path) -> None:
        """Fuse accumulated layer should outrank the empty kernel upper."""
        sandbox = ContainerSandbox.create("task-fuse")
        sandbox.task_dir = tmp_path / "task"
        sandbox.task_dir.mkdir()

        accumulated = sandbox.task_dir / "overlays" / "accumulated"
        accumulated.mkdir(parents=True)
        (accumulated / "foo.py").write_text("created by fuse")

        kernel_upper = tmp_path / "kernel-upper"
        kernel_upper.mkdir()
        sandbox.overlays["workspace"] = OverlayMount(
            task_id="task-fuse",
            context_name="workspace",
            lower=Path("/base"),
            upper=kernel_upper,
            work=tmp_path / "work",
            merged=tmp_path / "merged",
        )

        assert sandbox.get_workspace_layers() == [accumulated]

    def test_get_workspace_layers_falls_back_to_kernel_upper_when_accumulated_empty(self, tmp_path) -> None:
        """Empty accumulated dir should not mask the kernel overlay upper."""
        sandbox = ContainerSandbox.create("task-single")
        sandbox.task_dir = tmp_path / "task"
        sandbox.task_dir.mkdir()
        (sandbox.task_dir / "overlays" / "accumulated").mkdir(parents=True)

        kernel_upper = tmp_path / "kernel-upper"
        kernel_upper.mkdir()
        sandbox.overlays["workspace"] = OverlayMount(
            task_id="task-single",
            context_name="workspace",
            lower=Path("/base"),
            upper=kernel_upper,
            work=tmp_path / "work",
            merged=tmp_path / "merged",
        )

        assert sandbox.get_workspace_layers() == [kernel_upper]

    def test_get_workspace_layers_walks_mixed_ancestor_chain(self, tmp_path) -> None:
        """Child and parent may use fuse while grandparent remains kernel-overlay."""

        def make_sandbox(name: str, has_accumulated: bool) -> ContainerSandbox:
            sandbox = ContainerSandbox.create(name)
            sandbox.task_dir = tmp_path / name / "task"
            sandbox.task_dir.mkdir(parents=True)
            if has_accumulated:
                accumulated = sandbox.task_dir / "overlays" / "accumulated"
                accumulated.mkdir(parents=True)
                (accumulated / "file.py").write_text("content")
            upper = tmp_path / name / "upper"
            upper.mkdir(parents=True)
            sandbox.overlays["workspace"] = OverlayMount(
                task_id=name,
                context_name="workspace",
                lower=Path("/base"),
                upper=upper,
                work=tmp_path / name / "work",
                merged=tmp_path / name / "merged",
            )
            return sandbox

        grandparent = make_sandbox("grandparent", has_accumulated=False)
        parent = make_sandbox("parent", has_accumulated=True)
        child = make_sandbox("child", has_accumulated=True)
        child.parent_sandbox = parent
        parent.parent_sandbox = grandparent

        assert child.get_workspace_layers() == [
            child.task_dir / "overlays" / "accumulated",
            parent.task_dir / "overlays" / "accumulated",
            grandparent.overlays["workspace"].upper,
        ]


class TestScopeBasedParentTracking:
    """Tests for scope-based sandbox tracking integration."""

    def test_scope_finds_parent_sandbox(self) -> None:
        """Scope can find parent sandbox via effect stream."""
        with Scope() as scope:
            # Create and register a sandbox
            sandbox = ContainerSandbox.create("parent-task")
            sandbox.overlays["workspace"] = OverlayMount(
                task_id="parent-task",
                context_name="workspace",
                lower=Path("/base"),
                upper=Path("/tmp/parent/upper"),
                work=Path("/tmp/parent/work"),
                merged=Path("/tmp/parent/merged"),
            )
            scope.register_sandbox(sandbox)

            # Emit completion effect (as device would after extract_effects)
            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="parent-task",
                    context_name="workspace",
                    has_workspace_changes=True,
                )
            )

            # Now a child task should find this as parent
            parent = scope.get_latest_sandbox_for_context("workspace")
            assert parent is sandbox
            assert parent.get_workspace_layers() == [Path("/tmp/parent/upper")]

    def test_scope_finds_most_recent_sandbox(self) -> None:
        """Scope returns most recent sandbox when multiple exist."""
        with Scope() as scope:
            # Create first sandbox
            sandbox1 = ContainerSandbox.create("task-1")
            sandbox1.overlays["workspace"] = OverlayMount(
                task_id="task-1",
                context_name="workspace",
                lower=Path("/base"),
                upper=Path("/tmp/task-1/upper"),
                work=Path("/tmp/task-1/work"),
                merged=Path("/tmp/task-1/merged"),
            )
            scope.register_sandbox(sandbox1)
            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="task-1",
                    context_name="workspace",
                )
            )

            # Create second sandbox
            sandbox2 = ContainerSandbox.create("task-2")
            sandbox2.overlays["workspace"] = OverlayMount(
                task_id="task-2",
                context_name="workspace",
                lower=[Path("/tmp/task-1/upper"), Path("/base")],
                upper=Path("/tmp/task-2/upper"),
                work=Path("/tmp/task-2/work"),
                merged=Path("/tmp/task-2/merged"),
            )
            scope.register_sandbox(sandbox2)
            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="task-2",
                    context_name="workspace",
                )
            )

            # Should find sandbox2 (most recent)
            parent = scope.get_latest_sandbox_for_context("workspace")
            assert parent is sandbox2

    def test_child_scope_finds_parent_scope_sandbox(self) -> None:
        """Child scope can find sandbox registered in parent scope."""
        with Scope() as parent_scope:
            # Register sandbox in parent scope
            sandbox = ContainerSandbox.create("parent-task")
            sandbox.overlays["workspace"] = OverlayMount(
                task_id="parent-task",
                context_name="workspace",
                lower=Path("/base"),
                upper=Path("/tmp/parent/upper"),
                work=Path("/tmp/parent/work"),
                merged=Path("/tmp/parent/merged"),
            )
            parent_scope.register_sandbox(sandbox)
            parent_scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="parent-task",
                    context_name="workspace",
                )
            )

            with Scope() as child_scope:
                # Child should find parent's sandbox
                found = child_scope.get_latest_sandbox_for_context("workspace")
                assert found is sandbox

    def test_sibling_scope_finds_parent_sandbox_for_layering(self) -> None:
        """child_A registers sandbox + emits effect, child_B finds it via get_latest_sandbox_for_context."""
        with Scope() as parent_scope:
            with Scope() as child_a:
                sandbox = ContainerSandbox.create("sibling-task-a")
                sandbox.overlays["workspace"] = OverlayMount(
                    task_id="sibling-task-a",
                    context_name="workspace",
                    lower=Path("/base"),
                    upper=Path("/tmp/sibling-a/upper"),
                    work=Path("/tmp/sibling-a/work"),
                    merged=Path("/tmp/sibling-a/merged"),
                )
                child_a.register_sandbox(sandbox)
                child_a.emit(
                    ContainerExecutionCompleted(
                        sandbox_id="sibling-task-a",
                        context_name="workspace",
                        has_workspace_changes=True,
                    )
                )

            with Scope() as child_b:
                found = child_b.get_latest_sandbox_for_context("workspace")
                assert found is sandbox
                assert found.get_workspace_layers() == [Path("/tmp/sibling-a/upper")]


class TestWorkspaceLayeringIntegration:
    """Integration tests for complete workspace layering flow."""

    def test_layer_chain_built_correctly(self) -> None:
        """Complete layer chain is built through multiple sandbox creations."""
        with Scope() as scope:
            # Simulate Task A creating a sandbox
            sandbox_a = ContainerSandbox.create("task-a")
            sandbox_a.overlays["workspace"] = OverlayMount(
                task_id="task-a",
                context_name="workspace",
                lower=Path("/workspace"),
                upper=Path("/tmp/task-a/upper"),
                work=Path("/tmp/task-a/work"),
                merged=Path("/tmp/task-a/merged"),
            )
            scope.register_sandbox(sandbox_a)
            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="task-a",
                    context_name="workspace",
                    has_workspace_changes=True,
                )
            )

            # Find parent for Task B
            parent_for_b = scope.get_latest_sandbox_for_context("workspace")
            assert parent_for_b is sandbox_a

            # Get layers for Task B's overlay
            parent_layers = parent_for_b.get_workspace_layers()
            assert parent_layers == [Path("/tmp/task-a/upper")]

            # Task B's overlay should have: [task-a-upper, workspace] as lower
            sandbox_b = ContainerSandbox.create("task-b")
            sandbox_b.parent_sandbox = parent_for_b
            sandbox_b.overlays["workspace"] = OverlayMount(
                task_id="task-b",
                context_name="workspace",
                lower=[*parent_layers, Path("/workspace")],  # parent_layers + base
                upper=Path("/tmp/task-b/upper"),
                work=Path("/tmp/task-b/work"),
                merged=Path("/tmp/task-b/merged"),
            )

            # Verify Task B's overlay has correct mount options
            assert sandbox_b.overlays["workspace"].mount_options == (
                "lowerdir=/tmp/task-a/upper:/workspace,upperdir=/tmp/task-b/upper,workdir=/tmp/task-b/work"
            )


class TestFuseEffectReconciliation:
    """Tests for dual-path effect reconciliation in ContainerDevice.extract_effects()."""

    def _device(self) -> object:
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice.__new__(ContainerDevice)
        device._extractor = MagicMock()
        return device

    def _execution_result(self, collector_payload: object) -> object:
        return SimpleNamespace(metadata={"_collector": collector_payload, "_exit_code": 0})

    def _sandbox_with_workspace(self, tmp_path, accumulated_has_content: bool) -> ContainerSandbox:
        sandbox = ContainerSandbox.create("task-fuse")
        sandbox.task_dir = tmp_path / "task"
        sandbox.task_dir.mkdir(parents=True)
        kernel_upper = tmp_path / "kernel-upper"
        kernel_upper.mkdir()
        sandbox.overlays["workspace"] = OverlayMount(
            task_id="task-fuse",
            context_name="workspace",
            lower=Path("/base"),
            upper=kernel_upper,
            work=tmp_path / "work",
            merged=tmp_path / "merged",
            original_host_path=Path("/original"),
        )
        if accumulated_has_content:
            accumulated = sandbox.task_dir / "overlays" / "accumulated"
            accumulated.mkdir(parents=True)
            (accumulated / "foo.py").write_text("content")
        return sandbox

    def test_is_fuse_overlay_mode_detects_populated_accumulated(self, tmp_path) -> None:
        device = self._device()
        sandbox = self._sandbox_with_workspace(tmp_path, accumulated_has_content=True)

        assert device._is_fuse_overlay_mode(sandbox) is True

    def test_is_fuse_overlay_mode_rejects_empty_or_missing_accumulated(self, tmp_path) -> None:
        device = self._device()
        populated = self._sandbox_with_workspace(tmp_path / "missing", accumulated_has_content=False)
        assert device._is_fuse_overlay_mode(populated) is False

        no_task_dir = ContainerSandbox.create("no-task-dir")
        no_task_dir.task_dir = None
        assert device._is_fuse_overlay_mode(no_task_dir) is False

    @staticmethod
    async def _extract(device, sandbox, execution_result):
        return await device.extract_effects(sandbox, execution_result)

    def test_extract_effects_uses_collector_file_effects_in_fuse_mode(self, tmp_path) -> None:
        from shepherd_core.effects import AgentMessage, FileCreate
        from shepherd_runtime.device.container.effect_collector import EffectCollector

        device = self._device()
        device._extractor.extract.side_effect = AssertionError("overlay extract should be skipped in fuse mode")
        device._extractor.extract_workspace_patch.return_value = None
        device._extractor.extract_workspace_patch.return_value = None

        sandbox = self._sandbox_with_workspace(tmp_path, accumulated_has_content=True)

        collector = EffectCollector()
        collector.emit(FileCreate(path="foo.py", content="hello", caused_by="toolu_1"))
        collector.emit(AgentMessage(content="done", role="assistant"))

        bundle = __import__("asyncio").run(
            self._extract(device, sandbox, self._execution_result(collector.serialize_for_transport()))
        )

        assert [effect.effect_type for effect in bundle.context_effects["workspace"]] == ["file_create"]
        assert [effect.effect_type for effect in bundle.lifecycle_effects[:-1]] == ["agent_message"]
        assert bundle.execution_metadata["fuse_overlay_mode"] is True

    def test_extract_effects_builds_patch_from_accumulated_in_fuse_mode(self, tmp_path) -> None:
        from shepherd_runtime.device.container.effect_collector import EffectCollector

        device = self._device()
        device._extractor.extract.side_effect = AssertionError("overlay extract should be skipped in fuse mode")
        patch_effect = object()
        device._extractor.extract_workspace_patch.return_value = patch_effect

        sandbox = self._sandbox_with_workspace(tmp_path, accumulated_has_content=True)
        collector = EffectCollector()

        bundle = __import__("asyncio").run(
            self._extract(device, sandbox, self._execution_result(collector.serialize_for_transport()))
        )

        assert bundle.context_effects["workspace"] == [patch_effect]
        synthetic_overlay = device._extractor.extract_workspace_patch.call_args.args[0]
        assert synthetic_overlay.upper == sandbox.task_dir / "overlays" / "accumulated"

    def test_extract_effects_preserves_single_layer_behavior(self, tmp_path) -> None:
        from shepherd_core.effects import AgentMessage
        from shepherd_runtime.device.container.effect_collector import EffectCollector

        device = self._device()
        overlay_effect = object()
        patch_effect = object()
        device._extractor.extract.return_value = [overlay_effect]
        device._extractor.extract_workspace_patch.return_value = patch_effect

        sandbox = self._sandbox_with_workspace(tmp_path, accumulated_has_content=False)
        collector = EffectCollector()
        collector.emit(AgentMessage(content="done", role="assistant"))

        bundle = __import__("asyncio").run(
            self._extract(device, sandbox, self._execution_result(collector.serialize_for_transport()))
        )

        assert bundle.context_effects["workspace"] == [overlay_effect, patch_effect]
        assert [effect.effect_type for effect in bundle.lifecycle_effects[:-1]] == ["agent_message"]
        assert bundle.execution_metadata["fuse_overlay_mode"] is False


class TestContainerSandboxCleanup:
    """Tests for ContainerSandbox.cleanup() method."""

    def test_cleanup_calls_registered_callback(self) -> None:
        """cleanup() invokes the registered _cleanup_fn callback."""
        sandbox = ContainerSandbox.create("test-sandbox")

        cleanup_called = False

        def mock_cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        sandbox._cleanup_fn = mock_cleanup

        sandbox.cleanup()

        assert cleanup_called

    def test_cleanup_without_callback_logs_warning(self, caplog) -> None:
        """cleanup() logs warning when no callback is registered."""
        sandbox = ContainerSandbox.create("orphan-sandbox")
        # No _cleanup_fn set (default is None)

        sandbox.cleanup()

        assert "no cleanup function registered" in caplog.text
        assert "orphan-sandbox" in caplog.text

    def test_cleanup_callback_receives_sandbox_context(self) -> None:
        """Cleanup callback can access sandbox state via closure."""
        sandbox = ContainerSandbox.create("closure-test")
        sandbox.container_id = "container-123"

        captured_container_id = None

        def cleanup_with_context() -> None:
            nonlocal captured_container_id
            captured_container_id = sandbox.container_id

        sandbox._cleanup_fn = cleanup_with_context

        sandbox.cleanup()

        assert captured_container_id == "container-123"

    def test_scope_discard_triggers_real_sandbox_cleanup(self) -> None:
        """scope.discard() triggers cleanup() on real ContainerSandbox."""
        with Scope() as scope:
            child = scope.fork()

            sandbox = ContainerSandbox.create("real-sandbox")

            cleanup_invoked = False

            def track_cleanup() -> None:
                nonlocal cleanup_invoked
                cleanup_invoked = True

            sandbox._cleanup_fn = track_cleanup
            child.register_sandbox(sandbox)

            assert not cleanup_invoked

            child.discard()

            assert cleanup_invoked


class TestGenerateDiff:
    """Tests for OverlayEffectExtractor._generate_diff()."""

    def test_diff_has_relative_paths(self, tmp_path: Path) -> None:
        """Diff output uses --- a/file and +++ b/file, not absolute paths."""
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor

        lower = tmp_path / "lower"
        upper = tmp_path / "upper"
        work = tmp_path / "work"
        merged = tmp_path / "merged"
        for d in (lower, upper, work, merged):
            d.mkdir()

        (lower / "hello.txt").write_text("old\n")
        (upper / "hello.txt").write_text("new\n")

        overlay = OverlayMount(
            task_id="test",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=False,
            original_host_path=lower,
        )

        diff = OverlayEffectExtractor()._generate_diff(overlay)
        assert "--- a/hello.txt" in diff
        assert "+++ b/hello.txt" in diff
        assert str(tmp_path) not in diff

    def test_diff_includes_new_files(self, tmp_path: Path) -> None:
        """A file only in upper produces --- /dev/null + +++ b/file."""
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor

        lower = tmp_path / "lower"
        upper = tmp_path / "upper"
        work = tmp_path / "work"
        merged = tmp_path / "merged"
        for d in (lower, upper, work, merged):
            d.mkdir()

        (upper / "brand_new.txt").write_text("content\n")

        overlay = OverlayMount(
            task_id="test",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=False,
            original_host_path=lower,
        )

        diff = OverlayEffectExtractor()._generate_diff(overlay)
        assert "--- /dev/null" in diff
        assert "+++ b/brand_new.txt" in diff

    def test_diff_excludes_git_dir(self, tmp_path: Path) -> None:
        """.git/config in upper is not included in diff output."""
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor

        lower = tmp_path / "lower"
        upper = tmp_path / "upper"
        work = tmp_path / "work"
        merged = tmp_path / "merged"
        for d in (lower, upper, work, merged):
            d.mkdir()

        git_dir = upper / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n")

        overlay = OverlayMount(
            task_id="test",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=False,
            original_host_path=lower,
        )

        diff = OverlayEffectExtractor()._generate_diff(overlay)
        assert ".git" not in diff

    def test_diff_handles_binary_files(self, tmp_path: Path) -> None:
        """Non-UTF-8 bytes in upper don't cause an exception."""
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor

        lower = tmp_path / "lower"
        upper = tmp_path / "upper"
        work = tmp_path / "work"
        merged = tmp_path / "merged"
        for d in (lower, upper, work, merged):
            d.mkdir()

        (upper / "binary.bin").write_bytes(b"\x80\x81\x82\xff\xfe")

        overlay = OverlayMount(
            task_id="test",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=False,
            original_host_path=lower,
        )

        # Should not raise
        diff = OverlayEffectExtractor()._generate_diff(overlay)
        assert isinstance(diff, str)

    def test_diff_handles_modified_files(self, tmp_path: Path) -> None:
        """File present in both lower and upper produces correct unified diff."""
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor

        lower = tmp_path / "lower"
        upper = tmp_path / "upper"
        work = tmp_path / "work"
        merged = tmp_path / "merged"
        for d in (lower, upper, work, merged):
            d.mkdir()

        (lower / "mod.txt").write_text("line1\nline2\n")
        (upper / "mod.txt").write_text("line1\nchanged\n")

        overlay = OverlayMount(
            task_id="test",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=False,
            original_host_path=lower,
        )

        diff = OverlayEffectExtractor()._generate_diff(overlay)
        assert "-line2" in diff
        assert "+changed" in diff

    def test_diff_is_git_apply_compatible(self, tmp_path: Path) -> None:
        """Generated diff passes git apply --check in a real git repo."""
        import subprocess

        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor

        lower = tmp_path / "lower"
        upper = tmp_path / "upper"
        work = tmp_path / "work"
        merged = tmp_path / "merged"
        for d in (lower, upper, work, merged):
            d.mkdir()

        (lower / "file.txt").write_text("original\n")
        (upper / "file.txt").write_text("modified\n")

        overlay = OverlayMount(
            task_id="test",
            context_name="workspace",
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=False,
            original_host_path=lower,
        )

        diff = OverlayEffectExtractor()._generate_diff(overlay)

        # Create a git repo with the original file and apply the diff
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
        (repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"],
            capture_output=True,
            check=True,
            env={
                **__import__("os").environ,
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )

        result = subprocess.run(
            ["git", "-C", str(repo), "apply", "--check"],
            input=diff.encode(),
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"git apply --check failed: {result.stderr.decode()}"


class TestFuseWorkspaceHandshake:
    """Tests for the Phase 4 workspace layer handshake."""

    def test_generate_rebind_env_uses_workspace_mount_and_layer_order(self) -> None:
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice.__new__(ContainerDevice)
        device.use_fuse_workspace = True

        sandbox = ContainerSandbox.create("task-phase4")
        sandbox.overlays["workspace"] = OverlayMount(
            task_id="task-phase4",
            context_name="workspace",
            lower=[Path("/parent"), Path("/base")],
            upper=Path("/upper"),
            work=Path("/work"),
            merged=Path("/merged"),
        )
        sandbox.overlays["session"] = OverlayMount(
            task_id="task-phase4",
            context_name="session",
            lower=Path("/session-base"),
            upper=Path("/session-upper"),
            work=Path("/session-work"),
            merged=Path("/session-merged"),
        )

        env = device._generate_rebind_env(sandbox)

        assert env["WORKSPACE_PATH"] == "/workspace"
        assert env["SESSION_PATH"] == "/root/.claude"
        assert env["SHEPHERD_LAYERS"] == "parent_0:base"

    def test_generate_rebind_env_preserves_legacy_workspace_path_when_disabled(self) -> None:
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice.__new__(ContainerDevice)
        device.use_fuse_workspace = False

        sandbox = ContainerSandbox.create("task-legacy")
        sandbox.overlays["workspace"] = OverlayMount(
            task_id="task-legacy",
            context_name="workspace",
            lower=Path("/base"),
            upper=Path("/upper"),
            work=Path("/work"),
            merged=Path("/merged"),
        )

        env = device._generate_rebind_env(sandbox)

        assert env["WORKSPACE_PATH"] == "/container/workspace"
        assert "SHEPHERD_LAYERS" not in env

    def test_mount_workspace_layers_adds_raw_bind_mounts(self) -> None:
        from shepherd_runtime.device.container.podman import PodmanSandboxManager

        manager = PodmanSandboxManager.__new__(PodmanSandboxManager)
        cmd = ["podman", "create"]
        overlay = OverlayMount(
            task_id="task-phase4",
            context_name="workspace",
            lower=[Path("/parent-a"), Path("/parent-b"), Path("/base")],
            upper=Path("/upper"),
            work=Path("/work"),
            merged=Path("/merged"),
        )

        manager._mount_workspace_layers(cmd, overlay)

        assert "/base:/layers/base:ro" in cmd
        assert "/parent-a:/layers/parent_0:ro" in cmd
        assert "/parent-b:/layers/parent_1:ro" in cmd
