"""Process supervision primitives for daemon-owned managed exec."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import threading


@dataclass(frozen=True)
class StreamItem:
    name: str
    data: bytes | None


def launch_process(*, argv: list[str], cwd: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def process_group_id(process: subprocess.Popen[bytes]) -> int:
    try:
        return os.getpgid(process.pid)
    except OSError:
        return process.pid


def terminate_process_group(pgid: int) -> None:
    with suppress(PermissionError, ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    time.sleep(0.05)
    with suppress(PermissionError, ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


def pump_stream(
    name: str,
    stream: Any,
    stream_queue: queue.Queue[StreamItem],
    stop_streams: threading.Event,
) -> None:
    try:
        if stream is None:
            return
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            if not put_stream_item(stream_queue, StreamItem(name, chunk), stop_streams):
                return
    finally:
        put_stream_item(stream_queue, StreamItem(name, None), stop_streams)


def put_stream_item(
    stream_queue: queue.Queue[StreamItem],
    item: StreamItem,
    stop_streams: threading.Event,
) -> bool:
    while not stop_streams.is_set():
        try:
            stream_queue.put(item, timeout=0.05)
            return True
        except queue.Full:
            continue
    return False


def shell_exit_code(return_code: int) -> int:
    return 128 + (-return_code) if return_code < 0 else return_code
