"""Standalone-core fallback for VM effect extraction utilities.

Provides helpers for reading OverlayFS upper layer contents from inside
the Podman Machine VM via SSH + tar.

This module enables effect extraction from VM-internal overlay directories
on macOS, where the upper layer is stored in the VM's native ext4 filesystem
at /var/shepherd/overlays/{task_id}/{context}/upper.

Architecture:
    macOS HOST                          Podman Machine VM
    ──────────────────────             ────────────────────────────────
    VMUpperLayerReader                  /var/shepherd/overlays/
         │                                  └── {task_id}/
         │  SSH + tar                           └── {context}/
         └──────────────────────────────────────────► upper/
                                                          ├── new_file.py
                                                          ├── modified.py
                                                          └── .wh.deleted.py

See Also:
    design/containerized-execution/PLAN-macos-overlay-implementation.md
    design/containerized-execution/spikes/SPIKE-macos-overlay-architecture.md
"""

from __future__ import annotations

import io
import logging
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shepherd_runtime.device.container.vm_paths import VMCommandRunner

logger = logging.getLogger(__name__)

# OverlayFS whiteout prefix
_WHITEOUT_PREFIX = ".wh."


@dataclass
class VMFileInfo:
    """Information about a file in the VM upper layer.

    Attributes:
        relative_path: Path relative to upper layer root.
        is_whiteout: True if this represents a deleted file.
        is_directory: True if this is a directory.
        content: File content bytes, None for directories and whiteouts.
    """

    relative_path: Path
    is_whiteout: bool
    is_directory: bool
    content: bytes | None


class VMUpperLayerReader:
    """Read OverlayFS upper layer contents from VM via SSH.

    Extracts the upper layer via tar over SSH in a single round-trip,
    avoiding multiple SSH calls for individual file reads.

    The reader handles:
    - Regular files (returns content)
    - Directories (identified but no content)
    - Whiteout files (.wh.* prefix indicating deletions)

    Usage:
        reader = VMUpperLayerReader(vm_runner)
        for file_info in reader.read_upper_layer(Path("/var/shepherd/overlays/task/ctx/upper")):
            if file_info.is_whiteout:
                # Handle deletion
            elif file_info.is_directory:
                # Handle directory creation
            else:
                # Handle file create/modify with file_info.content

    Performance:
        Single SSH + tar round-trip regardless of file count.
        Typical overhead: 50-200ms for small overlays.
    """

    def __init__(self, vm_runner: VMCommandRunner):
        """Initialize the reader.

        Args:
            vm_runner: VMCommandRunner for executing commands in VM.
        """
        self.vm_runner = vm_runner

    def read_upper_layer(self, vm_upper_path: Path) -> Iterator[VMFileInfo]:
        """Read all files from VM upper layer.

        Extracts the entire upper layer as a tar archive via SSH,
        then parses it locally to yield file information.

        Args:
            vm_upper_path: Path to upper layer inside VM
                           (e.g., /var/shepherd/overlays/task/ctx/upper).

        Yields:
            VMFileInfo for each file/directory in upper layer.
        """
        # Check if upper layer has content
        if not self._has_content(vm_upper_path):
            logger.debug(f"Upper layer empty or missing: {vm_upper_path}")
            return

        logger.debug(f"Extracting upper layer via tar: {vm_upper_path}")

        # Extract via tar (binary mode for accurate content)
        tar_data = self._extract_tar(vm_upper_path)

        if not tar_data:
            logger.debug("No tar data returned")
            return

        # Parse tar and yield file info
        yield from self._parse_tar(tar_data)

    def _has_content(self, vm_path: Path) -> bool:
        """Check if directory exists and has content in VM.

        Args:
            vm_path: Path to check inside VM.

        Returns:
            True if directory exists and contains files.
        """
        # Use ls -A to check for any files (including hidden)
        result = self.vm_runner.run(
            f'test -d "{vm_path}" && ls -A "{vm_path}" 2>/dev/null | head -1',
            check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())

    def _extract_tar(self, vm_upper_path: Path) -> bytes:
        """Extract upper layer as tar archive via SSH.

        Uses tar with special handling to preserve file metadata.

        Args:
            vm_upper_path: Path to upper layer inside VM.

        Returns:
            Raw tar archive bytes.

        Raises:
            RuntimeError: If tar extraction fails.
        """
        # Build tar command
        # -c: create archive
        # -f -: output to stdout
        # -C: change to directory first
        # .: archive current directory contents
        cmd = f'tar -cf - -C "{vm_upper_path}" .'

        # Run via podman machine ssh with binary output
        try:
            result = subprocess.run(
                ["podman", "machine", "ssh", cmd],
                check=False,
                capture_output=True,
                timeout=60,
            )

            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                # Empty directory returns error but that's OK
                if "tar: .: Cannot stat" in stderr or not stderr.strip():
                    return b""
                raise RuntimeError(f"Failed to extract upper layer: {stderr}")

            return result.stdout

        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Timeout extracting upper layer from VM: {vm_upper_path}") from e

    def _parse_tar(self, tar_data: bytes) -> Iterator[VMFileInfo]:
        """Parse tar archive and yield file information.

        Handles:
        - Regular files: yields with content
        - Directories: yields with is_directory=True
        - Whiteouts: detects both .wh.* prefix and char device (0,0)

        OverlayFS uses two whiteout formats:
        1. Files prefixed with ".wh." (older format)
        2. Character devices with major=0, minor=0 (newer format, D9)

        Args:
            tar_data: Raw tar archive bytes.

        Yields:
            VMFileInfo for each entry in archive.
        """
        try:
            with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:") as tar:
                for member in tar.getmembers():
                    # Normalize path (remove leading ./)
                    name = member.name
                    name = name.removeprefix("./")
                    if not name or name == ".":
                        continue

                    relative_path = Path(name)
                    filename = relative_path.name

                    # Check for whiteout (OverlayFS deletion marker)
                    # Method 1: .wh.* prefix files
                    is_prefix_whiteout = filename.startswith(_WHITEOUT_PREFIX)
                    # Method 2: Character device with major=0, minor=0 (D9)
                    is_chardev_whiteout = member.ischr() and member.devmajor == 0 and member.devminor == 0

                    if member.isdir():
                        # Directory entry
                        yield VMFileInfo(
                            relative_path=relative_path,
                            is_whiteout=is_prefix_whiteout,
                            is_directory=True,
                            content=None,
                        )
                    elif is_chardev_whiteout:
                        # Character device whiteout (0,0) - filename is the deleted file
                        yield VMFileInfo(
                            relative_path=relative_path,
                            is_whiteout=True,
                            is_directory=False,
                            content=None,
                        )
                    elif is_prefix_whiteout:
                        # .wh.* prefix whiteout - extract the actual deleted filename
                        deleted_name = filename[len(_WHITEOUT_PREFIX) :]
                        actual_path = relative_path.parent / deleted_name
                        yield VMFileInfo(
                            relative_path=actual_path,
                            is_whiteout=True,
                            is_directory=False,
                            content=None,
                        )
                    elif member.isfile():
                        # Regular file - extract content
                        content = None
                        f = tar.extractfile(member)
                        if f:
                            content = f.read()

                        yield VMFileInfo(
                            relative_path=relative_path,
                            is_whiteout=False,
                            is_directory=False,
                            content=content,
                        )
                    # Skip other types (symlinks, etc.) for now

        except tarfile.TarError as e:
            logger.warning(f"Failed to parse tar archive: {e}")
            return

    def read_file(self, vm_path: Path) -> str | None:
        """Read a single text file from VM.

        Useful for reading text files outside the upper layer
        (e.g., lower layer files for diff generation).

        Note: This method is for TEXT files only. For binary files,
        use the tar-based extraction in read_upper_layer().

        Args:
            vm_path: Full path to file inside VM.

        Returns:
            File contents as string, or None if file doesn't exist.
        """
        result = self.vm_runner.run(f'cat "{vm_path}" 2>/dev/null', check=False)
        if result.returncode != 0:
            return None
        return result.stdout


__all__ = ["VMFileInfo", "VMUpperLayerReader"]
