"""Runtime-owned fuse-overlayfs layer manager for container task isolation."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WHITEOUT_PREFIX = ".wh."


def fuse_overlayfs_available() -> bool:
    """Check if fuse-overlayfs, fusermount3, and /dev/fuse are available."""
    if not Path("/dev/fuse").exists():
        return False
    for binary in ["fuse-overlayfs", "fusermount3"]:
        try:
            subprocess.run(
                [binary, "--version"],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    return True


class FuseOverlayManager:
    """In-process fuse-overlayfs manager for per-tool-call isolation."""

    FUSE_OVERLAYFS = "fuse-overlayfs"
    FUSERMOUNT = "fusermount3"
    WORKSPACE = Path("/workspace")
    WORKSPACE_RO = Path("/workspace-ro")
    OVERLAYS_ROOT = Path("/task/overlays")

    def __init__(self) -> None:
        self._accumulated = self.OVERLAYS_ROOT / "accumulated"
        self._work = self.OVERLAYS_ROOT / "work"
        self._lower_layers: list[Path] = [self.WORKSPACE_RO]
        self._tool_counter = 0
        self._tool_dir: Path | None = None
        self._mounted = False
        self._version = "unknown"
        self.merge_failed = False

    def setup(self, lower_layers: list[Path] | None = None) -> None:
        """Create directories and mount the initial fuse-overlayfs view."""
        if lower_layers is not None:
            for layer_path in lower_layers:
                if not layer_path.exists() or not layer_path.is_dir():
                    raise RuntimeError(
                        f"Layer {layer_path} not available. Check that the host bind-mounted all /layers/* paths."
                    )
            self._lower_layers = lower_layers
        else:
            if not self.WORKSPACE_RO.exists() or not self.WORKSPACE_RO.is_dir():
                raise RuntimeError(
                    f"{self.WORKSPACE_RO} not available. "
                    "Check that the host mounted the workspace overlay at /workspace-ro."
                )
            self._lower_layers = [self.WORKSPACE_RO]

        try:
            ver = subprocess.run(
                [self.FUSE_OVERLAYFS, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self._version = ver.stdout.strip() or ver.stderr.strip()
            logger.info("fuse-overlayfs version: %s", self._version)
        except (OSError, subprocess.SubprocessError):
            self._version = "unknown"

        self._accumulated.mkdir(parents=True, exist_ok=True)
        self._work.mkdir(parents=True, exist_ok=True)
        self._mount(
            lowers=self._lower_layers,
            upper=self._accumulated,
            work=self._work,
        )

    def teardown(self) -> None:
        """Unmount fuse-overlayfs."""
        with suppress(RuntimeError):
            self._unmount()

    def cleanup_partial(self) -> None:
        """Remove a partially created tool layer after setup failure."""
        if self._tool_dir and self._tool_dir.exists():
            shutil.rmtree(self._tool_dir, ignore_errors=True)
        self._tool_dir = None

    def push_layer(self, tool_use_id: str) -> None:
        """Create a fresh tool layer and remount with it on top."""
        self._unmount()

        self._tool_counter += 1
        self._tool_dir = self.OVERLAYS_ROOT / f"tool_{self._tool_counter}"
        tool_upper = self._tool_dir / "upper"
        tool_work = self._tool_dir / "work"
        tool_upper.mkdir(parents=True)
        tool_work.mkdir(parents=True)

        self._mount(
            lowers=[self._accumulated, *self._lower_layers],
            upper=tool_upper,
            work=tool_work,
        )

    def pop_and_merge(self, tool_use_id: str) -> list[dict[str, Any]]:
        """Extract tool effects, merge the tool layer, and restore base."""
        if self._tool_dir is None:
            raise RuntimeError("pop_and_merge called without push_layer")
        tool_upper = self._tool_dir / "upper"

        effects = self._extract_effects(tool_upper, tool_use_id)
        self._unmount()

        has_content = tool_upper.exists() and any(tool_upper.iterdir())
        if has_content:
            try:
                self._merge(tool_upper, self._accumulated)
            except (OSError, subprocess.CalledProcessError):
                logger.exception("Merge failed (accumulated may be incomplete)")
                self.merge_failed = True

        shutil.rmtree(self._tool_dir, ignore_errors=True)
        self._tool_dir = None

        shutil.rmtree(self._work, ignore_errors=True)
        self._work.mkdir(parents=True, exist_ok=True)

        self._mount(
            lowers=self._lower_layers,
            upper=self._accumulated,
            work=self._work,
        )

        return effects

    def _mount(self, lowers: list[Path], upper: Path, work: Path) -> None:
        """Mount fuse-overlayfs at /workspace."""
        self.WORKSPACE.mkdir(parents=True, exist_ok=True)
        lower_str = ":".join(str(p) for p in lowers)
        result = subprocess.run(
            [
                self.FUSE_OVERLAYFS,
                "-o",
                f"lowerdir={lower_str},upperdir={upper},workdir={work}",
                str(self.WORKSPACE),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"fuse-overlayfs mount failed (rc={result.returncode}): {result.stderr.strip()}")
        self._mounted = True

    def _unmount(self) -> None:
        """Unmount fuse-overlayfs from /workspace."""
        if not self._mounted:
            return
        result = subprocess.run(
            [self.FUSERMOUNT, "-u", str(self.WORKSPACE)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            self._mounted = False
            return

        logger.warning(
            "fusermount3 -u failed (rc=%s): %s; attempting lazy unmount (-z)",
            result.returncode,
            result.stderr.strip(),
        )
        lazy_result = subprocess.run(
            [self.FUSERMOUNT, "-u", "-z", str(self.WORKSPACE)],
            check=False,
            capture_output=True,
            text=True,
        )
        if lazy_result.returncode == 0:
            self._mounted = False
            return

        raise RuntimeError(f"Failed to unmount /workspace (both standard and lazy): {lazy_result.stderr.strip()}")

    def _merge(self, src: Path, dst: Path) -> None:
        """Merge src directory contents into dst, handling whiteouts."""
        for root, _dirs, files in os.walk(src):
            rel = Path(root).relative_to(src)
            for filename in files:
                full_path = Path(root) / filename
                if not self._is_whiteout(full_path, filename):
                    continue

                if filename.startswith(_WHITEOUT_PREFIX):
                    shadowed_name = filename[len(_WHITEOUT_PREFIX) :]
                else:
                    shadowed_name = filename

                target = dst / rel / shadowed_name
                if target.exists() or target.is_symlink():
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink(missing_ok=True)

        subprocess.run(
            ["cp", "-a", f"{src}/.", f"{dst}/"],
            check=True,
            capture_output=True,
        )

    def _extract_effects(self, upper: Path, tool_use_id: str) -> list[dict[str, Any]]:
        """Walk an upper layer and emit attributed file effect dicts."""
        effects: list[dict[str, Any]] = []

        if not upper.exists():
            return effects

        for root, dirs, files in os.walk(upper):
            rel_root = Path(root).relative_to(upper)
            dirs[:] = [d for d in dirs if not d.startswith(_WHITEOUT_PREFIX)]

            for filename in files:
                full_path = Path(root) / filename
                rel_path = str(rel_root / filename)

                if self._is_whiteout(full_path, filename):
                    if filename.startswith(_WHITEOUT_PREFIX):
                        original_name = filename[len(_WHITEOUT_PREFIX) :]
                    else:
                        original_name = filename
                    original_path = str(rel_root / original_name)
                    had_content = self._read_lower_content(original_path)
                    effects.append(
                        {
                            "effect_type": "file_delete",
                            "path": original_path,
                            "had_content": had_content,
                            "caused_by": tool_use_id,
                        }
                    )
                elif self._existed_in_lower(rel_root / filename):
                    old_content = self._read_lower_content(str(rel_root / filename))
                    try:
                        new_content = full_path.read_text(errors="replace")
                    except OSError:
                        new_content = ""
                    effects.append(
                        {
                            "effect_type": "file_patch",
                            "path": rel_path,
                            "old_content": old_content,
                            "new_content": new_content,
                            "caused_by": tool_use_id,
                        }
                    )
                else:
                    try:
                        content = full_path.read_text(errors="replace")
                    except OSError:
                        content = ""
                    effects.append(
                        {
                            "effect_type": "file_create",
                            "path": rel_path,
                            "content": content,
                            "caused_by": tool_use_id,
                        }
                    )

        return effects

    @staticmethod
    def _is_whiteout(path: Path, filename: str) -> bool:
        """Detect OverlayFS whiteout markers."""
        try:
            st = os.lstat(path)
            if stat.S_ISCHR(st.st_mode):
                return os.major(st.st_rdev) == 0 and os.minor(st.st_rdev) == 0
        except OSError:
            pass
        return filename.startswith(_WHITEOUT_PREFIX)

    def _existed_in_lower(self, rel_path: Path) -> bool:
        """Check if a file existed in the pre-tool state."""
        accumulated_path = self._accumulated / rel_path
        if accumulated_path.exists():
            return not self._is_whiteout(accumulated_path, accumulated_path.name)

        wh_path = self._accumulated / rel_path.parent / f"{_WHITEOUT_PREFIX}{rel_path.name}"
        if wh_path.exists():
            return False

        return any((layer / rel_path).exists() for layer in self._lower_layers)

    def _read_lower_content(self, rel_path: str) -> str:
        """Read pre-tool file content from accumulated or lower layers."""
        rel = Path(rel_path)
        accumulated_file = self._accumulated / rel
        if accumulated_file.exists():
            if self._is_whiteout(accumulated_file, accumulated_file.name):
                return ""
            try:
                return accumulated_file.read_text(errors="replace")
            except OSError:
                pass

        wh_path = self._accumulated / rel.parent / f"{_WHITEOUT_PREFIX}{rel.name}"
        if wh_path.exists():
            return ""

        for layer in self._lower_layers:
            layer_file = layer / rel
            if layer_file.exists():
                if self._is_whiteout(layer_file, layer_file.name):
                    return ""
                try:
                    return layer_file.read_text(errors="replace")
                except OSError:
                    continue
        return ""


__all__ = ["FuseOverlayManager", "fuse_overlayfs_available"]
