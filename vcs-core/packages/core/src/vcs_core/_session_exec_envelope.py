"""Shared session-exec operation envelope helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._vcscore_runtime import new_operation_id

if TYPE_CHECKING:
    from vcs_core.store import Store


EXEC_OUTCOMES = frozenset(
    (
        "success",
        "failed_exit",
        "signaled",
        "launch_error",
        "interrupted",
        "abandoned",
    )
)


def new_unique_command_operation_id(store: Store) -> str:
    while True:
        operation_id = new_operation_id().replace("op_", "cmd_", 1)
        if not store.operation_id_exists(operation_id):
            return operation_id


def new_unique_shell_lease_operation_id(store: Store) -> str:
    while True:
        operation_id = new_operation_id().replace("op_", "shl_", 1)
        if not store.operation_id_exists(operation_id):
            return operation_id


def new_capture_epoch_id() -> str:
    return new_operation_id().replace("op_", "cap_", 1)


def command_label(argv: list[str], *, transport: str = "exec", submitted_text: str | None = None) -> str:
    prefix = "session shell" if transport == "shell" else "session exec"
    rendered = (submitted_text or " ".join(argv)).strip() or prefix
    if len(rendered) > 80:
        rendered = f"{rendered[:77]}..."
    return f"{prefix}: {rendered}"


def completion_command_metadata(
    start_metadata: dict[str, object],
    *,
    outcome: str,
    ended_at: float,
    exit_code: int | None,
    signal: int | None,
    launch_error: str | None,
    abandoned_reason: str | None,
) -> dict[str, object]:
    validate_exec_outcome(
        outcome,
        exit_code=exit_code,
        signal=signal,
        launch_error=launch_error,
        abandoned_reason=abandoned_reason,
    )

    start_command = _start_command_from_metadata(start_metadata)
    started_at = _started_at_from_command(start_command)
    command: dict[str, object] = {
        "status": outcome,
        "ended_at": ended_at,
    }
    if start_command is not None:
        for field in (
            "argv",
            "cwd",
            "scope",
            "capture_requested",
            "managed",
            "client_pid",
            "capture_epoch",
            "capture_policy",
            "transport",
            "submitted_text",
            "shell_pid",
            "shell_lease_id",
            "daemon_instance_id",
            "shell_sequence",
        ):
            value = start_command.get(field)
            if value is not None:
                command[field] = value
    if started_at is not None:
        command["started_at"] = started_at
        command["duration_seconds"] = max(0.0, ended_at - started_at)
    if exit_code is not None:
        command["exit_code"] = exit_code
    if signal is not None:
        command["signal"] = signal
    if launch_error:
        command["launch_error"] = launch_error
    if abandoned_reason:
        command["abandoned_reason"] = abandoned_reason
    return command


def validate_exec_outcome(
    outcome: str,
    *,
    exit_code: int | None,
    signal: int | None,
    launch_error: str | None,
    abandoned_reason: str | None,
) -> None:
    if outcome not in EXEC_OUTCOMES:
        choices = ", ".join(sorted(EXEC_OUTCOMES))
        raise ValueError(f"Unsupported session exec outcome {outcome!r}. Supported: {choices}.")

    if outcome == "success":
        if exit_code != 0:
            raise ValueError("session exec success outcome requires exit_code=0.")
        _reject_fields(outcome, signal=signal, launch_error=launch_error, abandoned_reason=abandoned_reason)
        return

    if outcome == "failed_exit":
        _require_positive("exit_code", exit_code, outcome)
        _reject_fields(outcome, signal=signal, launch_error=launch_error, abandoned_reason=abandoned_reason)
        return

    if outcome == "signaled":
        _require_positive("signal", signal, outcome)
        _reject_fields(outcome, exit_code=exit_code, launch_error=launch_error, abandoned_reason=abandoned_reason)
        return

    if outcome == "launch_error":
        _require_non_empty("launch_error", launch_error, outcome)
        if exit_code is not None:
            _require_positive("exit_code", exit_code, outcome)
        _reject_fields(outcome, signal=signal, abandoned_reason=abandoned_reason)
        return

    if outcome == "interrupted":
        if signal is not None:
            _require_positive("signal", signal, outcome)
        _reject_fields(outcome, exit_code=exit_code, launch_error=launch_error, abandoned_reason=abandoned_reason)
        return

    _require_non_empty("abandoned_reason", abandoned_reason, outcome)
    _reject_fields(outcome, exit_code=exit_code, signal=signal, launch_error=launch_error)


def _start_command_from_metadata(metadata: dict[str, object]) -> dict[str, object] | None:
    command = metadata.get("command")
    if not isinstance(command, dict):
        return None
    return command


def _started_at_from_command(command: dict[str, object] | None) -> float | None:
    if command is None:
        return None
    value = command.get("started_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _require_positive(field: str, value: int | None, outcome: str) -> None:
    if value is None or value <= 0:
        raise ValueError(f"session exec {outcome} outcome requires a positive {field}.")


def _require_non_empty(field: str, value: str | None, outcome: str) -> None:
    if value is None or not value:
        raise ValueError(f"session exec {outcome} outcome requires {field}.")


def _reject_fields(outcome: str, **fields: object | None) -> None:
    present = sorted(name for name, value in fields.items() if value is not None)
    if present:
        rendered = ", ".join(present)
        raise ValueError(f"session exec {outcome} outcome does not accept: {rendered}.")
