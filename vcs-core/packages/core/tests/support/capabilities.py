"""Environment capability probes for tests."""

from __future__ import annotations

import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalBindCapability:
    """Result of probing whether local listener sockets can be bound."""

    available: bool
    reason: str | None = None


def probe_local_bind_capability(base_dir: Path) -> LocalBindCapability:
    """Check whether the current environment allows AF_UNIX bind() where the daemon uses it.

    The session daemon binds its sockets inside the workspace-local
    `.vcscore/` directory, so this probe should exercise that same path
    shape instead of treating `/tmp` or other unrelated locations as
    equivalent.
    """
    probe_root = base_dir / ".vcscore"
    probe_dir: Path | None = None
    try:
        probe_root.mkdir(parents=True, exist_ok=True)
        probe_dir = Path(tempfile.mkdtemp(prefix="mg-bind-", dir=str(probe_root)))
        socket_path = probe_dir / "s.sock"
        if socket_path.exists():
            socket_path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(socket_path))
        finally:
            listener.close()
    except PermissionError as exc:
        return LocalBindCapability(
            available=False,
            reason=f"workspace-local listener bind is not permitted in this environment: {exc}",
        )
    except OSError as exc:
        return LocalBindCapability(
            available=False,
            reason=f"workspace-local listener bind probe failed: {exc}",
        )
    finally:
        if probe_dir is not None:
            (probe_dir / "s.sock").unlink(missing_ok=True)
            probe_dir.rmdir()
    return LocalBindCapability(available=True)
