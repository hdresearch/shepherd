"""End-to-end tests for container execution.

These tests validate the complete container execution pipeline with actual
Podman containers. They require Podman to be installed and running.

Tests are organized into two classes:
- TestContainerExecution: Low-level tests using PodmanSandboxManager directly
- TestContainerDeviceE2E: High-level tests using ContainerDevice

Use `pytest -m e2e` to run only these tests.
Use `pytest -m "not e2e"` to skip these tests.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from shepherd_core.effects import FileCreate, FileDelete, FilePatch
from shepherd_core.foundation.protocols.device import SandboxConfig
from shepherd_runtime.device.container.device import ContainerDevice
from shepherd_runtime.device.container.effect_collector import EffectCollector
from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
from shepherd_runtime.device.container.podman import (
    ContainerSandbox,
    PodmanSandboxManager,
)

from .conftest import requires_fuse_workspace, requires_podman

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# Constants
# =============================================================================

CONTAINER_WORKSPACE_PATH = "/container/workspace"
FUSE_CONTAINER_WORKSPACE_PATH = "/workspace"
CONTAINER_TIMEOUT = 30  # seconds
CONTAINER_WAIT_TIMEOUT = 60  # seconds


def assert_container_exit_zero(
    manager: PodmanSandboxManager, sandbox: ContainerSandbox, exit_code: int, context: str
) -> None:
    """Assert a container succeeded, including logs when it did not."""
    if exit_code == 0:
        return
    logs = manager.get_container_logs(sandbox).strip()
    pytest.fail(f"{context} exited with code {exit_code}\nContainer logs:\n{logs or '<empty>'}")


def fuse_wrapped_shell_command(shell_script: str) -> list[str]:
    """Run a shell command after initializing the in-container fuse workspace."""
    bootstrap = textwrap.dedent(
        f"""
        import subprocess
        import sys
        from pathlib import Path

        from shepherd_runtime.device.container.fuse_overlay import FuseOverlayManager

        layers_root = Path("/layers")
        lower_layers = sorted(
            layers_root.glob("parent_*"),
            key=lambda path: int(path.name.split("_")[1]),
        )
        base_layer = layers_root / "base"
        if base_layer.exists():
            lower_layers.append(base_layer)

        overlay = FuseOverlayManager()
        overlay.setup(lower_layers=lower_layers or None)
        try:
            result = subprocess.run(["sh", "-lc", {shell_script!r}], check=False)
        finally:
            overlay.teardown()
        sys.exit(result.returncode)
        """
    )
    return ["python", "-c", bootstrap]


# =============================================================================
# Low-Level Container Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.container
@requires_podman
class TestContainerExecution:
    """Low-level container tests using PodmanSandboxManager directly.

    These tests validate:
    - Container lifecycle (create, start, wait, remove)
    - Overlay mounting and visibility
    - Copy-on-write isolation
    - Effect extraction for file operations
    """

    def test_container_runs_simple_command(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        unique_task_id: str,
    ) -> None:
        """Test that a container can run a simple command and exit.

        Validates:
        - Container creation with workspace overlay
        - Container start and wait
        - Successful command execution
        - Clean container removal
        """
        # Create overlay for workspace
        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="workspace",
            base_path=workspace,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["workspace"] = overlay

        try:
            # Mount overlay
            manager.mount_overlay(overlay)

            # Create and run container with simple echo command
            manager.create_container(
                sandbox,
                command=["sh", "-c", "echo 'hello from container'"],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)

            # Verify successful execution
            assert exit_code == 0, f"Container exited with code {exit_code}"

            # Get logs to verify output
            logs = manager.get_container_logs(sandbox)
            assert "hello from container" in logs

        finally:
            manager.cleanup(sandbox)

    def test_workspace_files_visible_in_container(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        unique_task_id: str,
    ) -> None:
        """Test that workspace files are visible inside the container.

        Validates:
        - Overlay lower layer (workspace) is accessible
        - Files created before container start are visible
        - Container can read workspace content
        """
        # Create a test file in workspace
        test_file = workspace / "test_file.txt"
        test_file.write_text("workspace content")

        # Create overlay
        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="workspace",
            base_path=workspace,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["workspace"] = overlay

        try:
            manager.mount_overlay(overlay)

            # Run container that reads the workspace file
            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f"cat {CONTAINER_WORKSPACE_PATH}/test_file.txt",
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)

            assert exit_code == 0, f"Container exited with code {exit_code}"

            logs = manager.get_container_logs(sandbox)
            assert "workspace content" in logs

        finally:
            manager.cleanup(sandbox)

    def test_container_changes_isolated_from_host(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        unique_task_id: str,
    ) -> None:
        """Test that container changes don't affect the host workspace.

        Validates:
        - Copy-on-write isolation via OverlayFS
        - Changes in container go to upper layer
        - Original workspace files unchanged
        """
        # Create a file in workspace that will be modified
        original_file = workspace / "original.txt"
        original_file.write_text("original content")

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="workspace",
            base_path=workspace,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["workspace"] = overlay

        try:
            manager.mount_overlay(overlay)

            # Run container that modifies the file
            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f'echo "modified content" > {CONTAINER_WORKSPACE_PATH}/original.txt',
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)

            # Remove container before unmount (required for clean unmount)
            manager.remove_container(sandbox)

            # Unmount overlay
            manager.unmount_overlay(overlay)

            assert exit_code == 0, f"Container exited with code {exit_code}"

            # Verify original file is unchanged
            assert original_file.read_text() == "original content"

        finally:
            manager.cleanup(sandbox)

    def test_effect_extraction_file_create(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        unique_task_id: str,
    ) -> None:
        """Test extraction of FileCreate effect from overlay.

        Validates:
        - New file created in container appears in upper layer
        - OverlayEffectExtractor correctly identifies FileCreate
        - Effect has correct path and content
        """
        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="workspace",
            base_path=workspace,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["workspace"] = overlay

        try:
            manager.mount_overlay(overlay)

            # Run container that creates a new file
            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f'echo "new file content" > {CONTAINER_WORKSPACE_PATH}/created.txt',
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)
            assert exit_code == 0, f"Container exited with code {exit_code}"

            # Remove container before unmount
            manager.remove_container(sandbox)

            # Unmount overlay before extraction
            manager.unmount_overlay(overlay)

            # Extract effects
            extractor = OverlayEffectExtractor(vm_runner=manager._vm_runner)
            collector = EffectCollector()
            collector._last_completed_intent_id = "test-intent-1"

            effects = extractor.extract(overlay, collector)

            # Verify FileCreate effect
            assert len(effects) == 1, f"Expected 1 effect, got {len(effects)}: {effects}"
            assert isinstance(effects[0], FileCreate)
            assert effects[0].path == "created.txt"
            assert "new file content" in effects[0].content
            assert effects[0].caused_by == "test-intent-1"

        finally:
            manager.cleanup(sandbox)

    def test_effect_extraction_file_modify(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        unique_task_id: str,
    ) -> None:
        """Test extraction of FilePatch effect from overlay.

        Validates:
        - Modified file appears in upper layer
        - OverlayEffectExtractor correctly identifies FilePatch
        - Effect has correct old and new content
        """
        # Create original file in workspace
        original = workspace / "existing.txt"
        original.write_text("original content")

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="workspace",
            base_path=workspace,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["workspace"] = overlay

        try:
            manager.mount_overlay(overlay)

            # Run container that modifies the file
            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f'echo "modified content" > {CONTAINER_WORKSPACE_PATH}/existing.txt',
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)
            assert exit_code == 0, f"Container exited with code {exit_code}"

            # Remove container before unmount
            manager.remove_container(sandbox)

            # Unmount overlay before extraction
            manager.unmount_overlay(overlay)

            # Extract effects
            extractor = OverlayEffectExtractor(vm_runner=manager._vm_runner)
            collector = EffectCollector()
            collector._last_completed_intent_id = "test-intent-2"

            effects = extractor.extract(overlay, collector)

            # Verify FilePatch effect
            assert len(effects) == 1, f"Expected 1 effect, got {len(effects)}: {effects}"
            assert isinstance(effects[0], FilePatch)
            assert effects[0].path == "existing.txt"
            assert effects[0].old_content == "original content"
            assert "modified content" in effects[0].new_content
            assert effects[0].caused_by == "test-intent-2"

        finally:
            manager.cleanup(sandbox)

    def test_effect_extraction_file_delete(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        unique_task_id: str,
    ) -> None:
        """Test extraction of FileDelete effect from overlay.

        Validates:
        - Deleted file creates whiteout in upper layer
        - OverlayEffectExtractor correctly identifies FileDelete
        - Effect has correct path and original content
        """
        # Create file to be deleted
        to_delete = workspace / "to_delete.txt"
        to_delete.write_text("content to delete")

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="workspace",
            base_path=workspace,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["workspace"] = overlay

        try:
            manager.mount_overlay(overlay)

            # Run container that deletes the file
            manager.create_container(
                sandbox,
                command=[
                    "sh",
                    "-c",
                    f"rm {CONTAINER_WORKSPACE_PATH}/to_delete.txt",
                ],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)
            assert exit_code == 0, f"Container exited with code {exit_code}"

            # Remove container before unmount
            manager.remove_container(sandbox)

            # Unmount overlay before extraction
            manager.unmount_overlay(overlay)

            # Extract effects
            extractor = OverlayEffectExtractor(vm_runner=manager._vm_runner)
            collector = EffectCollector()
            collector._last_completed_intent_id = "test-intent-3"

            effects = extractor.extract(overlay, collector)

            # Verify FileDelete effect
            assert len(effects) == 1, f"Expected 1 effect, got {len(effects)}: {effects}"
            assert isinstance(effects[0], FileDelete)
            assert effects[0].path == "to_delete.txt"
            assert effects[0].had_content == "content to delete"
            assert effects[0].caused_by == "test-intent-3"

        finally:
            manager.cleanup(sandbox)

    def test_multiple_file_operations(
        self,
        manager: PodmanSandboxManager,
        workspace: Path,
        unique_task_id: str,
    ) -> None:
        """Test extraction of multiple file effects from single container run.

        Validates:
        - Multiple operations (create, modify, delete) in one container
        - All effects correctly extracted
        - Effects correctly typed and attributed
        """
        # Create files for modification and deletion
        (workspace / "modify_me.txt").write_text("modify this")
        (workspace / "delete_me.txt").write_text("delete this")

        overlay = manager.create_overlay(
            task_id=unique_task_id,
            context_name="workspace",
            base_path=workspace,
        )

        sandbox = ContainerSandbox.create(unique_task_id)
        sandbox.overlays["workspace"] = overlay

        try:
            manager.mount_overlay(overlay)

            # Run container with multiple file operations
            script = f"""
echo "new file" > {CONTAINER_WORKSPACE_PATH}/new_file.txt
echo "modified" > {CONTAINER_WORKSPACE_PATH}/modify_me.txt
rm {CONTAINER_WORKSPACE_PATH}/delete_me.txt
"""
            manager.create_container(
                sandbox,
                command=["sh", "-c", script],
            )
            manager.start_container(sandbox)
            exit_code = manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)
            assert exit_code == 0, f"Container exited with code {exit_code}"

            # Remove container before unmount
            manager.remove_container(sandbox)

            # Unmount overlay before extraction
            manager.unmount_overlay(overlay)

            # Extract effects
            extractor = OverlayEffectExtractor(vm_runner=manager._vm_runner)
            collector = EffectCollector()
            collector._last_completed_intent_id = "test-intent-multi"

            effects = extractor.extract(overlay, collector)

            # Verify we got all three effects
            assert len(effects) == 3, f"Expected 3 effects, got {len(effects)}: {effects}"

            # Categorize by type
            creates = [e for e in effects if isinstance(e, FileCreate)]
            patches = [e for e in effects if isinstance(e, FilePatch)]
            deletes = [e for e in effects if isinstance(e, FileDelete)]

            assert len(creates) == 1, f"Expected 1 FileCreate, got {len(creates)}"
            assert len(patches) == 1, f"Expected 1 FilePatch, got {len(patches)}"
            assert len(deletes) == 1, f"Expected 1 FileDelete, got {len(deletes)}"

            # Verify specific effects
            assert creates[0].path == "new_file.txt"
            assert patches[0].path == "modify_me.txt"
            assert deletes[0].path == "delete_me.txt"

            # All effects should have causality link
            for effect in effects:
                assert effect.caused_by == "test-intent-multi"

        finally:
            manager.cleanup(sandbox)


# =============================================================================
# High-Level ContainerDevice Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.container
@requires_podman
@requires_fuse_workspace
class TestContainerDeviceE2E:
    """High-level end-to-end tests using ContainerDevice.

    These tests validate the full DeviceProtocol contract:
    - create_sandbox() with overlay setup
    - execute() with actual container execution
    - extract_effects() with effect bundle generation
    - cleanup() with resource release
    """

    @pytest.mark.asyncio
    async def test_device_execute_with_mock_provider(
        self,
        mock_scope: MagicMock,
        workspace: Path,
        temp_overlays: Path,
    ) -> None:
        """Test ContainerDevice execute() with simulated execution.

        This test validates the DeviceProtocol contract by:
        1. Creating a sandbox with overlays
        2. Simulating file creation (via VM runner for macOS)
        3. Extracting effects from the overlay

        Does not run a full task_runner - tests orchestration only.
        """
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create a test file in workspace
        (workspace / "existing.txt").write_text("workspace file")

        # Create sandbox
        config = SandboxConfig(
            context_states={
                "workspace": MagicMock(path=str(workspace)),
            },
        )

        sandbox = await device.create_sandbox(mock_scope, config)

        try:
            assert sandbox.sandbox_id is not None
            assert "workspace" in sandbox.overlays

            overlay = sandbox.overlays["workspace"]

            # Simulate what a container would do: create a file in upper layer
            if overlay.is_vm_path:
                # macOS: write via VM runner
                assert device.manager._vm_runner is not None
                device.manager._vm_runner.run(f'echo "created by agent" > "{overlay.upper}/agent_output.py"')
            else:
                # Linux: write directly
                (overlay.upper / "agent_output.py").write_text("created by agent")

            # Create mock result with collector
            from shepherd_core.effects import ToolCallCompleted
            from shepherd_core.foundation.protocols.device import ExecutionResult

            collector = EffectCollector(_id="e2e-test")
            tool_effect = ToolCallCompleted(
                tool_call_id="tc-write",
                tool_name="write_file",
                output="File created",
            )
            collector.emit(tool_effect)

            result = ExecutionResult(
                success=True,
                output_text="Task completed",
                metadata={"_collector": collector.serialize_for_transport()},
            )

            # Extract effects
            bundle = await device.extract_effects(sandbox, result)

            # Verify effects extracted
            assert "workspace" in bundle.context_effects
            workspace_effects = bundle.context_effects["workspace"]

            # Should have FileCreate for agent_output.py
            file_creates = [e for e in workspace_effects if isinstance(e, FileCreate) and "agent_output.py" in e.path]
            assert len(file_creates) >= 1, f"Expected FileCreate for agent_output.py: {workspace_effects}"

        finally:
            await device.cleanup(sandbox, force=True)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_device_full_lifecycle(
        self,
        mock_scope: MagicMock,
        workspace: Path,
        temp_overlays: Path,
    ) -> None:
        """Test complete ContainerDevice lifecycle with real container.

        This test validates the full integration:
        1. create_sandbox() - creates overlays and task directory
        2. Manual container execution via manager (simulating execute())
        3. extract_effects() - extracts changes from overlay
        4. cleanup() - releases all resources

        Note: Does not test full execute() as that requires task_runner setup.
        """
        device = ContainerDevice(overlays_root=temp_overlays)

        # Create workspace content
        (workspace / "readme.md").write_text("# Test Project\n")
        (workspace / "main.py").write_text("print('hello')\n")

        # 1. Create sandbox
        config = SandboxConfig(
            context_states={
                "workspace": MagicMock(path=str(workspace)),
            },
        )

        sandbox = await device.create_sandbox(mock_scope, config)

        try:
            assert sandbox.sandbox_id is not None
            assert sandbox.task_dir is not None
            assert sandbox.task_dir.exists()
            assert "workspace" in sandbox.overlays

            overlay = sandbox.overlays["workspace"]

            # 2. Run actual container that modifies files
            # Mount is already done by create_sandbox
            device.manager.create_container(
                sandbox,
                command=fuse_wrapped_shell_command(
                    f"""
echo '# Updated readme' >> {FUSE_CONTAINER_WORKSPACE_PATH}/readme.md
echo "print('goodbye')" > {FUSE_CONTAINER_WORKSPACE_PATH}/new_script.py
                    """
                ),
                use_fuse_workspace=device.use_fuse_workspace,
            )
            device.manager.start_container(sandbox)
            exit_code = device.manager.wait_container(sandbox, timeout=CONTAINER_WAIT_TIMEOUT)
            assert_container_exit_zero(device.manager, sandbox, exit_code, "Container")

            # Remove container before extraction
            device.manager.remove_container(sandbox)

            # Unmount overlay before extraction
            device.manager.unmount_overlay(overlay)

            # 3. Extract effects
            from shepherd_core.effects import ToolCallCompleted
            from shepherd_core.foundation.protocols.device import ExecutionResult

            collector = EffectCollector(_id="lifecycle-test")
            collector.emit(
                ToolCallCompleted(
                    tool_call_id="tc-edit",
                    tool_name="edit_file",
                    output="Files modified",
                )
            )

            result = ExecutionResult(
                success=True,
                output_text="Execution complete",
                metadata={"_collector": collector.serialize_for_transport()},
            )

            bundle = await device.extract_effects(sandbox, result)

            # Verify effects
            assert "workspace" in bundle.context_effects
            effects = bundle.context_effects["workspace"]
            assert len(effects) >= 1, f"Expected workspace patch effects: {effects}"

            patch_effects = [effect for effect in effects if effect.__class__.__name__ == "WorkspacePatchCaptured"]
            assert patch_effects, f"Expected WorkspacePatchCaptured effect: {effects}"
            patch_text = patch_effects[0].patch.patch
            assert "readme.md" in patch_text
            assert "new_script.py" in patch_text

            # 4. Cleanup
            await device.cleanup(sandbox, force=True)

            # Verify cleanup happened
            assert sandbox.task_dir is None
            assert sandbox.overlays == {}

        except Exception:
            # Ensure cleanup on failure
            await device.cleanup(sandbox, force=True)
            raise

    @pytest.mark.asyncio
    async def test_sequential_overlay_stacking(
        self,
        workspace: Path,
        temp_overlays: Path,
    ) -> None:
        """Task B sees Task A's files via stacked overlays.

        Exercises the full stacking lifecycle:
        1. Task A creates a file in its overlay upper layer
        2. extract_effects() emits ContainerExecutionCompleted
        3. Task B's create_sandbox() finds A's sandbox via scope
        4. Task B's container can read A's file
        5. _generate_diff() produces git-apply-compatible output
        """
        import subprocess as sp

        from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
        from shepherd_core.effects import ContainerExecutionCompleted, ToolCallCompleted
        from shepherd_core.foundation.protocols.device import ExecutionResult
        from shepherd_runtime.scope import Scope

        device = ContainerDevice(overlays_root=temp_overlays)

        # Seed workspace with a base file
        (workspace / "base.txt").write_text("base content\n")

        with Scope() as scope:
            scope.bind("workspace", MagicMock(path=str(workspace)))

            # ---- Task A: create utils.py ----

            config_a = SandboxConfig(
                context_states={"workspace": MagicMock(path=str(workspace))},
            )
            sandbox_a = await device.create_sandbox(scope, config_a)

            try:
                overlay_a = sandbox_a.overlays["workspace"]

                # Run container A: create utils.py
                device.manager.create_container(
                    sandbox_a,
                    command=fuse_wrapped_shell_command(
                        f'echo "def helper(): return 42" > {FUSE_CONTAINER_WORKSPACE_PATH}/utils.py'
                    ),
                    use_fuse_workspace=device.use_fuse_workspace,
                )
                device.manager.start_container(sandbox_a)
                exit_a = device.manager.wait_container(sandbox_a, timeout=CONTAINER_WAIT_TIMEOUT)
                assert_container_exit_zero(device.manager, sandbox_a, exit_a, "Container A")
                device.manager.remove_container(sandbox_a)
                device.manager.unmount_overlay(overlay_a)

                # Verify utils.py appeared in A's upper layer
                effective_layer_a = sandbox_a.get_workspace_layers()[0]
                assert (effective_layer_a / "utils.py").exists(), "utils.py not in effective workspace layer"

                # Extract effects for A (emits ContainerExecutionCompleted)
                collector_a = EffectCollector(_id="task-a")
                collector_a.emit(
                    ToolCallCompleted(
                        tool_call_id="tc-write-a",
                        tool_name="write_file",
                        output="ok",
                    )
                )
                result_a = ExecutionResult(
                    success=True,
                    output_text="done",
                    metadata={"_collector": collector_a.serialize_for_transport()},
                )
                bundle_a = await device.extract_effects(sandbox_a, result_a)

                # Push effects into scope (mimics _apply_effect_bundle)
                for effect in bundle_a.lifecycle_effects:
                    scope.emit(effect)
                for effects in bundle_a.context_effects.values():
                    for effect in effects:
                        scope.emit(effect)

                # Verify ContainerExecutionCompleted is in the stream
                completion_effects = [
                    e.effect for e in scope.effects if isinstance(e.effect, ContainerExecutionCompleted)
                ]
                assert len(completion_effects) == 1
                assert completion_effects[0].sandbox_id == sandbox_a.sandbox_id

                # Verify scope can find A's sandbox for stacking
                found = scope.get_latest_sandbox_for_context("workspace")
                assert found is sandbox_a

                # Verify _generate_diff produced git-apply-compatible output
                patches_a = [e.effect for e in scope.effects if isinstance(e.effect, WorkspacePatchCaptured)]
                assert len(patches_a) >= 1, "No WorkspacePatchCaptured from Task A"
                patch_text_a = patches_a[0].patch.patch
                assert "utils.py" in patch_text_a
                assert "--- /dev/null" in patch_text_a or "--- a/" in patch_text_a
                assert "+++ b/utils.py" in patch_text_a
                # Verify no absolute paths leaked
                assert str(temp_overlays) not in patch_text_a
                assert str(workspace) not in patch_text_a

                # ---- Task B: read utils.py, create main.py ----

                config_b = SandboxConfig(
                    context_states={"workspace": MagicMock(path=str(workspace))},
                )
                sandbox_b = await device.create_sandbox(scope, config_b)

                try:
                    overlay_b = sandbox_b.overlays["workspace"]

                    # Verify Task B has parent reference
                    assert sandbox_b.parent_sandbox is sandbox_a

                    # Verify B's overlay has A's upper as a lower layer
                    lower_layers = overlay_b.lower_layers
                    assert effective_layer_a in lower_layers, (
                        f"A's effective layer {effective_layer_a} not in B's lower layers: {lower_layers}"
                    )

                    # Run container B: read utils.py and create main.py
                    device.manager.create_container(
                        sandbox_b,
                        command=fuse_wrapped_shell_command(
                            f"""
cat {FUSE_CONTAINER_WORKSPACE_PATH}/utils.py > /tmp/check.txt && \
echo "from utils import helper" > {FUSE_CONTAINER_WORKSPACE_PATH}/main.py && \
echo "print(helper())" >> {FUSE_CONTAINER_WORKSPACE_PATH}/main.py
                            """
                        ),
                        use_fuse_workspace=device.use_fuse_workspace,
                    )
                    device.manager.start_container(sandbox_b)
                    exit_b = device.manager.wait_container(sandbox_b, timeout=CONTAINER_WAIT_TIMEOUT)
                    assert_container_exit_zero(
                        device.manager,
                        sandbox_b,
                        exit_b,
                        "Container B, likely while reading utils.py from A's overlay",
                    )
                    device.manager.remove_container(sandbox_b)
                    device.manager.unmount_overlay(overlay_b)

                    # Verify main.py in B's upper layer
                    effective_layer_b = sandbox_b.get_workspace_layers()[0]
                    assert (effective_layer_b / "main.py").exists()
                    main_content = (effective_layer_b / "main.py").read_text()
                    assert "from utils import helper" in main_content

                    # Extract effects for B
                    collector_b = EffectCollector(_id="task-b")
                    collector_b.emit(
                        ToolCallCompleted(
                            tool_call_id="tc-write-b",
                            tool_name="write_file",
                            output="ok",
                        )
                    )
                    result_b = ExecutionResult(
                        success=True,
                        output_text="done",
                        metadata={"_collector": collector_b.serialize_for_transport()},
                    )
                    bundle_b = await device.extract_effects(sandbox_b, result_b)

                    # Verify workspace patch for B
                    b_patches = [
                        e
                        for e in bundle_b.context_effects.get("workspace", [])
                        if isinstance(e, WorkspacePatchCaptured)
                    ]
                    assert len(b_patches) >= 1, "No WorkspacePatchCaptured from Task B"
                    patch_text_b = b_patches[0].patch.patch
                    assert "+++ b/main.py" in patch_text_b
                    # B's diff should NOT include utils.py (that's A's change, not B's)
                    assert "utils.py" not in patch_text_b, (
                        "B's patch should only contain B's changes (main.py), not A's (utils.py)"
                    )

                    # Verify B's diff is git-apply-compatible
                    repo = workspace.parent / "apply-check-repo"
                    repo.mkdir()
                    sp.run(["git", "init", str(repo)], capture_output=True, check=True)
                    sp.run(
                        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
                        capture_output=True,
                        check=True,
                        env={
                            **__import__("os").environ,
                            "GIT_AUTHOR_NAME": "t",
                            "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t",
                        },
                    )
                    apply_result = sp.run(
                        ["git", "-C", str(repo), "apply", "--check"],
                        input=patch_text_b.encode(),
                        capture_output=True,
                        check=False,
                    )
                    assert apply_result.returncode == 0, (
                        f"git apply --check failed on B's patch:\n"
                        f"{apply_result.stderr.decode()}\n"
                        f"Patch content:\n{patch_text_b}"
                    )

                finally:
                    await device.cleanup(sandbox_b, force=True)

            finally:
                await device.cleanup(sandbox_a, force=True)

    @pytest.mark.asyncio
    async def test_task_b_modifies_task_a_file(
        self,
        workspace: Path,
        temp_overlays: Path,
    ) -> None:
        """When Task B modifies a file created by Task A, the diff is correct.

        Task A creates utils.py.  Task B modifies utils.py (adds a line).
        B's diff should show the file as created-from-scratch relative to
        the original workspace (since utils.py didn't exist there), and the
        diff must still be git-apply-compatible.
        """
        import subprocess as sp

        from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
        from shepherd_core.effects import ToolCallCompleted
        from shepherd_core.foundation.protocols.device import ExecutionResult
        from shepherd_runtime.scope import Scope

        device = ContainerDevice(overlays_root=temp_overlays)

        with Scope() as scope:
            scope.bind("workspace", MagicMock(path=str(workspace)))

            # ---- Task A: create utils.py ----
            config_a = SandboxConfig(
                context_states={"workspace": MagicMock(path=str(workspace))},
            )
            sandbox_a = await device.create_sandbox(scope, config_a)

            try:
                overlay_a = sandbox_a.overlays["workspace"]
                device.manager.create_container(
                    sandbox_a,
                    command=fuse_wrapped_shell_command(
                        f'echo "def helper(): return 42" > {FUSE_CONTAINER_WORKSPACE_PATH}/utils.py'
                    ),
                    use_fuse_workspace=device.use_fuse_workspace,
                )
                device.manager.start_container(sandbox_a)
                exit_a = device.manager.wait_container(sandbox_a, timeout=CONTAINER_WAIT_TIMEOUT)
                assert_container_exit_zero(device.manager, sandbox_a, exit_a, "Container A")
                device.manager.remove_container(sandbox_a)
                device.manager.unmount_overlay(overlay_a)

                collector_a = EffectCollector(_id="task-a")
                collector_a.emit(
                    ToolCallCompleted(
                        tool_call_id="tc-a",
                        tool_name="write",
                        output="ok",
                    )
                )
                bundle_a = await device.extract_effects(
                    sandbox_a,
                    ExecutionResult(
                        success=True,
                        output_text="ok",
                        metadata={"_collector": collector_a.serialize_for_transport()},
                    ),
                )
                for effect in bundle_a.lifecycle_effects:
                    scope.emit(effect)
                for effects in bundle_a.context_effects.values():
                    for effect in effects:
                        scope.emit(effect)

                # ---- Task B: modify utils.py ----
                config_b = SandboxConfig(
                    context_states={"workspace": MagicMock(path=str(workspace))},
                )
                sandbox_b = await device.create_sandbox(scope, config_b)

                try:
                    overlay_b = sandbox_b.overlays["workspace"]
                    device.manager.create_container(
                        sandbox_b,
                        command=fuse_wrapped_shell_command(
                            f'echo "def extra(): return 99" >> {FUSE_CONTAINER_WORKSPACE_PATH}/utils.py'
                        ),
                        use_fuse_workspace=device.use_fuse_workspace,
                    )
                    device.manager.start_container(sandbox_b)
                    exit_b = device.manager.wait_container(sandbox_b, timeout=CONTAINER_WAIT_TIMEOUT)
                    assert_container_exit_zero(device.manager, sandbox_b, exit_b, "Container B")
                    device.manager.remove_container(sandbox_b)
                    device.manager.unmount_overlay(overlay_b)

                    # B's upper should have utils.py (copy-on-write from A)
                    effective_layer_b = sandbox_b.get_workspace_layers()[0]
                    assert (effective_layer_b / "utils.py").exists()
                    b_content = (effective_layer_b / "utils.py").read_text()
                    assert "extra" in b_content, "B's modification missing"
                    assert "helper" in b_content, "A's content missing from B's copy"

                    collector_b = EffectCollector(_id="task-b")
                    collector_b.emit(
                        ToolCallCompleted(
                            tool_call_id="tc-b",
                            tool_name="write",
                            output="ok",
                        )
                    )
                    bundle_b = await device.extract_effects(
                        sandbox_b,
                        ExecutionResult(
                            success=True,
                            output_text="ok",
                            metadata={"_collector": collector_b.serialize_for_transport()},
                        ),
                    )

                    # Check B's workspace patch
                    b_patches = [
                        e
                        for e in bundle_b.context_effects.get("workspace", [])
                        if isinstance(e, WorkspacePatchCaptured)
                    ]
                    assert len(b_patches) >= 1
                    patch_text = b_patches[0].patch.patch
                    # utils.py doesn't exist in original workspace, so diff is vs /dev/null
                    assert "--- /dev/null" in patch_text
                    assert "+++ b/utils.py" in patch_text
                    # Should contain BOTH A's and B's content (full file as new)
                    assert "helper" in patch_text
                    assert "extra" in patch_text
                    # No absolute paths
                    assert str(temp_overlays) not in patch_text

                    # Must be git-apply-compatible
                    repo = workspace.parent / "apply-repo-2"
                    repo.mkdir()
                    sp.run(["git", "init", str(repo)], capture_output=True, check=True)
                    sp.run(
                        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
                        capture_output=True,
                        check=True,
                        env={
                            **__import__("os").environ,
                            "GIT_AUTHOR_NAME": "t",
                            "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t",
                        },
                    )
                    apply_result = sp.run(
                        ["git", "-C", str(repo), "apply", "--check"],
                        input=patch_text.encode(),
                        capture_output=True,
                        check=False,
                    )
                    assert apply_result.returncode == 0, (
                        f"git apply --check failed:\n{apply_result.stderr.decode()}\n{patch_text}"
                    )

                finally:
                    await device.cleanup(sandbox_b, force=True)

            finally:
                await device.cleanup(sandbox_a, force=True)
