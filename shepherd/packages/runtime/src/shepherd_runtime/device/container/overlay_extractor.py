"""Standalone-core fallback for overlay effect extraction.

This module provides extraction of filesystem effects from OverlayFS
upper layers. Changes in the upper layer represent modifications made
during container execution.

Key features:
- Detect file creates, modifications, and deletes
- Generate unified diffs for patch effects
- Link effects to causing IntentEffects via caused_by (D25)
- Handle OverlayFS whiteout files (D9: char device 0,0)
- Extract from VM upper layers on macOS (via SSH + tar)

Architecture:
    OverlayFS Upper Layer         Extracted Effects
    ─────────────────────         ─────────────────
    new_file.py              →    FileCreate(path=..., caused_by=...)
    modified_file.py         →    FilePatch(path=..., caused_by=...)
    .wh.deleted_file.py      →    FileDelete(path=..., caused_by=...)

    On macOS with VM overlay:
    ─────────────────────────
    VMUpperLayerReader reads upper layer via SSH + tar,
    then extraction proceeds as normal.

See Also:
    design/containerized-execution/PROPOSAL-containerized-execution-reconciliation.md
    design/DECISIONS.md#d9-whiteout-detection - Whiteout detection
    design/DECISIONS.md#d25-causality-linking - Causality linking
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import (
    DiffPatch,
    Effect,
    FileCreate,
    FileDelete,
    FilePatch,
)

if TYPE_CHECKING:
    from shepherd_runtime.device.container.effect_collector import EffectCollector
    from shepherd_runtime.device.container.podman import OverlayMount
    from shepherd_runtime.device.container.vm_extraction import VMUpperLayerReader
    from shepherd_runtime.device.container.vm_paths import VMCommandRunner

logger = logging.getLogger(__name__)

# OverlayFS whiteout prefix
_WHITEOUT_PREFIX = ".wh."


class OverlayEffectExtractor:
    """Extract ResultEffects from OverlayFS upper layer.

    After container execution, the upper layer contains all filesystem
    changes made by the agent. This extractor walks the upper layer
    and generates appropriate effects for each change.

    Extraction modes:
    - VM overlay (macOS): Reads upper layer from VM via SSH + tar
    - Local OverlayFS (Linux): Walks upper layer directly on host

    Causality Linking:
        Effects are linked to the IntentEffect (tool call) that caused them
        via the `caused_by` field. This is determined by querying the
        EffectCollector for the last completed intent ID.

    Whiteout Handling:
        OverlayFS uses "whiteout" files to represent deletions. These are
        character devices with major/minor number 0/0, or files prefixed
        with ".wh." depending on the OverlayFS version.
    """

    def __init__(
        self,
        lower_path: Path | None = None,
        vm_runner: VMCommandRunner | None = None,
    ):
        """Initialize the extractor.

        Args:
            lower_path: Optional path to lower layer for diff generation.
                        If not provided, diffs will be generated against
                        empty content for new files.
            vm_runner: VMCommandRunner for reading VM upper layers on macOS.
                       Required for extracting from VM overlay paths.
        """
        self.lower_path = lower_path
        self._vm_runner = vm_runner
        self._vm_reader: VMUpperLayerReader | None = None

    @property
    def vm_reader(self) -> VMUpperLayerReader | None:
        """Lazy-initialize VM upper layer reader."""
        if self._vm_runner and not self._vm_reader:
            from shepherd_runtime.device.container.vm_extraction import VMUpperLayerReader

            self._vm_reader = VMUpperLayerReader(self._vm_runner)
        return self._vm_reader

    def extract(
        self,
        overlay: OverlayMount,
        collector: EffectCollector,
        manifest: dict[str, str] | None = None,
    ) -> list[Effect]:
        """Extract effects from OverlayFS upper layer.

        Routes to appropriate extraction method based on overlay type:
        - VM paths (macOS): Extract via SSH + tar from VM
        - Local paths with upper content: Walk upper layer directly

        Uses manifest to filter out pre-applied bundle state.
        Only files that differ from manifest generate effects.

        All effects are linked to the last completed intent via caused_by.

        Args:
            overlay: The OverlayMount to extract from.
            collector: EffectCollector with intent tracking.
            manifest: Filename -> content hash for filtering pre-applied state.

        Returns:
            List of extracted effects.
        """
        manifest = manifest or {}
        caused_by = collector.get_last_completed_intent_id()
        logger.debug(f"Extracting effects: overlay={overlay.context_name}, caused_by={caused_by}")
        logger.debug(f"  upper={overlay.upper}, is_vm_path={overlay.is_vm_path}")
        logger.debug(f"  merged={overlay.merged}")
        logger.debug(f"  lower={overlay.lower}")
        logger.debug(f"  original_host_path={overlay.original_host_path}")
        logger.debug(f"  manifest entries={len(manifest)}")

        # Route based on overlay type
        if overlay.is_vm_path:
            # macOS: extract via SSH + tar from VM
            logger.debug("  Using VM extraction (SSH + tar)")
            effects = self._extract_from_vm(overlay, caused_by, manifest)

        elif overlay.upper.exists() and any(overlay.upper.iterdir()):
            # Linux: walk upper layer directly
            logger.debug("  Using local upper layer extraction (OverlayFS)")
            effects = self._extract_from_upper(overlay, caused_by, manifest)

        else:
            logger.debug("  No overlay content found")
            effects = []

        logger.debug(f"  Extracted {len(effects)} effects")
        return effects

    def _should_emit_effect(
        self,
        rel_path: str,
        content: bytes,
        manifest: dict[str, str],
    ) -> bool:
        """Check if file change should emit an effect.

        Returns False if file matches manifest (unchanged from bundle).

        Args:
            rel_path: Relative path of the file.
            content: File content.
            manifest: Filename -> content hash mapping.

        Returns:
            True if effect should be emitted, False if file unchanged from bundle.
        """
        if rel_path not in manifest:
            return True  # New file, emit effect

        content_hash = hashlib.sha256(content).hexdigest()
        return manifest[rel_path] != content_hash

    def _extract_from_vm(
        self,
        overlay: OverlayMount,
        caused_by: str | None,
        manifest: dict[str, str] | None = None,
    ) -> list[Effect]:
        """Extract effects from VM upper layer via SSH + tar.

        Reads the upper layer contents from the VM in a single round-trip,
        then processes each file to generate appropriate effects.

        Args:
            overlay: OverlayMount with VM paths (is_vm_path=True).
            caused_by: ID of causing intent for effect linking.
            manifest: Filename -> content hash for filtering pre-applied state.

        Returns:
            List of extracted effects.

        Raises:
            RuntimeError: If vm_runner not provided.
        """
        if not self.vm_reader:
            raise RuntimeError("VM extraction requires vm_runner. Pass vm_runner to OverlayEffectExtractor.__init__().")

        manifest = manifest or {}
        effects: list[Effect] = []

        # Read all files from VM upper layer
        for file_info in self.vm_reader.read_upper_layer(overlay.upper):
            rel_path = file_info.relative_path

            if file_info.is_whiteout:
                # File was deleted - create FileDelete effect
                effects.append(
                    self._create_delete_effect_from_vm(
                        path=str(rel_path),
                        caused_by=caused_by,
                        overlay=overlay,
                    )
                )

            elif file_info.is_directory:
                # Directory created - usually implicit, skip for now
                # Could track DirectoryCreate effects if needed
                pass

            else:
                # Check manifest to filter pre-applied bundle state
                if not self._should_emit_effect(str(rel_path), file_info.content or b"", manifest):
                    logger.debug(f"Skipping {rel_path} - unchanged from bundle")
                    continue

                # File created or modified
                effect = self._create_file_effect_from_vm(
                    overlay=overlay,
                    rel_path=rel_path,
                    content=file_info.content,
                    caused_by=caused_by,
                )
                if effect:
                    effects.append(effect)

        return effects

    def _create_file_effect_from_vm(
        self,
        overlay: OverlayMount,
        rel_path: Path,
        content: bytes | None,
        caused_by: str | None,
    ) -> Effect | None:
        """Create FileCreate or FilePatch effect for VM file.

        Determines if file is new or modified by checking lower layer
        on the host (accessible via VirtioFS/original_host_path).

        Args:
            overlay: The overlay mount with original_host_path.
            rel_path: Relative path within the overlay.
            content: File content from VM upper layer.
            caused_by: ID of causing intent.

        Returns:
            FileCreate for new files, FilePatch for modifications.
        """
        # Check if file exists in lower layer (on host via VirtioFS)
        lower_file: Path | None = None
        if overlay.original_host_path:
            lower_file = overlay.original_host_path / rel_path

        new_content = (content or b"").decode("utf-8", errors="replace")

        if lower_file and lower_file.exists():
            # File exists in lower layer - this is a modification
            try:
                original_content = lower_file.read_text(errors="replace")
            except (OSError, UnicodeDecodeError) as e:
                logger.warning(
                    f"Cannot read lower file {rel_path} for diff: {e}. Patch will show empty original content."
                )
                original_content = ""

            return FilePatch(
                path=str(rel_path),
                old_content=original_content,
                new_content=new_content,
                caused_by=caused_by,
            )

        # New file - doesn't exist in lower layer
        return FileCreate(
            path=str(rel_path),
            content=new_content,
            caused_by=caused_by,
        )

    def _create_delete_effect_from_vm(
        self,
        path: str,
        caused_by: str | None,
        overlay: OverlayMount,
    ) -> FileDelete:
        """Create FileDelete effect for VM whiteout.

        Reads original content from lower layer on host if available.

        Args:
            path: Relative path of deleted file.
            caused_by: ID of causing intent.
            overlay: The overlay mount.

        Returns:
            FileDelete effect with original content if available.
        """
        had_content = ""

        # Try to read original content from host lower layer
        if overlay.original_host_path:
            lower_file = overlay.original_host_path / path
            if lower_file.exists():
                try:
                    had_content = lower_file.read_text(errors="replace")
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning(
                        f"Cannot read deleted file {path} for content: {e}. "
                        f"Delete effect will show empty original content."
                    )

        return FileDelete(
            path=path,
            had_content=had_content,
            caused_by=caused_by,
        )

    def _extract_from_upper(
        self,
        overlay: OverlayMount,
        caused_by: str | None,
        manifest: dict[str, str] | None = None,
    ) -> list[Effect]:
        """Extract effects from OverlayFS upper layer (Linux).

        Args:
            overlay: The OverlayMount with populated upper layer.
            caused_by: ID of causing intent.
            manifest: Filename -> content hash for filtering pre-applied state.

        Returns:
            List of extracted effects.
        """
        manifest = manifest or {}
        effects: list[Effect] = []
        upper_path = overlay.upper

        # Walk the upper layer
        for root, dirs, files in os.walk(upper_path):
            rel_root = Path(root).relative_to(upper_path)

            # Filter out whiteout directories
            dirs[:] = [d for d in dirs if not d.startswith(_WHITEOUT_PREFIX)]

            for filename in files:
                upper_file = Path(root) / filename
                rel_path = rel_root / filename

                if self._is_whiteout(upper_file, filename):
                    # This is a deletion — strip .wh. prefix if present,
                    # otherwise the filename is already the original name
                    # (char device 0,0 whiteouts use the original filename).
                    if filename.startswith(_WHITEOUT_PREFIX):
                        original_name = filename[len(_WHITEOUT_PREFIX) :]
                    else:
                        original_name = filename
                    original_path = rel_root / original_name
                    effects.append(
                        self._create_delete_effect(
                            path=str(original_path),
                            caused_by=caused_by,
                            overlay=overlay,
                        )
                    )
                else:
                    # Check manifest to filter pre-applied bundle state
                    try:
                        content = upper_file.read_bytes()
                    except OSError:
                        content = b""

                    if not self._should_emit_effect(str(rel_path), content, manifest):
                        logger.debug(f"Skipping {rel_path} - unchanged from bundle")
                        continue

                    if self._existed_in_lower(overlay, rel_path):
                        # This is a modification
                        effects.append(
                            self._create_patch_effect(
                                path=str(rel_path),
                                upper_file=upper_file,
                                caused_by=caused_by,
                                overlay=overlay,
                            )
                        )
                    else:
                        # This is a new file
                        effects.append(
                            self._create_file_effect(
                                path=str(rel_path),
                                upper_file=upper_file,
                                caused_by=caused_by,
                            )
                        )

        return effects

    def extract_workspace_patch(
        self,
        overlay: OverlayMount,
        collector: EffectCollector,
    ) -> WorkspacePatchCaptured | None:
        """Extract a WorkspacePatchCaptured from overlay changes.

        Generates a unified diff of all changes in the upper layer,
        suitable for workspace state derivation.

        Args:
            overlay: The OverlayMount to extract from.
            collector: EffectCollector with intent tracking.

        Returns:
            WorkspacePatchCaptured if changes exist, None otherwise.
        """
        # For VM paths (macOS), generate diff via VM reader
        if overlay.is_vm_path:
            diff_content = self._generate_diff_from_vm(overlay)
        else:
            diff_content = self._generate_diff(overlay)

        if not diff_content.strip():
            return None

        # Get changed files (also needs VM-aware method)
        if overlay.is_vm_path:
            files_changed = self._get_changed_files_from_vm(overlay)
        else:
            files_changed = self._get_changed_files(overlay)

        caused_by = collector.get_last_completed_intent_id()

        patch = DiffPatch.from_diff(
            patch=diff_content,
            files=files_changed,
            source_step=caused_by,
        )

        return WorkspacePatchCaptured(
            files_changed=files_changed,
            patch_hash=patch.sha256 or "",
            patch_size_bytes=len(patch.patch),
            patch=patch,
            caused_by=caused_by,
        )

    def _is_whiteout(self, path: Path, filename: str) -> bool:
        """Detect if a file is an OverlayFS whiteout marker.

        Whiteouts can be:
        1. Character devices with major/minor 0/0 (D9)
        2. Files prefixed with ".wh."

        Args:
            path: Full path to the file.
            filename: Just the filename.

        Returns:
            True if this is a whiteout marker.
        """
        # Check for .wh. prefix
        if filename.startswith(_WHITEOUT_PREFIX):
            return True

        # Check for character device 0,0
        try:
            stat_result = os.lstat(path)  # Use lstat to not follow symlinks
            if stat.S_ISCHR(stat_result.st_mode):
                # Check if major and minor are both 0
                major = os.major(stat_result.st_rdev)
                minor = os.minor(stat_result.st_rdev)
                return major == 0 and minor == 0
        except OSError:
            pass

        return False

    @staticmethod
    def _lower_base(overlay: OverlayMount) -> Path:
        """Return the base (original workspace) path for the lower layer.

        For stacked overlays ``overlay.lower`` is a *list* of paths.
        We use ``original_host_path`` when available (preferred), otherwise
        fall back to the single lower path or the last element of the list
        (which is the original workspace — OverlayFS lowerdir is ordered
        newest-first).
        """
        if overlay.original_host_path is not None:
            return overlay.original_host_path
        if isinstance(overlay.lower, Path):
            return overlay.lower
        return overlay.lower[-1]

    def _existed_in_lower(self, overlay: OverlayMount, rel_path: Path) -> bool:
        """Check if a file existed in the lower layer.

        Args:
            overlay: The overlay mount.
            rel_path: Relative path within the overlay.

        Returns:
            True if file exists in lower layer.
        """
        lower_file = self._lower_base(overlay) / rel_path
        return lower_file.exists()

    def _create_file_effect(
        self,
        path: str,
        upper_file: Path,
        caused_by: str | None,
    ) -> FileCreate:
        """Create a FileCreate effect.

        Args:
            path: Relative path of the file.
            upper_file: Path to the file in upper layer.
            caused_by: ID of causing intent.

        Returns:
            FileCreate effect.
        """
        try:
            content = upper_file.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            content = ""

        return FileCreate(
            path=path,
            content=content,
            caused_by=caused_by,
        )

    def _create_patch_effect(
        self,
        path: str,
        upper_file: Path,
        caused_by: str | None,
        overlay: OverlayMount,
    ) -> FilePatch:
        """Create a FilePatch effect.

        Args:
            path: Relative path of the file.
            upper_file: Path to the file in upper layer.
            caused_by: ID of causing intent.
            overlay: The overlay mount.

        Returns:
            FilePatch effect.
        """
        lower_file = self._lower_base(overlay) / path

        try:
            old_content = lower_file.read_text(errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Cannot read lower file {path}: {e}. Patch will show empty original content.")
            old_content = ""

        try:
            new_content = upper_file.read_text(errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Cannot read upper file {path}: {e}.")
            new_content = ""

        return FilePatch(
            path=path,
            old_content=old_content,
            new_content=new_content,
            caused_by=caused_by,
        )

    def _create_delete_effect(
        self,
        path: str,
        caused_by: str | None,
        overlay: OverlayMount,
    ) -> FileDelete:
        """Create a FileDelete effect.

        Args:
            path: Relative path of the deleted file.
            caused_by: ID of causing intent.
            overlay: The overlay mount.

        Returns:
            FileDelete effect.
        """
        lower_file = self._lower_base(overlay) / path

        try:
            had_content = lower_file.read_text(errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Cannot read deleted file {path}: {e}. Delete effect will show empty original content.")
            had_content = ""

        return FileDelete(
            path=path,
            had_content=had_content,
            caused_by=caused_by,
        )

    def _generate_diff(self, overlay: OverlayMount) -> str:
        """Generate unified diff of all changes in overlay.

        Walks the upper layer (which only contains modified/new files due to
        OverlayFS copy-on-write) and diffs each file individually against its
        lower-layer counterpart.  Produces per-file unified diffs with
        ``git apply``-compatible ``a/`` / ``b/`` path prefixes.

        For VM paths, use _generate_diff_from_vm() instead.

        Args:
            overlay: The overlay mount (must be local paths, not VM).

        Returns:
            Unified diff content.

        Raises:
            ConfigurationError: If overlay has VM paths.
        """
        # Guard: VM paths must use _generate_diff_from_vm()
        if overlay.is_vm_path:
            from shepherd_core.errors import ConfigurationError

            raise ConfigurationError(
                context_id=overlay.context_name,
                message=(
                    f"_generate_diff() does not support VM paths (upper={overlay.upper}). "
                    f"Use _generate_diff_from_vm() instead, or ensure vm_runner is configured."
                ),
            )

        if not overlay.upper.exists() or not any(overlay.upper.iterdir()):
            return ""

        files = self._get_changed_files(overlay)
        base = self._lower_base(overlay)
        parts: list[str] = []
        for rel_path in files:
            upper_file = overlay.upper / rel_path

            if rel_path.startswith(".git/") or rel_path == ".git":
                continue

            if self._is_whiteout(upper_file, upper_file.name):
                continue  # whiteouts handled separately by extract()

            lower_file = base / rel_path

            if not lower_file.exists():
                result = subprocess.run(
                    ["diff", "-u", "/dev/null", str(upper_file)],
                    capture_output=True,
                    check=False,
                )
            else:
                result = subprocess.run(
                    ["diff", "-u", str(lower_file), str(upper_file)],
                    capture_output=True,
                    check=False,
                )

            raw = result.stdout.decode("utf-8", errors="replace")
            rewritten = _rewrite_diff_paths(raw, rel_path)
            parts.append(rewritten)

        return "".join(parts)

    def _manual_diff(self, overlay: OverlayMount) -> str:
        """Generate manual diff when system diff unavailable.

        Args:
            overlay: The overlay mount.

        Returns:
            Simple diff content.
        """
        lines = []
        changed_files = self._get_changed_files(overlay)

        for file_path in changed_files:
            upper_file = overlay.upper / file_path
            lower_file = self._lower_base(overlay) / file_path

            if upper_file.name.startswith(_WHITEOUT_PREFIX):
                original = file_path.replace(_WHITEOUT_PREFIX, "", 1)
                lines.append(f"--- a/{original}")
                lines.append("+++ /dev/null")
                lines.append("@@ -1 +0,0 @@")
                lines.append("-[deleted]")
            elif lower_file.exists():
                lines.append(f"--- a/{file_path}")
                lines.append(f"+++ b/{file_path}")
                lines.append("@@ modified @@")
            else:
                lines.append("--- /dev/null")
                lines.append(f"+++ b/{file_path}")
                lines.append("@@ +1 @@")
                lines.append("+[new file]")

        return "\n".join(lines)

    def _get_changed_files(self, overlay: OverlayMount) -> tuple[str, ...]:
        """Get list of changed files in overlay by walking the upper layer.

        Args:
            overlay: The overlay mount.

        Returns:
            Tuple of relative file paths that were changed.
        """
        if not overlay.upper.exists():
            return ()

        files: list[str] = []
        for root, _dirs, filenames in os.walk(overlay.upper):
            rel_root = Path(root).relative_to(overlay.upper)
            for filename in filenames:
                rel_path = rel_root / filename
                files.append(str(rel_path))
        return tuple(files)

    def _generate_diff_from_vm(self, overlay: OverlayMount) -> str:
        """Generate unified diff from VM upper layer.

        Reads files from VM via SSH and generates diff against lower layer
        which is accessible via VirtioFS.

        Args:
            overlay: The overlay mount with VM paths.

        Returns:
            Unified diff content.
        """
        if not self.vm_reader:
            logger.warning("VM diff generation requires vm_runner")
            return ""

        lines: list[str] = []

        for file_info in self.vm_reader.read_upper_layer(overlay.upper):
            if file_info.is_directory:
                continue

            rel_path = str(file_info.relative_path)

            # Skip .git directory
            if rel_path.startswith(".git/") or rel_path == ".git":
                continue

            if file_info.is_whiteout:
                # Deletion: show removal of original content
                original_path = rel_path.replace(_WHITEOUT_PREFIX, "", 1)
                lower_file = Path(overlay.original_host_path) / original_path  # type: ignore[arg-type]
                if lower_file.exists():
                    try:
                        original_content = lower_file.read_text(errors="replace")
                        lines.append(f"--- a/{original_path}")
                        lines.append("+++ /dev/null")
                        orig_lines = original_content.splitlines()
                        lines.append(f"@@ -1,{len(orig_lines)} +0,0 @@")
                        for line in orig_lines:
                            lines.append(f"-{line}")
                    except (OSError, UnicodeDecodeError):
                        pass
            else:
                # New or modified file
                new_content = file_info.content.decode("utf-8", errors="replace") if file_info.content else ""
                lower_file = Path(overlay.original_host_path) / rel_path  # type: ignore[arg-type]

                if lower_file.exists():
                    # Modified file
                    try:
                        original_content = lower_file.read_text(errors="replace")
                        lines.append(f"--- a/{rel_path}")
                        lines.append(f"+++ b/{rel_path}")
                        # Simple diff (not unified format, but good enough for patch)
                        orig_lines = original_content.splitlines()
                        new_lines = new_content.splitlines()
                        lines.append(f"@@ -1,{len(orig_lines)} +1,{len(new_lines)} @@")
                        for line in orig_lines:
                            lines.append(f"-{line}")
                        for line in new_lines:
                            lines.append(f"+{line}")
                    except (OSError, UnicodeDecodeError):
                        pass
                else:
                    # New file
                    lines.append("--- /dev/null")
                    lines.append(f"+++ b/{rel_path}")
                    new_lines = new_content.splitlines()
                    lines.append(f"@@ -0,0 +1,{len(new_lines)} @@")
                    for line in new_lines:
                        lines.append(f"+{line}")

        return "\n".join(lines)

    def _get_changed_files_from_vm(self, overlay: OverlayMount) -> tuple[str, ...]:
        """Get list of changed files from VM upper layer.

        Args:
            overlay: The overlay mount with VM paths.

        Returns:
            Tuple of relative file paths that were changed.
        """
        if not self.vm_reader:
            logger.warning("VM file listing requires vm_runner")
            return ()

        files: list[str] = []
        for file_info in self.vm_reader.read_upper_layer(overlay.upper):
            if file_info.is_directory:
                continue
            rel_path = str(file_info.relative_path)
            # Skip .git directory
            if rel_path.startswith(".git/") or rel_path == ".git":
                continue
            files.append(rel_path)

        return tuple(files)


def _rewrite_diff_paths(raw: str, rel_path: str) -> str:
    """Rewrite diff ``---``/``+++`` lines to git-compatible ``a/``/``b/`` prefixes.

    Args:
        raw: Raw unified diff output from ``diff -u``.
        rel_path: Relative path of the file within the overlay.

    Returns:
        Diff with rewritten path headers.
    """
    lines: list[str] = []
    for line in raw.splitlines(keepends=True):
        if line.startswith("--- "):
            lines.append("--- /dev/null\n" if "/dev/null" in line else f"--- a/{rel_path}\n")
        elif line.startswith("+++ "):
            lines.append("+++ /dev/null\n" if "/dev/null" in line else f"+++ b/{rel_path}\n")
        else:
            lines.append(line)
    return "".join(lines)


__all__ = ["OverlayEffectExtractor"]
