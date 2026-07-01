"""Socket transport glue for daemon-owned managed session exec."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from vcs_core._managed_exec_protocol import encode_managed_exec_frame

if TYPE_CHECKING:
    import socket

    from vcs_core._ipc import JsonObject


logger = logging.getLogger(__name__)


class ManagedExecController:
    """Encode service-owned managed-exec frames onto a socket."""

    def __init__(self, daemon: Any) -> None:
        self._daemon = daemon

    def handle_connection(self, conn: socket.socket, params: JsonObject) -> None:
        service = getattr(self._daemon, "_managed_execution_service", None)
        if service is None:
            raise RuntimeError("Managed execution service is not available.")
        frames = service.run_params(params)
        try:
            for frame in frames:
                conn.sendall(encode_managed_exec_frame(frame))
        except OSError:
            logger.warning("Managed session exec stream disconnected", exc_info=True)
            with suppress(Exception):
                frames.close()
