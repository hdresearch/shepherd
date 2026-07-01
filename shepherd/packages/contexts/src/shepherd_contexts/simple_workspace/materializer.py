"""Materializer for SimpleWorkspace contexts.

Provides:
- SimpleWorkspaceMaterializationIntent: Describes changesets to apply
- SimpleWorkspaceMaterializer: Applies changesets to filesystem
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from shepherd_runtime.materialization import MaterializationIntent, MaterializationResult

if TYPE_CHECKING:
    from shepherd_contexts.simple_workspace.delta import FileChangeset


# =============================================================================
# Intent
# =============================================================================


@dataclass(frozen=True)
class SimpleWorkspaceMaterializationIntent(MaterializationIntent):
    """Intent for SimpleWorkspace materialization.

    Contains the changesets to apply to the filesystem.
    """

    context_type: str = field(default="SimpleWorkspace", init=False)
    changesets: tuple[FileChangeset, ...] = ()


# =============================================================================
# Materializer
# =============================================================================


class SimpleWorkspaceMaterializer:
    """Materializer for SimpleWorkspace contexts.

    Applies file changesets to the filesystem with backup/restore support.
    Backup is transient - only used during materialization for atomic rollback
    on failure.

    This class is stateless - all state is passed via intent/result.

    Features:
    - Drift detection: Verifies files haven't been modified externally before modify operations
    - Atomic rollback: Backs up files before modification, restores on failure
    """

    def _check_drift(self, file_path: Path, expected_hash: str) -> str | None:
        """Check if file was modified externally (drift detection).

        Compares the current file content hash with the expected hash from
        when the changeset was created. If they differ, external modification
        has occurred.

        Args:
            file_path: Path to the file to check
            expected_hash: Expected SHA-256 hash of file contents

        Returns:
            Error message if drift detected, None if file matches expected state
        """
        import hashlib

        if not file_path.exists():
            return f"Drift detected: {file_path.name} was deleted externally."

        try:
            current_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if current_hash != expected_hash:
                return f"Drift detected: {file_path.name} was modified externally."
        except OSError as e:
            return f"Drift detection failed: {e}"

        return None

    def materialize(self, intent: SimpleWorkspaceMaterializationIntent) -> MaterializationResult:
        """Apply changesets to filesystem.

        Args:
            intent: The materialization intent with changesets

        Returns:
            MaterializationResult with success/failure and affected paths
        """
        if not intent.changesets:
            return MaterializationResult.ok()

        affected_paths: list[str] = []
        backup_dir = Path(tempfile.mkdtemp(prefix="shepherd-backup-"))

        try:
            for changeset in intent.changesets:
                for delta in changeset.deltas:
                    full_path = intent.target_path / delta.path

                    # Drift detection for modify operations
                    if delta.operation == "modify" and delta.old_content_hash:
                        drift_error = self._check_drift(full_path, delta.old_content_hash)
                        if drift_error:
                            # Rollback any changes made so far before returning failure
                            self._restore_from_backup(intent.target_path, backup_dir)
                            return MaterializationResult.failure(drift_error)

                    # Backup existing file before modifying
                    if full_path.exists():
                        backup_path = backup_dir / delta.path
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(full_path, backup_path)

                    # Apply delta based on operation
                    if delta.operation == "delete":
                        full_path.unlink(missing_ok=True)
                    else:  # create or modify
                        content = delta.decode_content()
                        if content is not None:
                            full_path.parent.mkdir(parents=True, exist_ok=True)
                            full_path.write_bytes(content)
                            # Apply mode if specified
                            if delta.new_mode is not None:
                                full_path.chmod(delta.new_mode)

                    affected_paths.append(delta.path)

            return MaterializationResult.ok(
                paths_affected=tuple(set(affected_paths)),
            )

        except Exception as e:  # noqa: BLE001
            # Rollback on failure
            self._restore_from_backup(intent.target_path, backup_dir)
            return MaterializationResult.failure(str(e))

        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)

    def can_rollback(self) -> bool:
        """Backup is transient - rollback not supported after materialize."""
        return False

    def rollback(
        self,
        intent: SimpleWorkspaceMaterializationIntent,
        result: MaterializationResult,
    ) -> None:
        """Not supported - backup is transient.

        Rollback only happens automatically during materialize() on failure.
        """

    def _restore_from_backup(self, target_path: Path, backup_dir: Path) -> None:
        """Restore files from backup directory.

        Args:
            target_path: The workspace root path
            backup_dir: Directory containing backed-up files
        """
        for backup_file in backup_dir.rglob("*"):
            if backup_file.is_file():
                rel_path = backup_file.relative_to(backup_dir)
                target_file = target_path / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, target_file)


__all__ = [
    "SimpleWorkspaceMaterializationIntent",
    "SimpleWorkspaceMaterializer",
]
