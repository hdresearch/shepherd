"""Socket request/response helpers for the session daemon."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from vcs_core._ipc import JsonObject, SessionErrorResponse, SessionOkResponse, SessionRequest


class _SocketLike(Protocol):
    def recv(self, bufsize: int) -> bytes: ...

    def sendall(self, data: bytes) -> None: ...


def read_request(conn: _SocketLike) -> SessionRequest | None:
    """Read one validated JSON request line from the daemon socket."""
    chunks: list[bytes] = []
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break

    raw = b"".join(chunks).decode().strip()
    if not raw:
        return None
    return _decode_session_request(json.loads(raw))


def send_response(conn: _SocketLike, *, ok: bool, result: JsonObject | None = None, error: str | None = None) -> None:
    """Write one JSON response line to the daemon socket."""
    payload: SessionOkResponse | SessionErrorResponse
    if ok:
        payload = {"ok": True, "result": result or {}}
    else:
        payload = {"ok": False, "error": error or "Unknown error"}
    conn.sendall((json.dumps(payload) + "\n").encode())


def _decode_session_request(request: object) -> SessionRequest:
    if not isinstance(request, dict):
        raise TypeError("Invalid JSON request: expected object.")
    method = request.get("method")
    if not isinstance(method, str):
        raise TypeError("Invalid JSON request: method must be a string.")
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise TypeError("Invalid JSON request: params must be an object.")
    return {"method": method, "params": cast("JsonObject", params)}
