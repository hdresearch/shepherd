"""Standalone-core fallback for Podman-based container helpers.

This module provides Podman-based container orchestration with OverlayFS
for isolated filesystem changes. The overlay structure enables:
- Copy-on-write isolation of workspace changes
- Efficient extraction of ResultEffects via upper layer diff
- Stacking for hierarchical task execution

Architecture (macOS with VM-based OverlayFS):

    macOS HOST                          Podman Machine VM
    ──────────────────────────         ────────────────────────────────
    /Users/dcx/project/                 VirtioFS → /Users/dcx/project/
         │                                        (lower layer, read-only)
         │                                              │
         │                              /var/shepherd/overlays/{task_id}/
         │                                  ├── upper/  (changes here)
         │                                  ├── work/   (OverlayFS internal)
         │                                  └── merged/ (container sees this)
         │                                              │
         └──────────────────────────────────────────────┘
                                                Container mounts merged

Architecture (Linux native OverlayFS):

    Linux HOST
    ──────────────────────────
    /tmp/shepherd-overlays/{task_id}/
        ├── lower → /path/to/workspace
        ├── upper/  (changes here)
        ├── work/   (OverlayFS internal)
        └── merged/ (container sees this)

See Also:
    design/containerized-execution/PLAN-macos-overlay-implementation.md
    design/containerized-execution/spikes/SPIKE-macos-overlay-architecture.md
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from shepherd_runtime.device.container.vm_paths import VMCommandRunner
    from shepherd_runtime.device.transfer import TransferBundle

logger = logging.getLogger(__name__)

_DEV_PACKAGE_DIR_CANDIDATES: dict[str, tuple[str, ...]] = {
    "shepherd-core": ("shepherd-core", "core"),
    "shepherd-providers": ("shepherd-providers", "providers"),
    "shepherd-contexts": ("shepherd-contexts", "contexts"),
    "shepherd-runtime": ("shepherd-runtime", "runtime"),
    "shepherd": ("shepherd", "meta"),
}


def _discover_dev_package_sources() -> dict[str, Path]:
    """Resolve workspace package source directories for container mounts.

    Supports both the legacy on-disk layout:
    ``packages/shepherd-core/src``

    and the split project-scoped layout:
    ``shepherd/packages/core/src``.
    """
    import shepherd_runtime

    packages_root = Path(shepherd_runtime.__file__).resolve().parent.parent.parent.parent
    package_sources: dict[str, Path] = {}

    for mount_name, candidates in _DEV_PACKAGE_DIR_CANDIDATES.items():
        for candidate in candidates:
            package_src = packages_root / candidate / "src"
            if package_src.exists():
                package_sources[mount_name] = package_src
                break

    workspace_root = packages_root.parent.parent
    commons_vcs_src = workspace_root / "commons-vcs" / "src"
    if commons_vcs_src.exists():
        package_sources["commons-vcs"] = commons_vcs_src

    return package_sources


# =============================================================================
# OverlayMount
# =============================================================================


@dataclass
class OverlayMount:
    """Configuration for a single OverlayFS mount.

    Represents the four directories required for an OverlayFS mount:
    - lower: Read-only base layer(s) - can be single path or list for stacking
    - upper: Writable layer where changes appear
    - work: OverlayFS internal working directory
    - merged: The combined view presented to the container

    For multi-layer overlays (workspace patch layering), lower can be a list
    of paths. OverlayFS lowerdir format is: lowerdir=newest:older:oldest:base
    where LEFTMOST = HIGHEST PRIORITY (overrides others).

    Attributes:
        task_id: Identifier for the task owning this overlay.
        context_name: Binding name (e.g., "workspace") for this overlay.
        lower: Path(s) to read-only base layer(s). List for stacked overlays.
        upper: Path to writable upper layer.
        work: Path to OverlayFS work directory.
        merged: Path to merged view (mount point).
        is_vm_path: True if paths are inside VM (macOS), False for host paths.
        original_host_path: Original host path for lower layer (for effect extraction).

    See Also:
        PLAN-workspace-patch-layering.md - Design rationale for multi-layer support
    """

    task_id: str
    context_name: str
    lower: Path | list[Path]  # Single path or list for stacked overlays
    upper: Path
    work: Path
    merged: Path
    is_vm_path: bool = False
    original_host_path: Path | None = None

    @property
    def lower_layers(self) -> list[Path]:
        """Get lower layers as a list.

        Returns:
            List of lower layer paths, ordered newest-first (highest priority).
        """
        if isinstance(self.lower, list):
            return self.lower
        return [self.lower]

    @property
    def mount_options(self) -> str:
        """Generate OverlayFS mount options string.

        For multi-layer overlays, formats lowerdir as colon-separated list.
        CRITICAL: OverlayFS lowerdir order is LEFTMOST = HIGHEST PRIORITY.
        """
        lower_str = ":".join(str(p) for p in self.lower_layers)
        return f"lowerdir={lower_str},upperdir={self.upper},workdir={self.work}"

    def is_mounted(self, vm_runner: VMCommandRunner | None = None) -> bool:
        """Check if this overlay is currently mounted.

        Args:
            vm_runner: VMCommandRunner for checking VM mounts (required for VM paths).

        Returns:
            True if mounted, False otherwise.

        Note:
            For VM paths, vm_runner is required. If not provided, logs a warning
            and returns False (conservative assumption).
        """
        if self.is_vm_path:
            # VM path: check mount status inside VM via SSH
            if vm_runner is None:
                import logging

                logging.getLogger(__name__).warning(
                    f"Cannot check mount status for VM path {self.merged} without vm_runner"
                )
                return False
            return vm_runner.is_mounted(self.merged)
        # Local path: check host mount table
        if not self.merged.exists():
            return False
        try:
            result = subprocess.run(
                ["mount"],
                capture_output=True,
                text=True,
                check=True,
            )
            return str(self.merged) in result.stdout
        except subprocess.CalledProcessError:
            return False


# =============================================================================
# ContainerSandbox
# =============================================================================


@dataclass
class ContainerSandbox:
    """Handle to a container sandbox.

    Implements SandboxHandle protocol. Tracks the container and its
    associated overlay mounts.

    Attributes:
        sandbox_id: Unique identifier for this sandbox.
        device_name: Name of the device that created it.
        container_id: Podman container ID (once created).
        overlays: Overlay mounts by context binding name.
        task_dir: Root directory for this sandbox's overlay structure.
        context_states: Original context states for serialization.
        parent_sandbox: Parent sandbox for overlay stacking (workspace layering).
        _metadata: Additional sandbox metadata.
        _cleanup_fn: Callback to clean up resources, set by ContainerDevice.
            Called by cleanup() method when scope.discard() is invoked.

    See Also:
        PLAN-workspace-patch-layering.md - Design rationale for parent tracking
    """

    sandbox_id: str
    device_name: str = "container"
    container_id: str | None = None
    overlays: dict[str, OverlayMount] = field(default_factory=dict)
    task_dir: Path | None = None
    context_states: dict[str, Any] = field(default_factory=dict)
    parent_sandbox: ContainerSandbox | None = None
    bundles: dict[str, TransferBundle] = field(default_factory=dict)
    _metadata: dict[str, Any] = field(default_factory=dict)
    _cleanup_fn: Callable[[], None] | None = field(default=None, repr=False)

    @classmethod
    def create(cls, task_id: str | None = None) -> ContainerSandbox:
        """Create a new sandbox with unique ID.

        Args:
            task_id: Optional task ID to use as sandbox_id.

        Returns:
            New ContainerSandbox instance.
        """
        sandbox_id = task_id or str(uuid.uuid4())
        return cls(sandbox_id=sandbox_id)

    def get_workspace_layers(self) -> list[Path]:
        """Get all workspace upper layers from this sandbox and ancestors.

        Returns layers ordered NEWEST FIRST (for OverlayFS lowerdir).
        OverlayFS lowerdir uses leftmost = highest priority.

        This enables workspace patch layering: Task B can see Task A's
        changes by including Task A's upper layer in Task B's lower layers.

        When fuse-overlayfs was active inside the container, all workspace
        writes go to /task/overlays/accumulated/ (not the kernel overlay upper).
        This method detects and prefers the accumulated dir when available.

        Returns:
            List of upper layer paths, ordered newest-first.

        See Also:
            PLAN-workspace-patch-layering.md (Change 1)
        """
        layers: list[Path] = []

        # Start with this sandbox's effective workspace layer (newest/highest priority)
        if "workspace" in self.overlays:
            layers.append(self._get_effective_workspace_layer())

        # Walk up to ancestors (older = lower priority = later in list)
        sandbox = self.parent_sandbox
        while sandbox is not None:
            if "workspace" in sandbox.overlays:
                layers.append(sandbox._get_effective_workspace_layer())
            sandbox = sandbox.parent_sandbox

        # Result: [newest, older, oldest] - ready for lowerdir
        return layers

    def _get_effective_workspace_layer(self) -> Path:
        """Return the fuse accumulated layer when populated, else kernel upper."""
        accumulated = self._get_fuse_accumulated_path()
        if accumulated is not None and accumulated.exists():
            try:
                if any(accumulated.iterdir()):
                    return accumulated
            except OSError:
                pass
        return self.overlays["workspace"].upper

    def _get_fuse_accumulated_path(self) -> Path | None:
        """Return the host-visible fuse-overlayfs accumulated directory."""
        if self.task_dir is None:
            return None
        return self.task_dir / "overlays" / "accumulated"

    @property
    def is_running(self) -> bool:
        """Check if the container is currently running."""
        if not self.container_id:
            return False
        try:
            result = subprocess.run(
                ["podman", "inspect", "--format", "{{.State.Running}}", self.container_id],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip().lower() == "true"
        except subprocess.CalledProcessError:
            return False

    def cleanup(self) -> None:
        """Clean up sandbox resources (overlay, container, temp dirs).

        This method is called by scope.discard() to clean up container sandboxes
        when a scope is abandoned. The actual cleanup logic is delegated to the
        device's manager via the _cleanup_fn callback set during creation.

        If no cleanup function was registered, this is a no-op (logs a warning).

        See Also:
            PLAN-workspace-patch-layering.md - Scope discard cleanup
        """
        if self._cleanup_fn is not None:
            self._cleanup_fn()
        else:
            logger.warning(
                "Sandbox %s has no cleanup function registered - resources may be leaked",
                self.sandbox_id,
            )


# =============================================================================
# PodmanSandboxManager
# =============================================================================


class PodmanSandboxManager:
    """Manages Podman containers and OverlayFS mounts.

    Provides lifecycle management for containerized execution:
    - Create overlay directories for workspace isolation
    - Mount overlays using OverlayFS
    - Run containers with appropriate bind mounts
    - Cleanup overlays and containers

    On macOS, uses VM-based OverlayFS:
    - VirtioFS provides read-only access to host paths (lower layer)
    - Upper/work/merged directories are on VM-native ext4
    - True OverlayFS semantics with copy-on-write

    On Linux, uses native OverlayFS directly on host.

    Attributes:
        overlays_root: Root directory for overlay storage (host path on Linux).
        image: Default container image to use.
        _use_vm_overlay: Whether to use VM-based overlay (macOS with feature flag).
        _path_translator: VMPathTranslator for host↔VM path translation.
        _vm_runner: VMCommandRunner for executing commands in VM.
    """

    def __init__(
        self,
        overlays_root: Path = Path("/tmp/shepherd-overlays"),
        image: str = "shepherd-sandbox",
    ):
        """Initialize the sandbox manager.

        Args:
            overlays_root: Root directory for overlay structures (Linux only).
            image: Default container image for execution.

        Raises:
            RuntimeError: If VM is required but not available.
        """
        self.overlays_root = overlays_root
        self.image = image
        self._environment_validated = False

        # Determine if we need VM-based overlay (macOS)
        is_macos = os.uname().sysname == "Darwin"

        if is_macos:
            # macOS: use VM-based overlay (VirtioFS lower + VM-native upper)
            from shepherd_runtime.device.container.vm_paths import (
                VMCommandRunner,
                VMPathTranslator,
            )

            self._use_vm_overlay = True
            self._vm_runner = VMCommandRunner(timeout=30.0)
            self._path_translator = VMPathTranslator.discover(verify=True)
            self._vm_overlays_root = self._path_translator.vm_overlays_root

            # Verify sudo is available for mounting
            self._verify_sudo_available()

            logger.info(f"VM overlay initialized. VM overlays root: {self._vm_overlays_root}")
        else:
            # Linux: use native OverlayFS on host
            self._use_vm_overlay = False
            self._path_translator = None  # type: ignore[assignment]
            self._vm_runner = None  # type: ignore[assignment]
            self._vm_overlays_root = None  # type: ignore[assignment]

        # Flag indicating whether we're on macOS (requires VM for containers)
        self._vm_required = is_macos

        # On Linux, overlay mounts use sudo, so podman must also run as root
        # to see those mounts (rootless podman uses a separate mount namespace).
        # On macOS, podman runs rootless and talks to the Podman Machine VM.
        self._podman_cmd: list[str] = ["podman"] if is_macos else ["sudo", "podman"]

    def _verify_sudo_available(self) -> None:
        """Verify passwordless sudo is available in VM.

        Required for mounting overlays. Raises if sudo not available.
        """
        if not self._vm_runner:
            return

        result = self._vm_runner.run("sudo -n true", check=False, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(
                "Passwordless sudo not available in Podman Machine VM.\n"
                "This is required for mounting OverlayFS.\n"
                "Check VM configuration or run: podman machine ssh 'sudo -n true'"
            )

    # =========================================================================
    # Pre-flight Validation
    # =========================================================================

    def is_podman_available(self) -> bool:
        """Check if Podman is running and accessible.

        Returns:
            True if Podman can be reached, False otherwise.
        """
        try:
            result = subprocess.run(
                [*self._podman_cmd, "version", "--format", "{{.Version}}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def is_image_available(self, image: str | None = None) -> bool:
        """Check if a container image is available locally.

        Args:
            image: Image name to check (defaults to self.image).

        Returns:
            True if image exists locally, False otherwise.
        """
        image = image or self.image
        try:
            result = subprocess.run(
                [*self._podman_cmd, "image", "exists", image],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def get_available_disk_mb(self, path: Path | None = None) -> int:
        """Get available disk space in megabytes.

        Args:
            path: Path to check (defaults to overlays_root).

        Returns:
            Available disk space in MB.
        """
        path = path or self.overlays_root
        # Ensure parent exists for statvfs
        check_path = path if path.exists() else path.parent
        while not check_path.exists() and check_path != check_path.parent:
            check_path = check_path.parent

        try:
            stat = os.statvfs(check_path)
            # Available blocks * block size / bytes per MB
            return (stat.f_bavail * stat.f_frsize) // (1024 * 1024)
        except OSError:
            return 0

    def validate_environment(
        self,
        min_disk_mb: int = 100,
        image: str | None = None,
    ) -> None:
        """Validate the execution environment before sandbox creation.

        Performs pre-flight checks to catch common issues early with
        clear, actionable error messages. After the first successful
        validation, subsequent calls are skipped (Podman availability
        and image presence are unlikely to change within a session).

        Args:
            min_disk_mb: Minimum required disk space in MB.
            image: Image to validate (defaults to self.image).

        Raises:
            SandboxCreationError: If validation fails.
        """
        if self._environment_validated:
            return

        from shepherd_core.foundation.protocols.device import SandboxCreationError

        # 1. Check Podman is running
        if not self.is_podman_available():
            raise SandboxCreationError.podman_unavailable()

        # 2. Check image exists
        image = image or self.image
        if not self.is_image_available(image):
            raise SandboxCreationError.image_not_found(image)

        # 3. Check disk space
        available_mb = self.get_available_disk_mb()
        if available_mb < min_disk_mb:
            raise SandboxCreationError.insufficient_disk(
                needed_mb=min_disk_mb,
                available_mb=available_mb,
                location=str(self.overlays_root),
            )

        # 4. Check VM disk space (macOS)
        if self._use_vm_overlay and self._vm_runner:
            self._check_vm_disk_space(min_mb=500)

        self._environment_validated = True

    def _check_vm_disk_space(self, min_mb: int = 500) -> None:
        """Check available disk space in VM.

        Args:
            min_mb: Minimum required space in MB.

        Raises:
            SandboxCreationError: If insufficient space.
        """
        from shepherd_core.foundation.protocols.device import SandboxCreationError

        if not self._vm_runner:
            return

        result = self._vm_runner.run(
            "df -m /var | tail -1 | awk '{print $4}'",
            check=False,
        )
        try:
            available = int(result.stdout.strip())
            if available < min_mb:
                raise SandboxCreationError.insufficient_disk(
                    needed_mb=min_mb,
                    available_mb=available,
                    location="/var (VM)",
                )
        except (ValueError, AttributeError):
            logger.warning("Could not check VM disk space")

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _ensure_overlays_root(self) -> None:
        """Ensure the overlays root directory exists."""
        if self._use_vm_overlay and self._vm_runner and self._vm_overlays_root:
            # Create in VM
            self._vm_runner.mkdir_p(self._vm_overlays_root)
        else:
            # Create on host
            self.overlays_root.mkdir(parents=True, exist_ok=True)

    def _run_in_vm(self, cmd: str) -> subprocess.CompletedProcess[str]:
        """Run a command in the Podman VM (macOS) or directly (Linux).

        Args:
            cmd: Command to execute.

        Returns:
            CompletedProcess with command results.
        """
        if self._vm_runner:
            return self._vm_runner.run(cmd)
        # On Linux, run directly
        return subprocess.run(
            ["sh", "-c", cmd],
            capture_output=True,
            text=True,
            check=True,
        )

    # =========================================================================
    # Overlay Management
    # =========================================================================

    def create_overlay(
        self,
        task_id: str,
        context_name: str,
        base_path: Path,
        parent_task_id: str | None = None,
        parent_layers: list[Path] | None = None,
    ) -> OverlayMount:
        """Create an OverlayFS overlay for a context.

        Creates the directory structure for an overlay mount:
        - lower: Points to base_path, plus any parent layers for stacking
        - upper: Empty directory for changes
        - work: OverlayFS internal
        - merged: Mount point

        For workspace patch layering, parent_layers contains upper layer paths
        from prior container tasks. These are prepended to lowerdir so Task B
        sees Task A's changes without materialization.

        On macOS with VM overlay, directories are created inside the VM
        on native ext4, while lower layer uses VirtioFS to access host.

        Args:
            task_id: Task identifier for this overlay.
            context_name: Binding name for the context.
            base_path: Path to the base filesystem to overlay.
            parent_task_id: DEPRECATED - use parent_layers instead.
            parent_layers: List of parent upper layer paths, ordered newest-first.
                           These become additional lower layers for stacking.

        Returns:
            OverlayMount with configured paths (lower may be a list for stacking).

        See Also:
            PLAN-workspace-patch-layering.md (Change 3)
        """
        if self._use_vm_overlay:
            return self._create_overlay_in_vm(task_id, context_name, base_path, parent_task_id, parent_layers)
        return self._create_overlay_local(task_id, context_name, base_path, parent_task_id, parent_layers)

    def _create_overlay_in_vm(
        self,
        task_id: str,
        context_name: str,
        base_path: Path,
        parent_task_id: str | None,
        parent_layers: list[Path] | None = None,
    ) -> OverlayMount:
        """Create overlay directories inside VM (macOS).

        Args:
            task_id: Task identifier.
            context_name: Context binding name.
            base_path: Host path to workspace (will be translated to VM path).
            parent_task_id: DEPRECATED - use parent_layers instead.
            parent_layers: List of parent upper layer paths for stacking.

        Returns:
            OverlayMount with VM paths.
        """
        assert self._path_translator is not None
        assert self._vm_runner is not None
        assert self._vm_overlays_root is not None

        # Translate host base_path to VM path via VirtioFS
        vm_base_path = self._path_translator.host_to_vm(base_path)

        # Create overlay structure in VM-native filesystem
        task_dir = self._vm_overlays_root / task_id / context_name

        upper = task_dir / "upper"
        work = task_dir / "work"
        merged = task_dir / "merged"

        # Create directories via SSH (batch for efficiency)
        self._vm_runner.mkdir_p(upper, work, merged)

        # Build lower layers list
        # CRITICAL: OverlayFS lowerdir order is LEFTMOST = HIGHEST PRIORITY
        # So we need: [parent_layers...] + [base_path] where parent_layers are newest-first
        lower_layers: list[Path] = []

        if parent_layers:
            # Parent layers are already ordered newest-first
            lower_layers.extend(parent_layers)
        elif parent_task_id:
            # DEPRECATED: Legacy single-parent support
            lower_layers.append(self._vm_overlays_root / parent_task_id / context_name / "upper")

        # Base path is always lowest priority (rightmost)
        lower_layers.append(vm_base_path)

        # Use list for multi-layer, single Path for single-layer (back-compat)
        lower: Path | list[Path] = lower_layers if len(lower_layers) > 1 else lower_layers[0]

        logger.debug(
            f"Created VM overlay for {task_id}/{context_name}: "
            f"lower_count={len(lower_layers)}, upper={upper}, merged={merged}"
        )

        return OverlayMount(
            task_id=task_id,
            context_name=context_name,
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=True,
            original_host_path=base_path,
        )

    def _create_overlay_local(
        self,
        task_id: str,
        context_name: str,
        base_path: Path,
        parent_task_id: str | None,
        parent_layers: list[Path] | None = None,
    ) -> OverlayMount:
        """Create overlay directories on host (Linux or legacy macOS).

        Args:
            task_id: Task identifier.
            context_name: Context binding name.
            base_path: Path to the base filesystem to overlay.
            parent_task_id: DEPRECATED - use parent_layers instead.
            parent_layers: List of parent upper layer paths for stacking.

        Returns:
            OverlayMount with host paths.
        """
        self._ensure_overlays_root()

        # Create task directory structure
        task_dir = self.overlays_root / task_id / context_name
        task_dir.mkdir(parents=True, exist_ok=True)

        # Create overlay directories
        upper = task_dir / "upper"
        work = task_dir / "work"
        merged = task_dir / "merged"

        upper.mkdir(exist_ok=True)
        work.mkdir(exist_ok=True)
        merged.mkdir(exist_ok=True)

        # Build lower layers list
        # CRITICAL: OverlayFS lowerdir order is LEFTMOST = HIGHEST PRIORITY
        # So we need: [parent_layers...] + [base_path] where parent_layers are newest-first
        lower_layers: list[Path] = []

        if parent_layers:
            # Parent layers are already ordered newest-first
            lower_layers.extend(parent_layers)
        elif parent_task_id:
            # DEPRECATED: Legacy single-parent support
            lower_layers.append(self.overlays_root / parent_task_id / context_name / "upper")

        # Base path is always lowest priority (rightmost)
        lower_layers.append(base_path)

        # Use list for multi-layer, single Path for single-layer (back-compat)
        lower: Path | list[Path] = lower_layers if len(lower_layers) > 1 else lower_layers[0]

        return OverlayMount(
            task_id=task_id,
            context_name=context_name,
            lower=lower,
            upper=upper,
            work=work,
            merged=merged,
            is_vm_path=False,
            original_host_path=base_path,
        )

    def mount_overlay(self, overlay: OverlayMount) -> None:
        """Mount an OverlayFS overlay.

        On macOS, mounts inside VM via SSH.
        On Linux, mounts directly on host.

        Args:
            overlay: The overlay configuration to mount.

        Raises:
            subprocess.CalledProcessError: If mount fails.
        """
        if overlay.is_mounted(vm_runner=self._vm_runner):
            return  # Already mounted

        mount_cmd = f"mount -t overlay overlay -o {overlay.mount_options} {overlay.merged}"

        if overlay.is_vm_path:
            # macOS: Mount inside VM via SSH
            assert self._vm_runner is not None
            logger.debug(f"Mounting overlay in VM: {mount_cmd}")
            self._vm_runner.run(f"sudo {mount_cmd}")
        else:
            # Linux: mount directly on host
            logger.debug(f"Mounting overlay on host: {mount_cmd}")
            subprocess.run(
                ["sudo", "sh", "-c", mount_cmd],
                capture_output=True,
                text=True,
                check=True,
            )

    def unmount_overlay(self, overlay: OverlayMount) -> None:
        """Unmount an OverlayFS overlay.

        Args:
            overlay: The overlay configuration to unmount.
        """
        if not overlay.is_mounted(vm_runner=self._vm_runner):
            return  # Not mounted

        if overlay.is_vm_path:
            # macOS: Unmount inside VM
            assert self._vm_runner is not None
            logger.debug(f"Unmounting overlay in VM: {overlay.merged}")
            self._vm_runner.run(f"sudo umount {overlay.merged}", check=False)
        else:
            # Linux: unmount on host
            with contextlib.suppress(subprocess.CalledProcessError):
                subprocess.run(
                    ["sudo", "umount", str(overlay.merged)],
                    capture_output=True,
                    text=True,
                    check=True,
                )

    # =========================================================================
    # Container Management
    # =========================================================================

    def create_container(
        self,
        sandbox: ContainerSandbox,
        command: list[str] | None = None,
        environment: dict[str, str] | None = None,
        working_dir: str | None = None,
        use_fuse_workspace: bool = False,
    ) -> str:
        """Create a Podman container for the sandbox.

        Args:
            sandbox: The sandbox to create a container for.
            command: Command to run in container.
            environment: Environment variables.
            working_dir: Working directory in container.
            use_fuse_workspace: Whether to pass raw workspace layers under
                ``/layers`` for in-container fuse composition.

        Returns:
            Container ID.

        Raises:
            subprocess.CalledProcessError: If container creation fails.
        """
        cmd = [*self._podman_cmd, "create"]

        # Explicitly run as root user (HOME=/root)
        # Session overlay is mounted at /root/.claude which requires this
        # Making this explicit prevents issues if base image defaults change
        cmd.extend(["--user", "root"])

        # Disable SELinux labeling for container volumes
        # OverlayFS merged directories don't support xattr operations required by SELinux
        # Since we're already isolated in a Podman VM, SELinux labeling is unnecessary
        cmd.extend(["--security-opt", "label=disable"])

        if use_fuse_workspace:
            cmd.extend(["--device", "/dev/fuse", "--cap-add", "SYS_ADMIN"])

        # Add environment variables
        if environment:
            for key, value in environment.items():
                cmd.extend(["-e", f"{key}={value}"])

        # Add working directory
        if working_dir:
            cmd.extend(["-w", working_dir])

        # Add volume mounts for overlays
        for ctx_name, overlay in sandbox.overlays.items():
            # Session overlay is mounted at /root/.claude, not /container/session
            # (Change 5 from PLAN-session-resumption-containers.md)
            # IMPORTANT: Container runs as root by default, so HOME=/root
            if ctx_name == "session":
                container_path = "/root/.claude"
            elif ctx_name == "workspace":
                if use_fuse_workspace:
                    self._mount_workspace_layers(cmd, overlay)
                else:
                    container_path = "/container/workspace"
                    if overlay.is_vm_path:
                        cmd.extend(["-v", f"{overlay.merged}:{container_path}"])
                    else:
                        mount_path = overlay.merged if overlay.merged.exists() else overlay.upper
                        cmd.extend(["-v", f"{mount_path}:{container_path}"])
                continue
            else:
                container_path = f"/container/{ctx_name}"

            if overlay.is_vm_path:
                # VM path: Podman can mount directly
                # SELinux labeling disabled at container level (OverlayFS doesn't support xattr)
                cmd.extend(["-v", f"{overlay.merged}:{container_path}"])
            else:
                # Host path
                mount_path = overlay.merged if overlay.merged.exists() else overlay.upper
                cmd.extend(["-v", f"{mount_path}:{container_path}"])

        # Add task directory mount
        if sandbox.task_dir:
            # SELinux labeling disabled at container level
            cmd.extend(["-v", f"{sandbox.task_dir}:/task"])

        # Mount workspace package sources into the container when running from a
        # checkout, regardless of whether the checkout uses legacy or split
        # on-disk package directory names.
        package_sources = _discover_dev_package_sources()
        if package_sources:
            for pkg_name, pkg_src in package_sources.items():
                cmd.extend(["-v", f"{pkg_src}:/packages/{pkg_name}/src:ro"])
            pythonpath = ":".join(f"/packages/{pkg_name}/src" for pkg_name in package_sources)
            cmd.extend(["-e", f"PYTHONPATH={pythonpath}"])

        # Add image and command
        cmd.append(self.image)
        if command:
            cmd.extend(command)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        container_id = result.stdout.strip()
        sandbox.container_id = container_id
        return container_id

    def _mount_workspace_layers(self, cmd: list[str], overlay: OverlayMount) -> None:
        """Pass raw workspace layers to the container under /layers."""
        lower_layers = overlay.lower_layers
        base_path = lower_layers[-1]
        parent_layers = lower_layers[:-1]

        cmd.extend(["-v", f"{base_path}:/layers/base:ro"])
        for i, parent_path in enumerate(parent_layers):
            cmd.extend(["-v", f"{parent_path}:/layers/parent_{i}:ro"])

    def start_container(self, sandbox: ContainerSandbox) -> None:
        """Start a created container.

        Args:
            sandbox: The sandbox with container to start.

        Raises:
            ValueError: If no container_id set.
            subprocess.CalledProcessError: If start fails.
        """
        if not sandbox.container_id:
            raise ValueError("No container_id set - create container first")

        subprocess.run(
            [*self._podman_cmd, "start", sandbox.container_id],
            capture_output=True,
            text=True,
            check=True,
        )

    def wait_container(self, sandbox: ContainerSandbox, timeout: int = 300) -> int:
        """Wait for container to complete.

        Args:
            sandbox: The sandbox with running container.
            timeout: Maximum wait time in seconds.

        Returns:
            Container exit code.

        Raises:
            ValueError: If no container_id set.
            subprocess.TimeoutExpired: If timeout exceeded.
        """
        if not sandbox.container_id:
            raise ValueError("No container_id set - create container first")

        result = subprocess.run(
            [*self._podman_cmd, "wait", sandbox.container_id],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )

        return int(result.stdout.strip())

    def get_container_logs(self, sandbox: ContainerSandbox) -> str:
        """Get container stdout/stderr logs.

        Args:
            sandbox: The sandbox with container.

        Returns:
            Combined stdout and stderr logs.
        """
        if not sandbox.container_id:
            return ""

        result = subprocess.run(
            [*self._podman_cmd, "logs", sandbox.container_id],
            check=False,
            capture_output=True,
            text=True,
        )

        return result.stdout + result.stderr

    def remove_container(self, sandbox: ContainerSandbox, force: bool = True) -> None:
        """Remove a container.

        Args:
            sandbox: The sandbox with container to remove.
            force: Force removal even if running.
        """
        if not sandbox.container_id:
            return

        cmd = [*self._podman_cmd, "rm"]
        if force:
            cmd.append("-f")
        cmd.append(sandbox.container_id)

        with contextlib.suppress(subprocess.CalledProcessError):
            subprocess.run(cmd, capture_output=True, text=True, check=True)

        sandbox.container_id = None

    # =========================================================================
    # Cleanup
    # =========================================================================

    def cleanup(self, sandbox: ContainerSandbox, *, preserve_overlays: bool = False) -> None:
        """Clean up all resources for a sandbox.

        Unmounts overlays, removes container, and optionally deletes overlay directories.

        Args:
            sandbox: The sandbox to clean up.
            preserve_overlays: If True, keep overlay directories for workspace layering.
                The container is stopped and overlays are unmounted, but the upper
                layer directories remain for subsequent tasks to use as lower layers.
                See PLAN-workspace-patch-layering.md.
        """
        # Remove container
        self.remove_container(sandbox)

        if preserve_overlays:
            # Only unmount overlays, keep directories for workspace layering
            for overlay in sandbox.overlays.values():
                with contextlib.suppress(Exception):
                    self.unmount_overlay(overlay)
            # Don't clear sandbox state - overlays can still be referenced
            logger.debug(f"Preserved overlays for sandbox {sandbox.sandbox_id} (workspace layering enabled)")
        else:
            # Full cleanup: unmount and delete overlay directories
            for overlay in sandbox.overlays.values():
                self._cleanup_overlay(overlay)

            # Delete task directory (host-side)
            if sandbox.task_dir and sandbox.task_dir.exists():
                with contextlib.suppress(OSError):
                    shutil.rmtree(sandbox.task_dir)

            # Clear sandbox state
            sandbox.overlays.clear()
            sandbox.task_dir = None

    def _cleanup_overlay(self, overlay: OverlayMount) -> None:
        """Clean up a single overlay mount and directories.

        Args:
            overlay: The overlay to clean up.
        """
        # 1. Unmount if mounted (ignore errors)
        with contextlib.suppress(Exception):
            self.unmount_overlay(overlay)

        # 2. Remove overlay directories
        if overlay.is_vm_path:
            # Remove in VM
            if self._vm_runner and self._vm_overlays_root:
                task_dir = self._vm_overlays_root / overlay.task_id / overlay.context_name
                self._vm_runner.run(f'sudo rm -rf "{task_dir}"', check=False)
        else:
            # Remove on host
            task_dir = overlay.upper.parent
            if task_dir.exists():
                with contextlib.suppress(OSError):
                    shutil.rmtree(task_dir)

    def cleanup_orphan_overlays(self, max_age_hours: int = 24) -> int:
        """Remove orphan overlay directories older than max_age_hours.

        Called at startup or periodically to clean up after crashes.
        Only removes directories matching UUID pattern to prevent
        accidental deletion of unrelated files.

        Args:
            max_age_hours: Maximum age of overlays to keep. Use 0 to clean ALL
                orphan directories regardless of age.

        Returns:
            Number of orphan directories removed.
        """
        import re

        # Safety pattern: only delete directories that look like task IDs (UUIDs)
        uuid_pattern = "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-*"
        uuid_regex = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

        # Build age filter (empty string if max_age_hours=0 to match all)
        age_filter = f"-mmin +{max_age_hours * 60}" if max_age_hours > 0 else ""

        if self._use_vm_overlay and self._vm_runner and self._vm_overlays_root:
            # Clean up in VM
            find_cmd = (
                f"find {self._vm_overlays_root} -maxdepth 1 -type d -name '{uuid_pattern}' {age_filter}"
            ).strip()

            result = self._vm_runner.run(
                f"{find_cmd} 2>/dev/null | wc -l",
                check=False,
            )
            try:
                count = int(result.stdout.strip())
            except ValueError:
                count = 0

            if count > 0:
                cleanup_cmd = f"{find_cmd} -exec rm -rf {{}} \\; 2>/dev/null"
                self._vm_runner.run(
                    f"sudo sh -lc {shlex.quote(cleanup_cmd)}",
                    check=False,
                )

            return count
        # Local cleanup
        import time

        count = 0
        # When max_age_hours=0, use a cutoff in the future to match all files
        if max_age_hours > 0:
            cutoff = time.time() - (max_age_hours * 3600)
        else:
            cutoff = time.time() + 1  # Future time = all files are older

        if not self.overlays_root.exists():
            return 0

        for task_dir in self.overlays_root.iterdir():
            if task_dir.is_dir() and uuid_regex.match(task_dir.name) and task_dir.stat().st_mtime < cutoff:
                try:
                    shutil.rmtree(task_dir)
                    count += 1
                except OSError:
                    pass

        return count


__all__ = [
    "ContainerSandbox",
    "OverlayMount",
    "PodmanSandboxManager",
]
