"""Command-correlated filesystem capture journal and reducer helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vcs_core._fs_capture import FsCaptureEvent, FsCaptureOp


CAPTURE_EVENT_EFFECT = "CaptureEvent"
CAPTURE_REDUCTION_KIND = "vcs_core.fs_capture_reduction"
CAPTURE_DIAGNOSTIC_KIND = "vcs_core.fs_capture_diagnostic"


@dataclass(frozen=True)
class CaptureJournalEvent:
    """One durable raw filesystem capture event linked to a command envelope."""

    command_operation_id: str
    binding_name: str
    op: FsCaptureOp
    path: str
    scope: str
    scope_instance_id: str
    pid: int
    proc_seq: int
    global_seq: int
    event_seq: int
    capture_mechanism: str = "preload"
    capture_epoch: str | None = None
    ppid: int | None = None
    exe: str | None = None
    cwd: str | None = None


def capture_event_metadata(
    *,
    command_operation_id: str,
    binding_name: str,
    event: FsCaptureEvent,
    global_seq: int,
    event_seq: int,
    capture_mechanism: str,
    capture_epoch: str | None = None,
) -> dict[str, Any]:
    """Return metadata for one raw capture journal entry."""
    metadata: dict[str, Any] = {
        "command_operation_id": command_operation_id,
        "binding_name": binding_name,
        "capture_mode": "direct",
        "capture_record": "raw_event",
        "capture_status": "journaled",
        "capture_mechanism": capture_mechanism,
        "op": event.op,
        "path": event.path,
        "capture_scope": event.scope,
        "capture_scope_instance_id": event.scope_instance_id,
        "pid": event.pid,
        "proc_seq": event.proc_seq,
        "global_seq": global_seq,
        "event_seq": event_seq,
    }
    if capture_epoch is not None:
        metadata["capture_epoch"] = capture_epoch
    if event.ppid is not None:
        metadata["ppid"] = event.ppid
    if event.exe is not None:
        metadata["exe"] = event.exe
    if event.cwd is not None:
        metadata["cwd"] = event.cwd
    return metadata


def capture_event_from_metadata(metadata: dict[str, object]) -> CaptureJournalEvent | None:
    """Parse a CaptureEvent commit's metadata into a reducer input event."""
    if metadata.get("type") != CAPTURE_EVENT_EFFECT:
        return None
    if metadata.get("capture_record") != "raw_event":
        return None
    command_operation_id = _str_field(metadata, "command_operation_id")
    binding_name = _str_field(metadata, "binding_name")
    op = _str_field(metadata, "op")
    if op not in {"write_open", "write_observed", "write_close", "metadata_change", "unlink"}:
        return None
    return CaptureJournalEvent(
        command_operation_id=command_operation_id,
        binding_name=binding_name,
        op=op,  # type: ignore[arg-type]
        path=_str_field(metadata, "path"),
        scope=_str_field(metadata, "capture_scope"),
        scope_instance_id=_str_field(metadata, "capture_scope_instance_id"),
        pid=_int_field(metadata, "pid"),
        proc_seq=_int_field(metadata, "proc_seq"),
        global_seq=_int_field(metadata, "global_seq"),
        event_seq=_int_field(metadata, "event_seq"),
        capture_mechanism=_str_field(metadata, "capture_mechanism", default="preload"),
        capture_epoch=_optional_str_field(metadata, "capture_epoch"),
        ppid=_optional_int_field(metadata, "ppid"),
        exe=_optional_str_field(metadata, "exe"),
        cwd=_optional_str_field(metadata, "cwd"),
    )


def ordered_capture_events(commits: tuple[Any, ...]) -> tuple[CaptureJournalEvent, ...]:
    """Extract and order raw capture events from an operation history."""
    events: list[CaptureJournalEvent] = []
    for commit in commits:
        metadata = getattr(commit, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        event = capture_event_from_metadata(metadata)
        if event is not None:
            events.append(event)
    return tuple(sorted(events, key=lambda event: (event.global_seq, event.pid, event.proc_seq)))


def covered_capture_paths(events: tuple[CaptureJournalEvent, ...]) -> tuple[str, ...]:
    """Return stable first-seen path coverage for a command's raw events."""
    seen: set[str] = set()
    paths: list[str] = []
    for event in events:
        if event.path in seen:
            continue
        seen.add(event.path)
        paths.append(event.path)
    return tuple(paths)


def reduction_operation_id(command_operation_id: str) -> str:
    """Return the deterministic reducer operation id for one command envelope."""
    return f"red_{command_operation_id}"


def _str_field(metadata: dict[str, object], key: str, *, default: str | None = None) -> str:
    value = metadata.get(key, default)
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"Capture event metadata field {key!r} must be a non-empty string.")


def _optional_str_field(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"Capture event metadata field {key!r} must be a non-empty string when present.")


def _int_field(metadata: dict[str, object], key: str) -> int:
    value = metadata.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"Capture event metadata field {key!r} must be an integer.")


def _optional_int_field(metadata: dict[str, object], key: str) -> int | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"Capture event metadata field {key!r} must be an integer when present.")
