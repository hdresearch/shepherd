"""Helper: programmatically initialize (or reuse) a Shepherd workspace.

``ensure_shepherd_workspace(path)`` is idempotent — safe to call on a directory
that was already initialized.  The returned ``ShepherdWorkspace`` is **not**
closed here; callers own the lifecycle (call ``ws.close()``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def ensure_shepherd_workspace(workspace: Path | str) -> Any:
    """Initialize a Shepherd workspace at *workspace* and open it.

    Steps:
    1. ``git init`` (no-op if already a repo).
    2. Create an empty initial commit so the tree has a HEAD (needed by the
       ``adopt="git-head"`` importer).
    3. ``initialize_workspace`` via shepherd_dialect (creates ``.vcscore``).
    4. ``ShepherdWorkspace.discover`` to open and activate.

    Returns a ``ShepherdWorkspace`` handle.
    """
    from shepherd_dialect import initialize_workspace
    from shepherd_dialect.workspace_control import ShepherdWorkspace

    ws = Path(workspace).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    # --- git init + empty root commit (idempotent) ---
    if not (ws / ".git").exists():
        subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "shepherd workspace init"],
            cwd=ws, check=True, capture_output=True,
        )

    # --- .vcscore init (idempotent) ---
    if not (ws / ".vcscore").exists():
        initialize_workspace(ws, adopt="git-head", explicit_adopt=False)

    return ShepherdWorkspace.discover(ws, activate=True)


def seed_workspace(ws_dir: Path, files: dict[str, str]) -> None:
    """Write *files* (path → content) into *ws_dir* and commit them to git."""
    for rel_path, content in files.items():
        target = ws_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=ws_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed: initial workspace files"],
        cwd=ws_dir, check=True, capture_output=True,
    )
