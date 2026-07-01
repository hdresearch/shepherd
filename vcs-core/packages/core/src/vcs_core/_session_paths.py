"""Deterministic short runtime paths for live session IPC artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from vcs_core._ipc import SESSION_SOCKET

SESSION_HOOK_SOCKET = "session-hook.sock"
SESSION_RUNTIME_ROOT = "vcs-core-session"
SESSION_RUNTIME_KEY_PREFIX = "repo"
SESSION_RUNTIME_KEY_HEX_LEN = 16


def _runtime_key(repo_path: str) -> str:
    resolved = Path(repo_path).resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:SESSION_RUNTIME_KEY_HEX_LEN]
    return f"{SESSION_RUNTIME_KEY_PREFIX}-{digest}"


def session_runtime_root(repo_path: str) -> Path:
    """Return the deterministic short runtime root for one repository."""
    return Path("/tmp") / f"{SESSION_RUNTIME_ROOT}-{os.getuid()}" / _runtime_key(repo_path)


def session_socket_path(repo_path: str) -> str:
    """Return the live daemon IPC socket path for one repository."""
    return str(session_runtime_root(repo_path) / SESSION_SOCKET)


def session_hook_socket_path(repo_path: str) -> str:
    """Return the live hook IPC socket path for one repository."""
    return str(session_runtime_root(repo_path) / SESSION_HOOK_SOCKET)
