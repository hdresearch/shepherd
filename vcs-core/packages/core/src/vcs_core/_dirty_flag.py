"""Push crash recovery via dirty flag file."""

from __future__ import annotations

import json
import time
from pathlib import Path

from vcs_core._errors import DirtyPushError


def write_dirty_flag(repo_path: str, session_id: str) -> None:
    """Write dirty flag indicating push in progress."""
    flag = Path(repo_path) / "dirty"
    flag.write_text(json.dumps({"session_id": session_id, "timestamp": time.time()}))


def read_dirty_flag(repo_path: str) -> tuple[str, float] | None:
    """Read dirty flag. Returns (session_id, timestamp) or None."""
    flag = Path(repo_path) / "dirty"
    if not flag.exists():
        return None
    data = json.loads(flag.read_text())
    return data["session_id"], data["timestamp"]


def clear_dirty_flag(repo_path: str) -> None:
    """Remove dirty flag after successful push or recovery."""
    flag = Path(repo_path) / "dirty"
    flag.unlink(missing_ok=True)


def check_dirty_flag(repo_path: str) -> None:
    """Raise DirtyPushError if dirty flag exists."""
    flag = Path(repo_path) / "dirty"
    if not flag.exists():
        return
    try:
        result = read_dirty_flag(repo_path)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise DirtyPushError(session_id="", dirty_since=0.0, corrupt=True, detail=str(exc)) from exc
    if result is not None:
        session_id, timestamp = result
        raise DirtyPushError(session_id=session_id, dirty_since=timestamp)
