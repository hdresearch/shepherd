"""ContainerDevice: DeviceProtocol implementation for containerized execution.

This module provides the ContainerDevice class that orchestrates:
- Sandbox creation with OverlayFS isolation
- Task execution in Podman containers
- Effect extraction from overlays with causality linking
- Resource cleanup

The ContainerDevice enables isolated execution where:
- Each task runs in its own container
- Filesystem changes are captured via OverlayFS
- Effects are linked to causing intents
- State can be stacked for hierarchical tasks

Architecture:
    ContainerDevice
    ├── create_sandbox()  →  PodmanSandboxManager.create_overlay()
    ├── execute()         →  Run task_runner.py in container
    ├── extract_effects() →  OverlayEffectExtractor.extract()
    └── cleanup()         →  PodmanSandboxManager.cleanup()

See Also:
    design/containerized-execution/PROPOSAL-containerized-execution-reconciliation.md
    packages/shepherd-core/src/shepherd_core/foundation/protocols/device.py
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shepherd_core.effects import ContainerExecutionCompleted, LifecyclePhaseCompleted
from shepherd_core.foundation.protocols.device import (
    ContextState,
    DeviceCapabilities,
    EffectBundle,
    EffectExtractionError,
    ExecutionResult,
    ExecutionSpec,
    SandboxConfig,
    SandboxCreationError,
    SandboxExecutionError,
)

from shepherd_runtime.device.container.effect_collector import EffectCollector
from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
from shepherd_runtime.device.container.podman import (
    ContainerSandbox,
    PodmanSandboxManager,
)

if TYPE_CHECKING:
    from shepherd_core.foundation.protocols.scope import ScopeProtocol

    from shepherd_runtime.device.transfer import TransferBundle


logger = logging.getLogger(__name__)

# Re-export ContainerSandbox for convenience
__all__ = ["ContainerDevice", "ContainerSandbox"]

# Production tmpfs path set up by .devcontainer/post-start.sh
_PROD_OVERLAY_TMPFS = Path("/tmp/shepherd-overlays")


def _default_overlays_root() -> Path:
    """Choose the best overlays root directory for the current platform.

    On macOS the Podman VM only shares ``/Users`` by default, so we use
    ``~/.shepherd/overlays``.  On Linux (e.g. devcontainers), the root FS
    is often itself an overlay, which prevents nested overlay mounts.  If
    ``/tmp/shepherd-overlays`` is a mount point (tmpfs set up by
    ``post-start.sh``) we use that.  Otherwise we probe whether ``$HOME``
    lives on overlayfs and, if so, create and return ``/tmp/shepherd-overlays``.
    """
    if platform.system() == "Darwin":
        return Path.home() / ".shepherd" / "overlays"

    # Linux: prefer the tmpfs mount if it exists
    if _PROD_OVERLAY_TMPFS.is_mount():
        return _PROD_OVERLAY_TMPFS

    # Detect if home lives on overlayfs (common in devcontainers)
    try:
        result = subprocess.run(
            ["stat", "-f", "-c", "%T", str(Path.home())],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and "overlay" in result.stdout.strip().lower():
            logger.warning(
                "Home directory is on overlayfs but %s is not a tmpfs mount. "
                "Overlay mounts may fail. Run post-start.sh or mount a tmpfs manually.",
                _PROD_OVERLAY_TMPFS,
            )
    except FileNotFoundError:
        pass

    return Path.home() / ".shepherd" / "overlays"


def _task_runner_module_name() -> str:
    """Return the runtime-owned task-runner module path."""
    return "shepherd_runtime.device.container.task_runner"


@dataclass
class ContainerDevice:
    """DeviceProtocol implementation for containerized execution.

    The ContainerDevice provides isolated execution environments using
    Podman containers with OverlayFS for filesystem isolation.

    Features:
    - Full container isolation (namespaces, cgroups)
    - Copy-on-write filesystem via OverlayFS
    - Effect extraction with causality linking
    - Hierarchical sandbox stacking

    Attributes:
        name: Device identifier.
        capabilities: What this device supports.
        overlays_root: Root directory for overlay storage.
        image: Default container image.
        _manager: PodmanSandboxManager instance.
        _extractor: OverlayEffectExtractor instance.
    """

    name: str = "container"
    capabilities: DeviceCapabilities = field(
        default_factory=lambda: DeviceCapabilities(
            isolation_level="container",
            effect_capture="overlay",
            supports_checkpoint=False,  # DMTCP deferred
            supports_restore=False,
            supports_dmtcp=False,
            supports_parallel=True,
        )
    )
    # Use ~/.shepherd/overlays instead of /tmp for macOS Podman VM compatibility
    # (Podman VM only shares /Users by default, not /tmp)
    overlays_root: Path = field(default_factory=lambda: _default_overlays_root())
    image: str = "shepherd-sandbox"

    # Debug mode: preserves containers/artifacts on failure, verbose logging
    # Can also be enabled via SHEPHERD_DEBUG=1 environment variable
    debug: bool = field(default_factory=lambda: os.environ.get("SHEPHERD_DEBUG", "").lower() in ("1", "true", "yes"))
    use_fuse_workspace: bool = True

    _manager: PodmanSandboxManager | None = field(default=None, repr=False)
    _extractor: OverlayEffectExtractor | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Initialize manager and extractor, clean up orphan overlays."""
        if self._manager is None:
            self._manager = PodmanSandboxManager(
                overlays_root=self.overlays_root,
                image=self.image,
            )
        if self._extractor is None:
            # Pass vm_runner to extractor for VM overlay extraction on macOS
            self._extractor = OverlayEffectExtractor(
                vm_runner=self._manager._vm_runner,
            )

        # Clean up orphan overlays from previous sessions/crashes
        # This prevents accumulation of stale mounts that cause memory issues
        self._cleanup_startup_orphans()

    def _cleanup_startup_orphans(self) -> None:
        """Clean orphan overlays from previous sessions.

        Called during __post_init__ to handle:
        - Crashes that left overlays mounted
        - Abnormal exits that didn't clean up
        - Previous sessions that used preserve_overlays=True

        This is a best-effort cleanup - errors are logged but don't prevent
        device initialization.
        """
        if self._manager is None:
            return

        try:
            # First, unmount any orphan overlays in the VM
            if self._manager._vm_runner is not None:
                try:
                    # Find and unmount all overlays in our namespace
                    result = self._manager._vm_runner.run(
                        f"mount | grep '{self._manager._vm_overlays_root}' | "
                        f"awk '{{print $3}}' | "
                        f"xargs -r -I{{}} sudo umount {{}} 2>/dev/null || true",
                        check=False,
                        timeout=30,
                    )
                    # Count unmounted overlays from output
                    if result.stdout.strip():
                        logger.debug("Unmounted orphan overlays at startup")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"Orphan overlay unmount (non-fatal): {e}")

            # Then clean up orphan directories (removes old task directories)
            # Use max_age_hours=0 to clean ALL orphans, not just old ones
            count = self._manager.cleanup_orphan_overlays(max_age_hours=0)
            if count > 0:
                logger.info(f"Cleaned {count} orphan overlay directory(ies) at startup")

        except Exception as e:  # noqa: BLE001
            # Startup cleanup is best-effort - don't fail device initialization
            logger.warning(f"Startup orphan cleanup failed (non-fatal): {e}")

    @property
    def manager(self) -> PodmanSandboxManager:
        """Get the sandbox manager."""
        if self._manager is None:
            self._manager = PodmanSandboxManager(
                overlays_root=self.overlays_root,
                image=self.image,
            )
        return self._manager

    @property
    def extractor(self) -> OverlayEffectExtractor:
        """Get the effect extractor."""
        if self._extractor is None:
            # Pass vm_runner from manager for VM overlay extraction
            self._extractor = OverlayEffectExtractor(
                vm_runner=self.manager._vm_runner,
            )
        return self._extractor

    # =========================================================================
    # DeviceProtocol Implementation
    # =========================================================================

    async def create_sandbox(
        self,
        scope: ScopeProtocol,
        config: SandboxConfig,
    ) -> ContainerSandbox:
        """Create isolated execution environment.

        Creates a sandbox with:
        - OverlayFS mounts for each context (with parent layering for workspace)
        - Task directory for I/O files
        - Container configuration

        Pre-flight validation is performed to catch common issues early:
        - Podman availability
        - Image existence
        - Disk space

        For workspace patch layering, finds parent sandbox via scope and stacks
        overlays so Task B sees Task A's changes without materialization.

        Args:
            scope: Parent scope (for inheriting configuration and sandbox tracking).
            config: Sandbox configuration with context states.

        Returns:
            ContainerSandbox handle.

        Raises:
            SandboxCreationError: If sandbox creation fails, with diagnostic context.

        See Also:
            PLAN-workspace-patch-layering.md (Change 2)
        """
        # Pre-flight validation (raises SandboxCreationError with clear message)
        self.manager.validate_environment()

        sandbox_id = str(uuid.uuid4())

        try:
            sandbox = ContainerSandbox.create(sandbox_id)

            # Find parent sandbox for overlay layering (workspace patch layering)
            # Uses scope's effect stream to find most recent container execution
            parent_sandbox = scope.get_latest_sandbox_for_context("workspace")  # type: ignore[attr-defined]
            sandbox.parent_sandbox = parent_sandbox

            # Get parent's workspace layers for stacking
            parent_layers: list[Path] | None = None
            if parent_sandbox is not None:
                parent_layers = parent_sandbox.get_workspace_layers()
                if parent_layers:
                    logger.debug(
                        f"Found parent sandbox {parent_sandbox.sandbox_id} with "
                        f"{len(parent_layers)} workspace layers for stacking"
                    )

            # Create task directory
            task_dir = self.overlays_root / sandbox_id / "task"
            task_dir.mkdir(parents=True, exist_ok=True)
            sandbox.task_dir = task_dir

            # Store context states for later serialization
            sandbox.context_states = dict(config.context_states)

            # Create overlays for each context
            for binding_name, state in config.context_states.items():
                # Get base path from state (if available)
                base_path = self._get_base_path(state)
                if base_path:
                    try:
                        # Use parent_layers for workspace layering
                        overlay = self.manager.create_overlay(
                            task_id=sandbox_id,
                            context_name=binding_name,
                            base_path=base_path,
                            parent_layers=parent_layers if binding_name == "workspace" else None,
                        )
                        sandbox.overlays[binding_name] = overlay

                        if not (self.use_fuse_workspace and binding_name == "workspace"):
                            self.manager.mount_overlay(overlay)
                    except subprocess.CalledProcessError as e:
                        raise SandboxCreationError.overlay_mount_failed(
                            mount_point=str(base_path),
                            reason=e.stderr or str(e),
                        ) from e

                # Create session overlay for ~/.claude/ if session context present
                # (Change 4 from PLAN-session-resumption-containers.md)
                ctx_type = getattr(state, "context_type", None)
                if ctx_type is None and isinstance(state, dict):
                    ctx_type = state.get("context_type")

                if binding_name == "session" or ctx_type == "session":
                    claude_dir = Path.home() / ".claude"

                    # Ensure ~/.claude exists (first-time users)
                    if not claude_dir.exists():
                        claude_dir.mkdir(parents=True, exist_ok=True)
                        logger.info("Created ~/.claude directory for session tracking")

                    # Handle macOS VM path translation (Podman runs in VM on macOS)
                    if self.manager._path_translator:
                        vm_claude_dir = self.manager._path_translator.host_to_vm(claude_dir)
                        base_path_for_overlay = vm_claude_dir
                    else:
                        base_path_for_overlay = claude_dir

                    try:
                        overlay = self.manager.create_overlay(
                            task_id=sandbox_id,
                            context_name="session",
                            base_path=base_path_for_overlay,
                            parent_task_id=config.parent_sandbox_id,
                        )
                        # Store original host path for symlink creation
                        overlay.original_host_path = claude_dir
                        sandbox.overlays["session"] = overlay
                        self.manager.mount_overlay(overlay)
                        logger.debug(f"Created session overlay for {claude_dir}")

                        # Store host_cwd for symlink creation (from session state)
                        host_cwd = None
                        if hasattr(state, "host_cwd"):
                            host_cwd = state.host_cwd
                        elif isinstance(state, dict):
                            host_cwd = state.get("host_cwd")

                        if host_cwd:
                            sandbox._metadata["session_host_cwd"] = host_cwd
                        else:
                            # Preflight warning: session_id present but no host_cwd
                            # This means symlink creation will fail and session resumption
                            # may not work correctly due to CWD mismatch
                            session_id = None
                            if hasattr(state, "session_id"):
                                session_id = state.session_id
                            elif isinstance(state, dict):
                                session_id = state.get("session_id")

                            if session_id:
                                logger.warning(
                                    "Session has session_id but no host_cwd. "
                                    "Symlink creation will be skipped, and session resumption "
                                    "may fail due to CWD mismatch. This can happen if the session "
                                    "was created before host_cwd tracking was added. "
                                    "Consider creating a new session."
                                )

                    except subprocess.CalledProcessError as e:
                        logger.warning(f"Failed to create session overlay: {e}")
                        # Continue without session overlay - will create new session

            # Collect transfer bundles from bound contexts
            # This enables patches accumulated locally to become visible inside containers
            bundles: dict[str, TransferBundle] = {}
            for binding_name in config.context_states:
                # Try to get the context from scope to call transfer_bundle()
                try:
                    ctx = scope.get_context(binding_name)  # type: ignore[attr-defined]
                    if ctx and hasattr(ctx, "transfer_bundle"):
                        bundle = ctx.transfer_bundle(scope)
                        if bundle:
                            bundles[binding_name] = bundle
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"Could not collect bundle for {binding_name}: {e}")
            sandbox.bundles = bundles

            # Set cleanup callback so scope.discard() can clean up this sandbox
            # Uses a closure to capture the manager reference without storing it on sandbox
            # See: PLAN-workspace-patch-layering.md (scope discard cleanup)
            manager = self.manager  # Capture reference for closure
            sandbox._cleanup_fn = lambda: manager.cleanup(sandbox)

            # Register sandbox with scope for parent tracking (workspace layering)
            # This enables future tasks to find this sandbox via get_latest_sandbox_for_context()
            # See: PLAN-workspace-patch-layering.md (Change 8)
            scope.register_sandbox(sandbox)  # type: ignore[attr-defined]

            return sandbox

        except SandboxCreationError:
            # Re-raise our structured errors
            raise
        except Exception as e:
            # Wrap unexpected errors with context
            raise SandboxCreationError(
                f"Failed to create sandbox {sandbox_id}: {e}",
                phase="overlay_setup",
                original_error=e,
            ) from e

    async def execute(
        self,
        sandbox: ContainerSandbox,
        spec: ExecutionSpec,
    ) -> ExecutionResult:
        """Execute within sandbox.

        Execution flow:
        1. Write input.json with task specification
        2. Write rebind.env with path mappings
        3. Create and start container with task_runner
        4. Wait for completion
        5. Read output.json with result

        Args:
            sandbox: Handle from create_sandbox().
            spec: What to execute.

        Returns:
            ExecutionResult with outputs and metadata.

        Raises:
            SandboxExecutionError: If execution fails.
        """
        if not sandbox.task_dir:
            raise SandboxExecutionError("Sandbox has no task_dir - was it created properly?")

        # Pre-flight validation of execution spec (D44)
        from shepherd_runtime.device.container.preflight import preflight_check_spec

        preflight_result = preflight_check_spec(spec)
        preflight_result.log_warnings(logger)
        if not preflight_result.is_ok:
            raise SandboxExecutionError(
                f"Pre-flight validation failed: {'; '.join(preflight_result.errors)}",
                phase="spec_validation",
            )

        try:
            phase_timings: dict[str, float] = {}

            # 1. Write input.json
            t0 = time.perf_counter()
            input_path = sandbox.task_dir / "input.json"
            input_data = {
                "prompt": spec.prompt,
                "provider_config": dict(spec.provider_config),
                "context_states": self._serialize_context_states(sandbox),
                "tools": list(spec.tools) if spec.tools else None,
                "output_format": dict(spec.output_format) if spec.output_format else None,
            }
            # Pass task identity for effect attribution inside the container
            task_name = sandbox._metadata.get("task_name")
            if task_name:
                input_data["task_name"] = task_name
            if spec.task_spec is not None:
                ts = spec.task_spec
                input_data["task_spec"] = {
                    "task_source": ts.task_source,
                    "task_class_name": ts.task_class_name,
                    "task_imports": list(ts.task_imports),
                    "task_inputs": dict(ts.task_inputs),
                    "output_fields": list(ts.output_fields),
                    "context_fields": dict(ts.context_fields),
                    "is_async": ts.is_async,
                }
            input_path.write_text(json.dumps(input_data, indent=2, default=str))
            phase_timings["device.write_input"] = (time.perf_counter() - t0) * 1000

            # 1a. Apply patches from bundles before execution
            t0 = time.perf_counter()
            self._apply_bundles(sandbox)
            phase_timings["device.apply_bundles"] = (time.perf_counter() - t0) * 1000

            # 1b. Create session symlink for CWD mismatch (Change 6)
            # Must be done before container starts so symlink is visible via overlay
            session_host_cwd = sandbox._metadata.get("session_host_cwd")
            if session_host_cwd:
                container_cwd = self._get_container_cwd(sandbox)
                if container_cwd:
                    self._create_session_symlink(sandbox, session_host_cwd, container_cwd)

            # 2. Write rebind.env
            rebind_path = sandbox.task_dir / "rebind.env"
            rebind_env = self._generate_rebind_env(
                sandbox, provider_config=dict(spec.provider_config) if spec.provider_config else None
            )
            rebind_path.write_text("\n".join(f"{k}={v}" for k, v in rebind_env.items()))

            # 3. Create container
            # Install dependencies first (for dev mode with source mounts)
            # In production, use a pre-built image with deps installed
            t0 = time.perf_counter()
            self.manager.create_container(
                sandbox=sandbox,
                command=[
                    "sh",
                    "-c",
                    "pip install -q pydantic anthropic claude-agent-sdk openai opencode-ai 2>/dev/null && "
                    f"python -m {_task_runner_module_name()}",
                ],
                environment=rebind_env,
                working_dir="/task",
                use_fuse_workspace=self.use_fuse_workspace,
            )
            phase_timings["device.create_container"] = (time.perf_counter() - t0) * 1000

            # 4. Start and wait
            t0 = time.perf_counter()
            self.manager.start_container(sandbox)
            exit_code = self.manager.wait_container(sandbox)
            phase_timings["device.container_run"] = (time.perf_counter() - t0) * 1000

            # 5. Capture and save container logs
            container_logs = self.manager.get_container_logs(sandbox)
            self._save_debug_artifacts(sandbox, container_logs, exit_code)

            # 6. Read output.json
            t0 = time.perf_counter()
            output_path = sandbox.task_dir / "output.json"
            if not output_path.exists():
                raise self._create_debug_error(
                    "Container exited without producing output.json",
                    sandbox=sandbox,
                    exit_code=exit_code,
                    logs=container_logs,
                    phase="output_reading",
                )

            output_data = json.loads(output_path.read_text())
            phase_timings["device.read_output"] = (time.perf_counter() - t0) * 1000

            if not output_data.get("success"):
                error = output_data.get("error", "Unknown error")
                raise self._create_debug_error(
                    f"Task execution failed: {error}",
                    sandbox=sandbox,
                    exit_code=exit_code,
                    logs=container_logs,
                    phase="task_execution",
                )

            # Parse result
            result_dict = output_data.get("result", {})
            collected_effects = output_data.get("collected_effects")

            return ExecutionResult(
                success=result_dict.get("success", True),
                output_text=result_dict.get("output_text", ""),
                structured_output=result_dict.get("structured_output"),
                session_id=result_dict.get("session_id"),
                metadata={
                    **result_dict.get("metadata", {}),
                    "_collector": collected_effects,  # D28: Pass collector for extraction
                    "_exit_code": exit_code,
                    "_phase_timings": phase_timings,
                },
            )

        except SandboxExecutionError:
            raise
        except subprocess.CalledProcessError as e:
            raise SandboxExecutionError(
                f"Container operation failed: {e.cmd}",
                phase="container_start",
                stderr=e.stderr,
                container_id=sandbox.container_id if sandbox else None,
            ) from e
        except Exception as e:
            raise SandboxExecutionError(
                f"Execution failed: {e}",
                phase="unknown",
                container_id=sandbox.container_id if sandbox else None,
            ) from e

    async def extract_effects(
        self,
        sandbox: ContainerSandbox,
        execution_result: ExecutionResult,
    ) -> EffectBundle:
        """Extract effects from sandbox after execution.

        Supports two modes:

        **Fuse-overlayfs mode**:
        Per-tool file effects are already present in the EffectCollector with
        precise caused_by attribution from StackHooks. The kernel workspace
        upper remains empty, so workspace file effects come from the collector
        and the workspace patch is generated from the accumulated fuse layer.

        **Single-layer mode**:
        File effects are extracted from the kernel overlay upper layer via
        OverlayEffectExtractor. This preserves the pre-fuse behavior.

        In both modes, the EffectCollector provides lifecycle and intent
        effects such as tool calls and agent messages.

        Args:
            sandbox: Handle to sandbox after execution.
            execution_result: Result from execute().

        Returns:
            Bundle of effects organized by context.

        Raises:
            EffectExtractionError: If extraction fails.
        """
        try:
            # Restore collector from metadata (D28)
            collector_data = (execution_result.metadata or {}).get("_collector")
            if collector_data:
                collector = EffectCollector.deserialize_from_transport(collector_data)
            else:
                collector = EffectCollector()

            # Collect context effects by binding name
            context_effects: dict[str, list[Any]] = {}

            fuse_mode = self._is_fuse_overlay_mode(sandbox)
            if fuse_mode:
                logger.debug("Fuse-overlayfs mode detected; reconciling file effects from collector")

            file_effect_types = frozenset({"file_create", "file_patch", "file_delete"})
            all_collector_effects = list(collector.get_all_effects())
            if fuse_mode:
                collector_file_effects = [e for e in all_collector_effects if e.effect_type in file_effect_types]
                lifecycle_effects: list[Any] = [
                    e for e in all_collector_effects if e.effect_type not in file_effect_types
                ]
            else:
                collector_file_effects = []
                lifecycle_effects = list(all_collector_effects)

            # Extract from each overlay
            for binding_name, overlay in sandbox.overlays.items():
                # Set lower_path for diff generation (uses host path via VirtioFS)
                self.extractor.lower_path = overlay.original_host_path

                # Get manifest from bundle for effect attribution
                # This filters out files unchanged from the transfer bundle
                bundle = sandbox.bundles.get(binding_name)
                manifest = dict(bundle.manifest) if bundle else {}

                if fuse_mode and binding_name == "workspace":
                    if collector_file_effects:
                        context_effects[binding_name] = list(collector_file_effects)

                    patch_effect = self._extract_workspace_patch_from_accumulated(sandbox, collector)
                    if patch_effect:
                        context_effects.setdefault(binding_name, []).append(patch_effect)
                else:
                    effects = self.extractor.extract(overlay, collector, manifest=manifest)
                    if effects:
                        context_effects[binding_name] = effects

                    patch_effect = self.extractor.extract_workspace_patch(overlay, collector)
                    if patch_effect:
                        context_effects.setdefault(binding_name, []).append(patch_effect)

            # Emit ContainerExecutionCompleted for parent tracking (Change 7)
            # This enables get_latest_sandbox_for_context() to find parent sandboxes
            # for overlay layering in subsequent container tasks.
            # See: PLAN-workspace-patch-layering.md
            has_workspace_changes = False
            if fuse_mode:
                has_workspace_changes = True
            elif "workspace" in sandbox.overlays:
                workspace_overlay = sandbox.overlays["workspace"]
                if workspace_overlay.upper.exists():
                    # Check if upper layer has any files (indicating changes)
                    has_workspace_changes = any(workspace_overlay.upper.iterdir())

            completion_effect = ContainerExecutionCompleted(
                sandbox_id=sandbox.sandbox_id,
                context_name="workspace",
                task_name=sandbox.sandbox_id,  # Use sandbox_id as task identifier for debugging
                has_workspace_changes=has_workspace_changes,
            )
            lifecycle_effects.append(completion_effect)

            # Emit phase timing effects for device and container overhead
            metadata = execution_result.metadata or {}
            for phase_name, duration_ms in metadata.get("_phase_timings", {}).items():
                lifecycle_effects.append(LifecyclePhaseCompleted(phase=phase_name, duration_ms=duration_ms))
            for phase_name, duration_ms in metadata.get("_container_timings", {}).items():
                lifecycle_effects.append(LifecyclePhaseCompleted(phase=phase_name, duration_ms=duration_ms))

            return EffectBundle(
                context_effects=context_effects,
                lifecycle_effects=lifecycle_effects,  # type: ignore[arg-type]
                execution_metadata={
                    "sandbox_id": sandbox.sandbox_id,
                    "overlay_count": len(sandbox.overlays),
                    "exit_code": (execution_result.metadata or {}).get("_exit_code"),
                    "fuse_overlay_mode": fuse_mode,
                },
            )

        except Exception as e:
            raise EffectExtractionError(f"Effect extraction failed: {e}") from e

    def _is_fuse_overlay_mode(self, sandbox: ContainerSandbox) -> bool:
        """Return True when the fuse accumulated dir exists and has content."""
        if sandbox.task_dir is None:
            return False
        accumulated = sandbox.task_dir / "overlays" / "accumulated"
        if not accumulated.exists():
            return False
        try:
            return any(accumulated.iterdir())
        except OSError:
            return False

    def _extract_workspace_patch_from_accumulated(
        self,
        sandbox: ContainerSandbox,
        collector: EffectCollector,
    ) -> Any:
        """Generate a workspace patch from the accumulated fuse layer."""
        from shepherd_runtime.device.container.podman import OverlayMount

        if sandbox.task_dir is None:
            return None

        accumulated = sandbox.task_dir / "overlays" / "accumulated"
        if not accumulated.exists():
            return None
        try:
            if not any(accumulated.iterdir()):
                return None
        except OSError:
            return None

        workspace_overlay = sandbox.overlays.get("workspace")
        if workspace_overlay is None:
            return None

        synthetic = OverlayMount(
            task_id=sandbox.sandbox_id,
            context_name="workspace",
            lower=workspace_overlay.lower,
            upper=accumulated,
            work=accumulated.parent / "work",
            merged=accumulated,
            is_vm_path=False,
            original_host_path=workspace_overlay.original_host_path,
        )

        return self.extractor.extract_workspace_patch(synthetic, collector)

    async def cleanup(
        self,
        sandbox: ContainerSandbox,
        *,
        force: bool = False,
        preserve: bool | None = None,
        preserve_overlays: bool = False,
    ) -> None:
        """Release sandbox resources.

        Cleans up:
        - Container (removed if exists)
        - Overlay mounts (unmounted, optionally preserved)
        - Task directory (deleted, unless preserving overlays)

        In debug mode, artifacts are always preserved for inspection.
        Safe to call multiple times (idempotent).

        Args:
            sandbox: Handle to cleanup.
            force: If True, clean up even in debug mode.
            preserve: If True, preserve artifacts. Defaults to self.debug.
            preserve_overlays: If True, keep overlay directories for workspace
                layering. Container is stopped but overlays remain for subsequent
                tasks to use as lower layers. See PLAN-workspace-patch-layering.md.
        """
        should_preserve = preserve if preserve is not None else self.debug

        if should_preserve and not force:
            # Preserve artifacts for debugging
            had_error = getattr(sandbox, "_had_error", False)
            status = "with errors" if had_error else "successfully"
            print(  # noqa: T201 — intentional user-facing debug output
                f"\n{'=' * 60}\n"
                f"DEBUG MODE: Container completed {status}\n"
                f"{'=' * 60}\n"
                f"Artifacts preserved for inspection:\n"
                f"\n"
                f"  Task directory:\n"
                f"    {sandbox.task_dir}\n"
                f"\n"
                f"  Debug commands:\n"
                f"    cat {sandbox.task_dir}/input.json      # Task input\n"
                f"    cat {sandbox.task_dir}/output.json     # Task output\n"
                f"    cat {sandbox.task_dir}/container.log   # Container logs\n"
                f"    cat {sandbox.task_dir}/debug_info.json # Debug info\n",
                flush=True,
            )
            if sandbox.container_id:
                print(  # noqa: T201 — intentional user-facing debug output
                    f"\n"
                    f"  Container (still running):\n"
                    f"    podman logs {sandbox.container_id}\n"
                    f"    podman exec -it {sandbox.container_id} bash\n"
                    f"    podman inspect {sandbox.container_id}\n"
                    f"{'=' * 60}\n",
                    flush=True,
                )
            return

        try:
            self.manager.cleanup(sandbox, preserve_overlays=preserve_overlays)
        except Exception as e:  # noqa: BLE001
            # Cleanup should not raise - log and continue
            logger.debug(f"Cleanup warning: {e}")

    # =========================================================================
    # Bundle Application
    # =========================================================================

    def _apply_bundles(self, sandbox: ContainerSandbox) -> None:
        """Apply transfer bundles to sandbox filesystem before execution.

        Writes patch files from bundles to the task directory, making them
        accessible via mount. This enables patches accumulated locally to
        become visible inside the container.

        Args:
            sandbox: The sandbox with bundles to apply.
        """
        if not sandbox.task_dir:
            return

        for binding_name, bundle in sandbox.bundles.items():
            if not bundle.files:
                continue

            # Write patch files to task_dir (accessible via mount)
            for path, content in bundle.files.items():
                if isinstance(content, bytes):
                    target = sandbox.task_dir / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)

            logger.debug(f"Applied bundle for {binding_name}: {len(bundle.files)} files")

    # =========================================================================
    # Debug Helper Methods
    # =========================================================================

    def _save_debug_artifacts(
        self,
        sandbox: ContainerSandbox,
        container_logs: str | None,
        exit_code: int,
    ) -> None:
        """Save debug artifacts to task directory.

        Saves:
        - container.log: Full container stdout/stderr
        - debug_info.json: Structured debug information

        Args:
            sandbox: The sandbox being executed.
            container_logs: Container stdout/stderr.
            exit_code: Container exit code.
        """
        if not sandbox.task_dir:
            return

        # Save container logs
        if container_logs:
            log_path = sandbox.task_dir / "container.log"
            try:
                log_path.write_text(container_logs)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Could not save container logs: {e}")

        # Save debug info
        debug_info = {
            "sandbox_id": sandbox.sandbox_id,
            "container_id": sandbox.container_id,
            "exit_code": exit_code,
            "task_dir": str(sandbox.task_dir),
            "overlays": {
                name: {
                    "lower": str(overlay.lower),
                    "upper": str(overlay.upper),
                    "merged": str(overlay.merged),
                }
                for name, overlay in sandbox.overlays.items()
            },
            "debug_commands": {
                "logs": f"podman logs {sandbox.container_id}" if sandbox.container_id else None,
                "shell": f"podman exec -it {sandbox.container_id} bash" if sandbox.container_id else None,
                "inspect": f"podman inspect {sandbox.container_id}" if sandbox.container_id else None,
                "input": f"cat {sandbox.task_dir / 'input.json'}" if sandbox.task_dir else None,
                "output": f"cat {sandbox.task_dir / 'output.json'}" if sandbox.task_dir else None,
            },
        }

        debug_path = sandbox.task_dir / "debug_info.json"
        try:
            debug_path.write_text(json.dumps(debug_info, indent=2))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not save debug info: {e}")

        # Log based on debug mode and exit code
        if exit_code != 0:
            sandbox._had_error = True  # type: ignore[attr-defined]  # Mark for cleanup preservation
            logger.warning(
                f"Container exited with code {exit_code}\n"
                f"  Task dir: {sandbox.task_dir}\n"
                f"  Logs: {sandbox.task_dir / 'container.log'}"
            )
        elif self.debug:
            logger.info(f"Container logs saved to {sandbox.task_dir / 'container.log'}")

    def _create_debug_error(
        self,
        message: str,
        *,
        sandbox: ContainerSandbox,
        exit_code: int,
        logs: str | None,
        phase: str,
    ) -> SandboxExecutionError:
        """Create a SandboxExecutionError with helpful debug information.

        Args:
            message: Error message.
            sandbox: The sandbox that failed.
            exit_code: Container exit code.
            logs: Container logs.
            phase: Execution phase where error occurred.

        Returns:
            SandboxExecutionError with debug context.
        """
        sandbox._had_error = True  # type: ignore[attr-defined]  # Mark for cleanup preservation

        # Build helpful debug section
        debug_lines = [
            f"\n{'=' * 60}",
            "CONTAINER EXECUTION FAILED",
            f"{'=' * 60}",
            f"  Container ID: {sandbox.container_id or 'N/A'}",
            f"  Exit code:    {exit_code}",
            f"  Phase:        {phase}",
            f"  Task dir:     {sandbox.task_dir}",
        ]

        if sandbox.task_dir:
            debug_lines.extend(
                [
                    "",
                    "Debug artifacts:",
                    f"  cat {sandbox.task_dir / 'input.json'}",
                    f"  cat {sandbox.task_dir / 'output.json'}",
                    f"  cat {sandbox.task_dir / 'container.log'}",
                ]
            )

        if sandbox.container_id:
            debug_lines.extend(
                [
                    "",
                    "Debug commands:",
                    f"  podman logs {sandbox.container_id}",
                    f"  podman inspect {sandbox.container_id}",
                ]
            )

        if logs:
            # Include last 20 lines of logs
            log_lines = logs.strip().split("\n")
            last_lines = log_lines[-20:] if len(log_lines) > 20 else log_lines
            debug_lines.extend(
                [
                    "",
                    f"Last {len(last_lines)} lines of container logs:",
                    "-" * 40,
                    *last_lines,
                    "-" * 40,
                ]
            )

        debug_lines.append(f"{'=' * 60}")

        full_message = message + "\n".join(debug_lines)

        return SandboxExecutionError(
            full_message,
            phase=phase,
            exit_code=exit_code,
            stderr=logs,
            container_id=sandbox.container_id,
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_base_path(self, state: ContextState) -> Path | None:
        """Extract base path from context state.

        Args:
            state: Context state to examine.

        Returns:
            Path if state has a path field, None otherwise.
        """
        # Try common path field names
        if hasattr(state, "path"):
            return Path(state.path)
        if hasattr(state, "base_path"):
            return Path(state.base_path)
        if hasattr(state, "workspace_path"):
            return Path(state.workspace_path)
        return None

    # =========================================================================
    # Session Overlay Support (PLAN-session-resumption-containers.md)
    # =========================================================================

    def _compute_project_folder(self, cwd: str | Path) -> str:
        """Compute Claude Code project folder name from cwd.

        Claude Code replaces both '/' and '_' with '-' in project folder names.
        Example: /Users/alice/my_project -> -Users-alice-my-project

        Args:
            cwd: Working directory path.

        Returns:
            Project folder name as used by Claude Code CLI.
        """
        cwd_str = str(Path(cwd).resolve())
        return cwd_str.replace("/", "-").replace("_", "-")

    def _create_session_symlink(
        self,
        sandbox: ContainerSandbox,
        host_cwd: str | Path,
        container_cwd: str | Path,
    ) -> None:
        """Create symlink so SDK can find transcripts despite cwd mismatch.

        The SDK computes transcript paths from cwd. When container cwd differs
        from host cwd, we create a symlink:

            ~/.claude/projects/<container-project> -> <host-project>

        This is created in the overlay's upper layer so it doesn't affect the host.

        Args:
            sandbox: Container sandbox with session overlay.
            host_cwd: Working directory when session was created (on host).
            container_cwd: Working directory inside container.
        """
        if "session" not in sandbox.overlays:
            logger.debug("No session overlay - skipping symlink creation")
            return

        host_folder = self._compute_project_folder(host_cwd)
        container_folder = self._compute_project_folder(container_cwd)

        if host_folder == container_folder:
            logger.debug(f"No CWD mismatch ({host_folder}) - no symlink needed")
            return

        overlay = sandbox.overlays["session"]
        projects_dir = Path(overlay.upper) / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        symlink_path = projects_dir / container_folder

        # Use relative symlink: both are siblings in the projects/ directory
        target_path = Path(host_folder)  # Sibling in same directory

        if not symlink_path.exists():
            try:
                symlink_path.symlink_to(target_path)
                logger.debug(f"Created session symlink: {container_folder} -> {host_folder}")
            except OSError as e:
                logger.warning(f"Failed to create session symlink: {e}")

    def _get_container_cwd(self, sandbox: ContainerSandbox) -> str | None:
        """Get the container working directory from context states.

        Args:
            sandbox: Sandbox with stored context states.

        Returns:
            Container cwd (rebinded path) or None.
        """
        for binding_name, state in sandbox.context_states.items():
            if hasattr(state, "path"):
                # This will be rebinded to /container/<binding_name>
                return f"/container/{binding_name}"
            if isinstance(state, dict) and "path" in state:
                return f"/container/{binding_name}"
        return None

    def _merge_session_overlay(
        self,
        sandbox: ContainerSandbox,
        target_dir: Path | None = None,
    ) -> list[Path]:
        """Merge session overlay to host, excluding infrastructure symlinks.

        Only copies actual transcript files (.jsonl), not symlinks we created
        for path translation. This is called when effects should be committed
        to the parent scope (not discarded).

        Args:
            sandbox: Sandbox with session overlay.
            target_dir: Host ~/.claude directory. Defaults to Path.home() / ".claude".

        Returns:
            List of files that were merged.
        """
        import shutil

        if "session" not in sandbox.overlays:
            return []

        if target_dir is None:
            target_dir = Path.home() / ".claude"

        overlay = sandbox.overlays["session"]
        upper_path = Path(overlay.upper)

        if not upper_path.exists():
            return []

        merged_files: list[Path] = []

        for item in upper_path.rglob("*"):
            # Skip symlinks (these are our path translation infrastructure)
            if item.is_symlink():
                logger.debug(f"Skipping symlink during merge: {item}")
                continue

            # Skip directories (we'll create them as needed)
            if item.is_dir():
                continue

            # Compute relative path and target
            rel_path = item.relative_to(upper_path)
            target_path = target_dir / rel_path

            # Only merge transcript files (.jsonl) and known safe extensions
            if item.suffix not in {".jsonl", ".json"}:
                logger.debug(f"Skipping non-transcript file during merge: {item}")
                continue

            # Ensure parent directory exists
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy file to host
            shutil.copy2(item, target_path)
            merged_files.append(target_path)
            logger.debug(f"Merged session file: {rel_path}")

        if merged_files:
            logger.info(f"Merged {len(merged_files)} session files to host")

        return merged_files

    def merge_session_to_host(self, sandbox: ContainerSandbox) -> list[Path]:
        """Public API to merge session overlay to host.

        Call this when committing effects to parent scope (scope.merge()).
        This merges new transcript files while filtering out symlinks.

        Args:
            sandbox: Sandbox with session overlay.

        Returns:
            List of files that were merged.
        """
        return self._merge_session_overlay(sandbox)

    def _serialize_context_states(
        self,
        sandbox: ContainerSandbox,
    ) -> dict[str, dict[str, Any]]:
        """Serialize context states for task input.

        Uses to_dict() on states that support it (ContextStateBase subclasses),
        otherwise falls back to basic dict conversion.

        Args:
            sandbox: Sandbox with stored context states.

        Returns:
            Serialized context states by binding name.
        """
        result: dict[str, dict[str, Any]] = {}

        for binding_name, state in sandbox.context_states.items():
            if hasattr(state, "to_dict"):
                # State implements to_dict() (e.g., WorkspaceState, SessionStateData)
                result[binding_name] = state.to_dict()
            elif hasattr(state, "model_dump"):
                # Pydantic model
                result[binding_name] = state.model_dump()
            elif hasattr(state, "__dict__"):
                # Dataclass or regular object
                data = {k: v for k, v in state.__dict__.items() if not k.startswith("_")}
                # Ensure context_type is present
                if "context_type" not in data and hasattr(state, "context_type"):
                    data["context_type"] = state.context_type
                result[binding_name] = data
            else:
                # Fallback: minimal info
                result[binding_name] = {"context_type": getattr(state, "context_type", binding_name)}

        return result

    def _generate_rebind_env(
        self,
        sandbox: ContainerSandbox,
        provider_config: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Generate rebind environment for container.

        Maps host paths to container paths and forwards API keys.

        Args:
            sandbox: Sandbox with overlay information.
            provider_config: Provider config dict (from ExecutionSpec) for
                reading container_env declarations.

        Returns:
            Environment variable mappings.
        """
        env: dict[str, str] = {}

        for binding_name in sandbox.overlays:
            # Standard environment variable naming
            key = f"{binding_name.upper()}_PATH"
            if self.use_fuse_workspace and binding_name == "workspace":
                container_path = "/workspace"
            else:
                container_path = f"/container/{binding_name}"
            env[key] = container_path

        # Session overlay gets special handling - mounted at /root/.claude not /container/session
        # (Change 7 from PLAN-session-resumption-containers.md)
        if "session" in sandbox.overlays:
            env["SESSION_PATH"] = "/root/.claude"

        # Pass through ANTHROPIC_API_KEY if set
        import os

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key

        # Forward provider-declared env vars (e.g., for OpenCode's multi-provider models)
        if provider_config:
            for key in provider_config.get("container_env", ()):
                value = os.environ.get(key)
                if value:
                    env[key] = value

        if self.use_fuse_workspace and "workspace" in sandbox.overlays:
            workspace_overlay = sandbox.overlays["workspace"]
            parent_count = len(workspace_overlay.lower_layers) - 1
            layer_names = [f"parent_{i}" for i in range(parent_count)] + ["base"]
            env["SHEPHERD_LAYERS"] = ":".join(layer_names)

        return env


__all__ = [
    "_PROD_OVERLAY_TMPFS",
    "ContainerDevice",
    "ContainerSandbox",
    "_default_overlays_root",
    "_task_runner_module_name",
]
