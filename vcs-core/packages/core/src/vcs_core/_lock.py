"""Multi-coordinator exclusion via filesystem lock."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from vcs_core._errors import ActivationError

_LOCK_STALE_SECONDS = 300  # 5 minutes


def acquire_session_lock(repo_path: str, session_id: str) -> None:
    """Acquire cross-process lock on the .vcscore/ repository.

    Uses O_CREAT|O_EXCL for atomic creation. Lock file contains
    session ID, PID, and timestamp. Stale locks detected via PID
    liveness check + 5-minute age timeout.
    """
    lock_path = str(Path(repo_path) / "session.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{session_id}\n{os.getpid()}\n{time.time()}\n".encode())
        os.close(fd)
    except FileExistsError:
        holder_pid, lock_age = _read_lock(lock_path)
        if not _pid_alive(holder_pid) or lock_age > _LOCK_STALE_SECONDS:
            Path(lock_path).unlink()
            acquire_session_lock(repo_path, session_id)
            return
        msg = f"Repository locked by session (PID {holder_pid}, age {lock_age:.0f}s). Another session is active."
        raise ActivationError(msg) from None


def release_session_lock(repo_path: str, session_id: str) -> None:
    """Release this session's lock if this session still owns it."""
    lock = Path(repo_path) / "session.lock"
    with contextlib.suppress(FileNotFoundError):
        held_session_id = lock.read_text().split("\n", 1)[0]
        if held_session_id == session_id:
            lock.unlink()


def _read_lock(lock_path: str) -> tuple[int, float]:
    """Read lock file, return (PID, age_seconds)."""
    lines = Path(lock_path).read_text().strip().split("\n")
    pid = int(lines[1])
    ts = float(lines[2])
    return pid, time.time() - ts


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive via kill(0)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but we can't signal it
    return True
