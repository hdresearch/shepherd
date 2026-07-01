"""Standalone-core fallback for VM path translation and command execution.

This module provides utilities for working with the Podman Machine VM on macOS:

1. VMPathTranslator: Translates paths between macOS host and VM
   - Host paths: /Users/dcx/project, /tmp/foo
   - VM paths: /Users/dcx/project (direct), /private/tmp/foo (via symlink)

2. VMCommandRunner: Executes commands inside the VM via SSH
   - Single and batch command execution
   - Convenience methods for common operations

VirtioFS mount points are discovered at runtime via `podman machine ssh`.

See Also:
    design/containerized-execution/PLAN-macos-overlay-implementation.md
    design/containerized-execution/spikes/SPIKE-macos-overlay-architecture.md
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# =============================================================================
# VMCommandRunner
# =============================================================================


@dataclass
class VMCommandRunner:
    """Execute commands inside the Podman Machine VM.

    Provides helpers for running shell commands via SSH with proper error
    handling and output capture.

    Attributes:
        timeout: Default timeout for commands in seconds.

    Performance Notes:
        Each command invocation opens a new SSH connection (~50-100ms overhead).
        For high-frequency operations, consider:
        - Using run_batch() to combine multiple commands
        - Future: SSH ControlMaster for connection reuse
          (ssh -o ControlMaster=auto -o ControlPath=/tmp/ssh-%r@%h:%p -o ControlPersist=60)

        Current overhead is acceptable since LLM API calls dominate (5-30s).
    """

    timeout: float = 30.0

    def run(
        self,
        command: str,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a shell command inside the VM.

        Args:
            command: Shell command to execute.
            check: Raise on non-zero exit code.
            timeout: Command timeout (uses default if None).

        Returns:
            CompletedProcess with stdout/stderr.

        Raises:
            subprocess.CalledProcessError: If check=True and command fails.
            subprocess.TimeoutExpired: If command exceeds timeout.
        """
        actual_timeout = timeout if timeout is not None else self.timeout

        logger.debug(f"VM command: {command}")

        result = subprocess.run(
            ["podman", "machine", "ssh", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=actual_timeout,
        )

        if result.stdout:
            logger.debug(f"VM stdout: {result.stdout[:500]}")
        if result.stderr:
            logger.debug(f"VM stderr: {result.stderr[:500]}")

        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                f"podman machine ssh: {command}",
                result.stdout,
                result.stderr,
            )

        return result

    def run_batch(
        self,
        commands: list[str],
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run multiple commands in a single SSH session.

        Commands are joined with ' && ' for sequential execution.
        If any command fails, subsequent commands are not executed.

        Args:
            commands: List of shell commands.
            check: Raise on non-zero exit code.
            timeout: Command timeout (uses default if None).

        Returns:
            CompletedProcess with combined output.
        """
        combined = " && ".join(commands)
        return self.run(combined, check=check, timeout=timeout)

    def mkdir_p(self, *paths: Path) -> None:
        """Create directories inside VM (mkdir -p).

        Args:
            paths: VM paths to create.
        """
        if not paths:
            return
        path_str = " ".join(f'"{p}"' for p in paths)
        self.run(f"mkdir -p {path_str}")

    def rm_rf(self, path: Path) -> None:
        """Remove directory tree inside VM (rm -rf).

        Args:
            path: VM path to remove.
        """
        self.run(f'rm -rf "{path}"')

    def exists(self, path: Path) -> bool:
        """Check if a path exists inside the VM.

        Args:
            path: VM path to check.

        Returns:
            True if path exists, False otherwise.
        """
        result = self.run(f'test -e "{path}"', check=False)
        return result.returncode == 0

    def is_mounted(self, path: Path) -> bool:
        """Check if a path is a mount point inside the VM.

        Args:
            path: VM path to check.

        Returns:
            True if path is a mount point, False otherwise.
        """
        # Use mountpoint command for robust checking (handles whitespace, etc.)
        result = self.run(f'mountpoint -q "{path}"', check=False)
        return result.returncode == 0


# =============================================================================
# VMPathTranslator
# =============================================================================


@dataclass
class VMPathTranslator:
    """Translates paths between macOS host and Podman Machine VM.

    On macOS, Podman Machine shares host directories via VirtioFS.
    This class discovers the mount points and provides bidirectional
    path translation.

    Attributes:
        virtio_mounts: Mapping of host prefixes to VM mount points.
        vm_overlays_root: Root directory for overlays inside VM.
    """

    virtio_mounts: dict[Path, Path] = field(default_factory=dict)
    vm_overlays_root: Path = field(default=Path("/var/shepherd/overlays"))

    _runner: VMCommandRunner = field(default_factory=VMCommandRunner, repr=False)

    @classmethod
    def discover(cls, verify: bool = True) -> VMPathTranslator:
        """Auto-discover VirtioFS mount points from running VM.

        Args:
            verify: If True, verify mount mapping with a test file.

        Returns:
            Configured VMPathTranslator instance.

        Raises:
            RuntimeError: If VM is not running or mounts cannot be discovered.
        """
        runner = VMCommandRunner(timeout=10.0)

        # Query mount table from VM
        try:
            result = runner.run("mount", timeout=10)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                "Timed out querying VM mounts. Is Podman Machine running?\nTry: podman machine start"
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to query VM mounts: {e.stderr}\nIs Podman Machine running? Try: podman machine start"
            ) from e

        mounts = cls._parse_mount_output(result.stdout)

        if not mounts:
            raise RuntimeError(
                "No VirtioFS mounts found in VM.\n"
                "Ensure Podman Machine is configured with volume sharing.\n"
                "Mount output:\n" + result.stdout[:1000]
            )

        translator = cls(virtio_mounts=mounts, _runner=runner)

        if verify:
            translator._verify_mounts()

        logger.info(f"Discovered VirtioFS mounts: {list(mounts.keys())}")
        return translator

    @staticmethod
    def _parse_mount_output(mount_output: str) -> dict[Path, Path]:
        """Parse mount command output to find VirtioFS mounts.

        Args:
            mount_output: Output from `mount` command in VM.

        Returns:
            Mapping of host prefixes to VM mount points.
        """
        mounts: dict[Path, Path] = {}

        for line in mount_output.splitlines():
            # VirtioFS mount lines look like:
            # "virtiofs on /Users type virtiofs (rw,relatime)"
            # or on some systems:
            # "host on /mnt/host/Users type virtiofs (rw,relatime)"
            if "virtiofs" not in line.lower():
                continue

            parts = line.split()
            if len(parts) < 3:
                continue

            # parts[2] is the mount point (after "on")
            mount_point = Path(parts[2])

            # Determine host prefix based on mount point
            # Case 1: VM mount matches host path (e.g., /Users → /Users)
            # Case 2: VM uses /mnt/host prefix (e.g., /Users → /mnt/host/Users)
            mount_str = str(mount_point)
            if mount_str.startswith("/mnt/host/"):
                # Prefixed mount: extract host path
                host_prefix = Path("/" + mount_str.removeprefix("/mnt/host/"))
                mounts[host_prefix] = mount_point
            else:
                # Direct mount: VM path matches host path
                mounts[mount_point] = mount_point

        # Handle macOS /tmp → /private/tmp symlink
        # /private is typically mounted, which includes /private/tmp
        if Path("/private") in mounts or Path("/private/tmp") in mounts:
            mounts[Path("/tmp")] = Path("/private/tmp")

        return mounts

    def _verify_mounts(self) -> None:
        """Verify VirtioFS mounts work as expected.

        Creates a temp file on host and verifies it's visible at the
        expected VM path.

        Raises:
            RuntimeError: If mount mapping is incorrect.
        """
        import time

        # Find a suitable directory for verification
        # Try multiple locations in case some aren't shared
        verify_locations = [
            Path.home() / ".shepherd-tmp",
            Path("/tmp"),
            Path.home(),
        ]

        test_path: Path | None = None
        vm_path: Path | None = None

        for base_dir in verify_locations:
            try:
                # Ensure base directory exists
                if base_dir.name == ".shepherd-tmp":
                    base_dir.mkdir(exist_ok=True)

                if not base_dir.exists():
                    continue

                # Check if this path is under a VirtioFS mount
                try:
                    self.host_to_vm(base_dir)
                except ValueError:
                    continue

                # Create temp file
                with tempfile.NamedTemporaryFile(
                    dir=base_dir,
                    delete=False,
                    suffix=".shepherd-verify",
                    mode="w",
                ) as f:
                    f.write("shepherd-mount-verification")
                    f.flush()
                    os.fsync(f.fileno())  # Force flush to disk
                    test_path = Path(f.name)
                    vm_path = self.host_to_vm(test_path)
                    break

            except (OSError, PermissionError):
                continue

        if test_path is None or vm_path is None:
            raise RuntimeError(
                "Could not create verification file in any VirtioFS-shared location.\n"
                f"Tried: {verify_locations}\n"
                f"Available mounts: {list(self.virtio_mounts.keys())}"
            )

        try:
            # Small delay to allow VirtioFS to propagate the file
            # This is a workaround for VirtioFS caching behavior
            time.sleep(0.1)

            # Verify file is visible in VM at expected path
            # Retry a few times in case of propagation delay
            for attempt in range(3):
                result = self._runner.run(f"cat '{vm_path}'", check=False, timeout=5)

                if result.returncode == 0 and "shepherd-mount-verification" in result.stdout:
                    logger.debug(f"Mount verification passed: {test_path} → {vm_path}")
                    return  # Success!

                if attempt < 2:
                    time.sleep(0.2)  # Brief wait before retry

            # All retries failed
            if result.returncode != 0:
                raise RuntimeError(
                    f"VirtioFS mount verification failed.\n"
                    f"Host path: {test_path}\n"
                    f"Expected VM path: {vm_path}\n"
                    f"Error: File not found in VM (after 3 attempts)"
                )
            raise RuntimeError(
                f"VirtioFS mount verification failed.\n"
                f"Host path: {test_path}\n"
                f"Expected VM path: {vm_path}\n"
                f"Error: Content mismatch\n"
                f"Got: {result.stdout[:100]}"
            )

        finally:
            # Cleanup
            test_path.unlink(missing_ok=True)
            # Clean up temp directory if we created it and it's empty
            if test_path.parent.name == ".shepherd-tmp":
                with contextlib.suppress(OSError):
                    test_path.parent.rmdir()

    def host_to_vm(self, host_path: Path) -> Path:
        """Translate absolute host path to VM-visible path.

        Args:
            host_path: Absolute path on macOS host.

        Returns:
            Corresponding path inside the VM.

        Raises:
            ValueError: If path is not under any VirtioFS mount.
        """
        # Resolve symlinks and normalize path
        try:
            resolved_path = host_path.resolve()
        except OSError:
            # Path doesn't exist yet, just normalize it
            resolved_path = Path(os.path.normpath(host_path))

        # Find the longest matching mount prefix
        best_match: Path | None = None
        best_vm_path: Path | None = None

        for host_prefix, vm_mount in self.virtio_mounts.items():
            try:
                relative = resolved_path.relative_to(host_prefix)
                if best_match is None or len(host_prefix.parts) > len(best_match.parts):
                    best_match = host_prefix
                    best_vm_path = vm_mount / relative
            except ValueError:
                continue

        if best_vm_path is None:
            raise ValueError(
                f"Path {host_path} is not under any VirtioFS mount.\n"
                f"Resolved to: {resolved_path}\n"
                f"Available mounts: {list(self.virtio_mounts.keys())}"
            )

        return best_vm_path

    def vm_to_host(self, vm_path: Path) -> Path | None:
        """Translate VM path back to host path (if on VirtioFS).

        Args:
            vm_path: Path inside the VM.

        Returns:
            Corresponding host path, or None if not on VirtioFS.
        """
        for host_prefix, vm_mount in self.virtio_mounts.items():
            try:
                relative = vm_path.relative_to(vm_mount)
                return host_prefix / relative
            except ValueError:
                continue
        return None

    def is_vm_native(self, vm_path: Path) -> bool:
        """Check if a VM path is on native ext4 (not VirtioFS).

        Args:
            vm_path: Path inside the VM.

        Returns:
            True if path is on VM-native filesystem.
        """
        return self.vm_to_host(vm_path) is None

    def get_overlay_path(self, task_id: str, context_name: str) -> Path:
        """Get the VM path for an overlay directory.

        Args:
            task_id: Task identifier.
            context_name: Context binding name.

        Returns:
            VM path to the overlay directory.
        """
        return self.vm_overlays_root / task_id / context_name


# =============================================================================
# Module-level helpers
# =============================================================================


def is_macos() -> bool:
    """Check if running on macOS."""
    return os.uname().sysname == "Darwin"


def is_vm_available() -> bool:
    """Check if Podman Machine VM is running and accessible.

    Returns:
        True if VM is available, False otherwise.
    """
    if not is_macos():
        return False

    try:
        result = subprocess.run(
            ["podman", "machine", "ssh", "echo ok"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


__all__ = [
    "VMCommandRunner",
    "VMPathTranslator",
    "is_macos",
    "is_vm_available",
]
