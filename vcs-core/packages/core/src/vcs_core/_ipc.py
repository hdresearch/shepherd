r"""IPC protocol types for vcs-core session daemon.

Minimal JSON-over-Unix-socket protocol. One connection per
request-response, then close.

Client sends:  {"method": "<name>", "params": {<key>: <value>, ...}}\\n
Server sends:  {"ok": true, "result": {<key>: <value>, ...}}\\n
          or:  {"ok": false, "error": "<message>"}\\n
"""

from __future__ import annotations

import errno
import json
import os
import socket
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Callable

SESSION_INFO_FILE = "session.json"
SESSION_SOCKET = "session.sock"
SESSION_LOG = "session.log"

_RECV_BUFSIZE = 65536

JsonObject = dict[str, object]


class SessionRequest(TypedDict):
    method: str
    params: JsonObject


class SessionOkResponse(TypedDict):
    ok: Literal[True]
    result: JsonObject


class SessionErrorResponse(TypedDict):
    ok: Literal[False]
    error: str


SessionResponse = SessionOkResponse | SessionErrorResponse


class _SocketLike(Protocol):
    def connect(self, address: str) -> None: ...

    def sendall(self, data: bytes) -> None: ...

    def shutdown(self, how: int) -> None: ...

    def recv(self, bufsize: int) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SessionInfo:
    """Persisted session metadata written by the daemon."""

    pid: int
    socket_path: str
    mount_path: str
    workspace: str
    started_at: float
    daemon_instance_id: str | None = None


def write_session_info(repo_path: str, info: SessionInfo) -> None:
    """Write session.json atomically."""
    path = Path(repo_path) / SESSION_INFO_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(info), indent=2))
    tmp.replace(path)


def read_session_info(repo_path: str) -> SessionInfo | None:
    """Read session.json, return None if missing or malformed."""
    path = Path(repo_path) / SESSION_INFO_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return SessionInfo(**data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def remove_session_info(repo_path: str) -> None:
    """Remove session.json if it exists."""
    path = Path(repo_path) / SESSION_INFO_FILE
    with suppress(FileNotFoundError):
        path.unlink()


def is_session_alive(repo_path: str) -> bool:
    """Check if a session daemon is running (session.json + PID alive)."""
    info = read_session_info(repo_path)
    if info is None:
        return False
    return _pid_alive(info.pid)


def _open_ipc_socket() -> socket.socket:
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)


def send_request(
    socket_path: str,
    method: str,
    params: JsonObject | None = None,
    *,
    socket_factory: Callable[[], _SocketLike] | None = None,
) -> SessionResponse:
    """Send a single IPC request and return the response.

    Connects to the Unix domain socket, sends the request as a
    JSON line, reads the response line, and closes. Raises
    ConnectionError if the daemon is unreachable.
    """
    request: SessionRequest = {"method": method, "params": params or {}}
    payload = json.dumps(request) + "\n"

    sock = _open_ipc_socket() if socket_factory is None else socket_factory()
    try:
        try:
            sock.connect(socket_path)
            sock.sendall(payload.encode())
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError as exc:
                if exc.errno != errno.ENOTCONN:
                    raise

            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(_RECV_BUFSIZE)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError as exc:
            msg = f"Could not reach session daemon at {socket_path}: {exc}"
            raise ConnectionError(msg) from exc

        raw = b"".join(chunks).decode().strip()
        if not raw:
            msg = "Empty response from session daemon."
            raise ConnectionError(msg)
        return _decode_session_response(json.loads(raw))
    finally:
        sock.close()


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive via kill(0)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _decode_session_response(response: object) -> SessionResponse:
    if not isinstance(response, dict):
        msg = "Invalid response from session daemon: expected JSON object."
        raise ConnectionError(msg)
    ok = response.get("ok")
    if ok is True:
        return {
            "ok": True,
            "result": _require_json_object(response.get("result", {}), context="session result"),
        }
    if ok is False:
        error = response.get("error", "Unknown error")
        return {"ok": False, "error": str(error)}
    msg = "Invalid response from session daemon: missing boolean 'ok' field."
    raise ConnectionError(msg)


def _require_json_object(value: object, *, context: str) -> JsonObject:
    if isinstance(value, dict):
        return cast("JsonObject", value)
    msg = f"Invalid {context} from session daemon: expected JSON object."
    raise ConnectionError(msg)
