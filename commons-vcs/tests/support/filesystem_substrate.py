"""Minimal filesystem substrate (Phase -1 spike).

Stages a workdir's contents into a Git index and runs `git write-tree`,
returning an `Observation` whose `workspace_tree` is the resulting Git
tree OID. No actual checkout, no working-tree mutation, no overlay
machinery — just the index→tree path that a real FilesystemSubstrate
would use under §8.5.

The Observation contains *what was captured*; the coordinator decides
what to do with it.

Phase -1 simplifications baked in here:
- No isolation: caller controls when to call `capture()`. There's no
  fork/merge/discard around it.
- No incremental capture: every call walks the entire workdir and
  rebuilds the index from scratch. Real substrates would track diffs.
- No symlink / mode handling: regular files only.
- No staging coordinator: the substrate doesn't see the Repo and can't
  write Objects (per §8.5: "substrates have no Repo handle").
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# Per refactor.md §8.5
_GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Observation:
    """What a substrate reports to the coordinator about a captured state.

    Phase -1 reduces refactor.md §8.5's full Observation shape to the
    fields actually exercised: `workspace_tree` (the Git tree OID) and
    `metadata` (free-form). The §8.5 `effect` field is handled by the
    coordinator's pending-effect register in this spike (the shepherd
    effect id is passed to the coordinator separately, not via the
    Observation).
    """

    workspace_tree: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class FilesystemSubstrate:
    """Stages a directory into a Git index and produces a tree OID.

    Construction parameters:
        workdir: the directory whose contents are captured.
        git_dir: a Git object database (typically created via
                 `git init --bare`). The substrate writes blobs and
                 the tree into this database; nothing is checked out.
    """

    def __init__(self, workdir: Path, git_dir: Path) -> None:
        self.workdir = workdir
        self.git_dir = git_dir

    def _git(self, *args: str, env: dict | None = None) -> str:
        """Run a git plumbing command against `git_dir`. Returns stdout (stripped)."""
        full_env = os.environ.copy()
        full_env["GIT_DIR"] = str(self.git_dir)
        if env:
            full_env.update(env)
        result = subprocess.run(
            ["git", *args],
            cwd=str(self.workdir),
            env=full_env,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def capture(self) -> Observation:
        """Stage all regular files under workdir into a fresh index, then
        write the tree. Returns the resulting tree OID inside an Observation.
        """
        # Use a fresh per-call index so capture() is stateless.
        index_path = self.git_dir / "phase_minus_1_capture.index"
        if index_path.exists():
            index_path.unlink()

        env = {"GIT_INDEX_FILE": str(index_path)}

        # Walk workdir, stage every regular file.
        files = sorted(p for p in self.workdir.rglob("*") if p.is_file())
        for path in files:
            relpath = str(path.relative_to(self.workdir))
            # `git hash-object -w` writes the blob to the object DB and
            # returns its OID. `git update-index --add --cacheinfo`
            # binds the OID to the path in our index.
            oid = self._git(
                "hash-object",
                "-w",
                "--",
                str(path),
                env=env,
            )
            self._git(
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{oid},{relpath}",
                env=env,
            )

        tree_oid = self._git("write-tree", env=env)

        if not _GIT_OID_RE.match(tree_oid):
            raise RuntimeError(f"unexpected git write-tree output: {tree_oid!r} (not a Git OID format)")

        index_path.unlink(missing_ok=True)

        return Observation(
            workspace_tree=tree_oid,
            metadata={"file_count": len(files)},
        )
