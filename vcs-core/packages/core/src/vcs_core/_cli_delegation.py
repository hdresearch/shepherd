"""Shared helpers for session-aware CLI delegation."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, TypeVar

from vcs_core import _cli_ipc
from vcs_core._cli_errors import emit_error_message

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

ResultT = TypeVar("ResultT")


def with_session_result(
    method: str,
    params: Mapping[str, object] | None,
    *,
    on_result: Callable[[dict[str, Any]], ResultT],
    on_fallback: Callable[[], ResultT],
) -> ResultT:
    """Run a command via session IPC when available, otherwise fall back locally."""
    result = _session_result_or_none(method, params)
    if result is not None:
        return on_result(result)
    return on_fallback()


def _session_result_or_none(method: str, params: Mapping[str, object] | None) -> dict[str, Any] | None:
    try:
        response = _cli_ipc.try_session_ipc(method, None if params is None else dict(params))
    except _cli_ipc.SessionIpcError as exc:
        emit_error_message(str(exc))
        sys.exit(1)
    if response is None:
        return None
    if not _cli_ipc.response_ok(response):
        emit_error_message(_cli_ipc.response_error(response))
        sys.exit(1)
    try:
        return _cli_ipc.response_result(response)
    except ValueError as exc:
        emit_error_message(str(exc))
        sys.exit(1)
