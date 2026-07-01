"""Shared managed session-exec wire protocol helpers."""

from __future__ import annotations

import binascii
import json
import time
from base64 import b64decode, b64encode
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping


DAEMON_OWNED_ENV_KEYS = frozenset(
    {
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "VCS_CORE_CAPTURE_ACTIVE",
        "VCS_CORE_CAPTURE_EPOCH",
        "VCS_CORE_COMMAND_OPERATION_ID",
        "VCS_CORE_FS_CAPTURE_DEBUG_LOG",
        "VCS_CORE_FS_CAPTURE_SUPPRESS",
        "VCS_CORE_HOOK_SUPPRESS",
        "VCS_CORE_HOOK_SOCKET",
        "VCS_CORE_SCOPE",
        "VCS_CORE_SCOPE_INSTANCE_ID",
        "VCS_CORE_SESSION",
        "VCS_CORE_WORKSPACE",
    }
)


@dataclass(frozen=True)
class ManagedExecRequest:
    argv: list[str]
    scope_name: str | None
    create: bool
    parent: str | None
    cwd_subpath: str | None
    capture_requested: bool
    started_at: float
    client_pid: int
    env: dict[str, str]


@dataclass(frozen=True)
class ManagedExecStartedFrame:
    operation_id: str
    pid: int
    pgid: int
    type: Literal["started"] = "started"


@dataclass(frozen=True)
class ManagedExecStreamFrame:
    stream: Literal["stdout", "stderr"]
    data: bytes

    @property
    def type(self) -> Literal["stdout", "stderr"]:
        return self.stream


@dataclass(frozen=True)
class ManagedExecMessageFrame:
    type: Literal["error", "recording_error"]
    message: str


@dataclass(frozen=True)
class ManagedExecExitFrame:
    exit_code: int
    type: Literal["exit"] = "exit"


ManagedExecFrame: TypeAlias = (
    ManagedExecStartedFrame | ManagedExecStreamFrame | ManagedExecMessageFrame | ManagedExecExitFrame
)


def managed_exec_request_from_params(params: Mapping[str, object]) -> ManagedExecRequest:
    allowed = {
        "argv",
        "scope",
        "create",
        "parent",
        "cwd_subpath",
        "capture_requested",
        "capture_debug_log",
        "env",
        "started_at",
        "client_pid",
    }
    unexpected = sorted(set(params) - allowed)
    if unexpected:
        rendered = ", ".join(unexpected)
        raise ValueError(f"Unsupported managed exec parameter(s): {rendered}.")

    argv = _require_string_list(params, "argv")
    if not argv:
        raise ValueError("Managed session exec requires a non-empty argv.")

    env = _string_mapping(params.get("env"))
    daemon_owned = sorted(set(env) & DAEMON_OWNED_ENV_KEYS)
    if daemon_owned:
        rendered = ", ".join(daemon_owned)
        raise ValueError(f"Managed exec env contains daemon-owned key(s): {rendered}.")
    env["VCS_CORE_SESSION"] = "1"
    capture_debug_log = _optional_str(params, "capture_debug_log")
    if capture_debug_log is not None:
        env["VCS_CORE_FS_CAPTURE_DEBUG_LOG"] = capture_debug_log

    return ManagedExecRequest(
        argv=argv,
        scope_name=_optional_str(params, "scope"),
        create=_optional_bool(params, "create", default=False),
        parent=_optional_str(params, "parent"),
        cwd_subpath=_optional_str(params, "cwd_subpath"),
        capture_requested=_optional_bool(params, "capture_requested", default=False),
        started_at=_optional_float(params, "started_at", default=time.time()),
        client_pid=_optional_int(params, "client_pid", default=0),
        env=env,
    )


def sanitized_managed_exec_env(env: dict[str, str]) -> dict[str, str]:
    """Drop caller-supplied values for environment owned by the session daemon."""
    return {key: value for key, value in env.items() if key not in DAEMON_OWNED_ENV_KEYS}


def started_frame(*, operation_id: str, pid: int, pgid: int) -> ManagedExecStartedFrame:
    return ManagedExecStartedFrame(operation_id=operation_id, pid=pid, pgid=pgid)


def stream_frame(stream: Literal["stdout", "stderr"], data: bytes) -> ManagedExecStreamFrame:
    return ManagedExecStreamFrame(stream=stream, data=data)


def error_frame(message: str) -> ManagedExecMessageFrame:
    return ManagedExecMessageFrame(type="error", message=message)


def recording_error_frame(message: str) -> ManagedExecMessageFrame:
    return ManagedExecMessageFrame(type="recording_error", message=message)


def exit_frame(exit_code: int) -> ManagedExecExitFrame:
    return ManagedExecExitFrame(exit_code=exit_code)


def encode_managed_exec_frame(frame: Mapping[str, object] | ManagedExecFrame) -> bytes:
    return (json.dumps(_managed_exec_frame_to_wire(frame)) + "\n").encode()


def decode_managed_exec_frame(raw_line: bytes) -> dict[str, object]:
    frame = json.loads(raw_line.decode())
    if not isinstance(frame, dict):
        raise TypeError("session daemon sent non-object exec frame.")
    return dict(frame)


def decode_managed_exec_response_frame(raw_line: bytes) -> ManagedExecFrame:
    frame = decode_managed_exec_frame(raw_line)
    frame_type = _require_str(frame, "type")
    if frame_type == "started":
        return ManagedExecStartedFrame(
            operation_id=_require_str(frame, "operation_id"),
            pid=_require_int(frame, "pid"),
            pgid=_require_int(frame, "pgid"),
        )
    if frame_type in {"stdout", "stderr"}:
        return ManagedExecStreamFrame(
            stream=frame_type,  # type: ignore[arg-type]
            data=_decode_b64_payload(_require_str(frame, "data_b64")),
        )
    if frame_type in {"error", "recording_error"}:
        return ManagedExecMessageFrame(
            type=frame_type,  # type: ignore[arg-type]
            message=_require_str(frame, "message"),
        )
    if frame_type == "exit":
        return ManagedExecExitFrame(exit_code=_require_int(frame, "exit_code"))
    raise ValueError(f"session daemon sent unknown exec frame: {frame_type!r}")


def encode_managed_exec_request(params: Mapping[str, object]) -> bytes:
    return encode_managed_exec_frame({"method": "exec_managed", "params": dict(params)})


def decode_managed_exec_request(raw_line: bytes) -> ManagedExecRequest:
    frame = decode_managed_exec_frame(raw_line)
    method = _require_str(frame, "method")
    if method != "exec_managed":
        raise ValueError(f"Expected exec_managed request, got {method!r}.")
    params = frame.get("params")
    if not isinstance(params, dict):
        raise TypeError("Expected object request field 'params'.")
    return managed_exec_request_from_params(params)


def _managed_exec_frame_to_wire(frame: Mapping[str, object] | ManagedExecFrame) -> dict[str, object]:
    if isinstance(frame, ManagedExecStartedFrame):
        return {
            "type": frame.type,
            "operation_id": frame.operation_id,
            "pid": frame.pid,
            "pgid": frame.pgid,
        }
    if isinstance(frame, ManagedExecStreamFrame):
        return {
            "type": frame.type,
            "data_b64": b64encode(frame.data).decode(),
        }
    if isinstance(frame, ManagedExecMessageFrame):
        return {
            "type": frame.type,
            "message": frame.message,
        }
    if isinstance(frame, ManagedExecExitFrame):
        return {
            "type": frame.type,
            "exit_code": frame.exit_code,
        }
    return dict(frame)


def _require_string_list(params: Mapping[str, object], key: str) -> list[str]:
    value = params.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"Expected string-list parameter '{key}'.")


def _optional_str(params: Mapping[str, object], key: str) -> str | None:
    value = params.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"Expected string parameter '{key}'.")


def _optional_bool(params: Mapping[str, object], key: str, default: bool) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"Expected boolean parameter '{key}'.")


def _optional_int(params: Mapping[str, object], key: str, default: int) -> int:
    value = params.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"Expected integer parameter '{key}'.")


def _optional_float(params: Mapping[str, object], key: str, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"Expected number parameter '{key}'.")


def _require_str(params: Mapping[str, object], key: str) -> str:
    value = params.get(key)
    if isinstance(value, str):
        return value
    raise ValueError(f"Expected string frame field '{key}'.")


def _require_int(params: Mapping[str, object], key: str) -> int:
    value = params.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"Expected integer frame field '{key}'.")


def _decode_b64_payload(value: str) -> bytes:
    try:
        return b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ValueError("Expected base64-encoded exec stream payload.") from exc


def _string_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("Expected object parameter 'env'.")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError("Managed exec env must contain only string keys and values.")
        result[key] = item
    return result
