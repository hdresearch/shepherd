"""Daemon-owned state machine for command-correlated filesystem capture."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal

CapturePolicy = Literal["event_only", "managed_lifecycle", "shell_command"]


@dataclass(frozen=True)
class CaptureAcceptResult:
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class CaptureDrainResult:
    complete: bool
    reason: str | None = None
    accepted_count: int = 0
    processed_count: int = 0
    high_water_by_pid: dict[int, int] = field(default_factory=dict)
    registered_count: int = 0
    finished_count: int = 0
    capture_policy: CapturePolicy = "event_only"


@dataclass
class _CaptureCommandState:
    status: str = "open"
    accepted: set[int] = field(default_factory=set)
    processed: set[int] = field(default_factory=set)
    events_by_global_seq: dict[int, tuple[int, int]] = field(default_factory=dict)
    proc_seq_to_global_by_pid: dict[int, dict[int, int]] = field(default_factory=dict)
    capture_policy: CapturePolicy = "event_only"
    shell_pid: int | None = None
    shell_finish_proc_seq_by_pid: dict[int, int] = field(default_factory=dict)
    registered_pids: set[int] = field(default_factory=set)
    finished_pids: set[int] = field(default_factory=set)
    last_proc_seq_by_pid: dict[int, int] = field(default_factory=dict)
    incomplete_reason: str | None = None


class CaptureAuthority:
    """Track capture completeness for one daemon process.

    Durable raw events still live in operation history. This object owns the
    in-memory admission/drain state that decides whether a command's journal is
    complete enough for direct-authoritative reduction.
    """

    def __init__(self) -> None:
        self._states: dict[str, _CaptureCommandState] = {}
        self._terminal_status: dict[str, str] = {}
        self._lock = threading.RLock()

    def begin(
        self,
        command_operation_id: str,
        *,
        require_lifecycle: bool = False,
        capture_policy: CapturePolicy | None = None,
        shell_pid: int | None = None,
    ) -> None:
        with self._lock:
            self._terminal_status.pop(command_operation_id, None)
            state = self._states.setdefault(command_operation_id, _CaptureCommandState())
            if capture_policy is None:
                resolved_policy: CapturePolicy = "managed_lifecycle" if require_lifecycle else "event_only"
            else:
                resolved_policy = capture_policy
            state.capture_policy = resolved_policy
            state.shell_pid = shell_pid
            state.shell_finish_proc_seq_by_pid.clear()

    def register_process(self, command_operation_id: str, *, pid: int) -> CaptureAcceptResult:
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is None:
                return CaptureAcceptResult(False, self._missing_state_reason(command_operation_id))
            if state.status in {"complete", "incomplete"}:
                return CaptureAcceptResult(False, f"capture_{state.status}")
            state.registered_pids.add(pid)
            return CaptureAcceptResult(True)

    def finish_process(
        self,
        command_operation_id: str,
        *,
        pid: int,
        last_proc_seq: int,
    ) -> CaptureAcceptResult:
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is None:
                return CaptureAcceptResult(False, self._missing_state_reason(command_operation_id))
            if state.status in {"complete", "incomplete"}:
                return CaptureAcceptResult(False, f"capture_{state.status}")
            if last_proc_seq < 0:
                if state.incomplete_reason is None:
                    state.incomplete_reason = "hook_proc_seq_out_of_order"
                return CaptureAcceptResult(True)
            if (
                state.capture_policy in {"managed_lifecycle", "shell_command"}
                and pid not in state.registered_pids
                and pid != state.shell_pid
            ):
                if state.incomplete_reason is None:
                    state.incomplete_reason = "missing_process_start"
                return CaptureAcceptResult(True)
            state.finished_pids.add(pid)
            state.last_proc_seq_by_pid[pid] = max(last_proc_seq, state.last_proc_seq_by_pid.get(pid, 0))
            return CaptureAcceptResult(True)

    def finish_shell_command(
        self,
        command_operation_id: str,
        *,
        pid: int,
        proc_seq: int,
    ) -> CaptureAcceptResult:
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is None:
                return CaptureAcceptResult(False, self._missing_state_reason(command_operation_id))
            if state.status in {"complete", "incomplete"}:
                return CaptureAcceptResult(False, f"capture_{state.status}")
            if state.capture_policy != "shell_command":
                return CaptureAcceptResult(False, "capture_policy_not_shell_command")
            if proc_seq < 1:
                if state.incomplete_reason is None:
                    state.incomplete_reason = "hook_proc_seq_out_of_order"
                return CaptureAcceptResult(True)
            if state.shell_pid is None:
                state.shell_pid = pid
            elif state.shell_pid != pid:
                if state.incomplete_reason is None:
                    state.incomplete_reason = "shell_pid_mismatch"
                return CaptureAcceptResult(True)
            state.shell_finish_proc_seq_by_pid[pid] = max(proc_seq, state.shell_finish_proc_seq_by_pid.get(pid, 0))
            return CaptureAcceptResult(True)

    def accept_event(
        self, command_operation_id: str, *, pid: int, proc_seq: int, global_seq: int
    ) -> CaptureAcceptResult:
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is None:
                return CaptureAcceptResult(False, self._missing_state_reason(command_operation_id))
            if state.status in {"complete", "incomplete"}:
                return CaptureAcceptResult(False, f"capture_{state.status}")

            if global_seq in state.accepted:
                return CaptureAcceptResult(True)

            state.accepted.add(global_seq)
            state.events_by_global_seq[global_seq] = (pid, proc_seq)
            proc_seq_to_global = state.proc_seq_to_global_by_pid.setdefault(pid, {})
            prior_global_seq = proc_seq_to_global.get(proc_seq)
            if proc_seq < 1 and state.incomplete_reason is None:
                state.incomplete_reason = "hook_proc_seq_out_of_order"
            elif prior_global_seq is not None and prior_global_seq != global_seq and state.incomplete_reason is None:
                state.incomplete_reason = "hook_proc_seq_duplicate"
            else:
                proc_seq_to_global.setdefault(proc_seq, global_seq)
            return CaptureAcceptResult(True)

    def mark_processed(self, command_operation_id: str, *, global_seq: int) -> None:
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is not None:
                state.processed.add(global_seq)

    def mark_failed(self, command_operation_id: str, *, global_seq: int, reason: str) -> None:
        del global_seq
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is not None:
                if state.incomplete_reason is None:
                    state.incomplete_reason = reason
                state.status = "incomplete"

    def drain(
        self,
        command_operation_id: str,
        *,
        timeout_seconds: float = 1.0,
        quiet_period_seconds: float = 0.05,
    ) -> CaptureDrainResult:
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is None:
                return CaptureDrainResult(False, reason="unknown_command_operation")
            if state.status == "incomplete":
                return self._incomplete_result(state)
            state.status = "draining"

        deadline = time.monotonic() + timeout_seconds
        stable_since: float | None = None
        stable_snapshot: tuple[int, int, int, int, int] | None = None
        while True:
            with self._lock:
                if state.incomplete_reason is not None:
                    state.status = "incomplete"
                    return self._incomplete_result(state)

                accepted_complete = state.accepted <= state.processed
                sequences_complete = self._sequences_complete(state)
                lifecycle_complete = self._lifecycle_complete(state)
                snapshot = (
                    len(state.accepted),
                    len(state.processed),
                    len(state.registered_pids),
                    len(state.finished_pids),
                    len(state.shell_finish_proc_seq_by_pid),
                )
            now = time.monotonic()
            if accepted_complete and sequences_complete and lifecycle_complete:
                if snapshot != stable_snapshot:
                    stable_snapshot = snapshot
                    stable_since = now
                elif stable_since is not None and now - stable_since >= quiet_period_seconds:
                    with self._lock:
                        state.status = "complete"
                        return CaptureDrainResult(
                            True,
                            accepted_count=len(state.accepted),
                            processed_count=len(state.processed),
                            high_water_by_pid=self._high_water_by_pid(state),
                            registered_count=len(state.registered_pids),
                            finished_count=len(state.finished_pids),
                            capture_policy=state.capture_policy,
                        )
            else:
                stable_since = None
                stable_snapshot = None

            if now >= deadline:
                with self._lock:
                    state.status = "incomplete"
                    state.incomplete_reason = (
                        self._first_lifecycle_gap(state) or self._first_sequence_gap(state) or "hook_drain_timeout"
                    )
                    return self._incomplete_result(state)
            time.sleep(min(0.01, max(0.0, deadline - now)))

    def status(self, command_operation_id: str) -> str | None:
        with self._lock:
            state = self._states.get(command_operation_id)
            if state is not None:
                return state.status
            return self._terminal_status.get(command_operation_id)

    def finalize(self, command_operation_id: str) -> None:
        """Release per-event state after the durable command outcome is archived."""
        with self._lock:
            state = self._states.pop(command_operation_id, None)
            if state is not None:
                status = state.status if state.status in {"complete", "incomplete"} else "incomplete"
                self._terminal_status[command_operation_id] = status

    def active_count(self) -> int:
        with self._lock:
            return len(self._states)

    @staticmethod
    def _incomplete_result(state: _CaptureCommandState) -> CaptureDrainResult:
        return CaptureDrainResult(
            False,
            reason=state.incomplete_reason or "capture_incomplete",
            accepted_count=len(state.accepted),
            processed_count=len(state.processed),
            high_water_by_pid=CaptureAuthority._high_water_by_pid(state),
            registered_count=len(state.registered_pids),
            finished_count=len(state.finished_pids),
            capture_policy=state.capture_policy,
        )

    @staticmethod
    def _sequences_complete(state: _CaptureCommandState) -> bool:
        return CaptureAuthority._first_sequence_gap(state) is None

    @staticmethod
    def _lifecycle_complete(state: _CaptureCommandState) -> bool:
        return CaptureAuthority._first_lifecycle_gap(state) is None

    @staticmethod
    def _first_lifecycle_gap(state: _CaptureCommandState) -> str | None:
        if state.capture_policy == "event_only":
            return None
        if state.capture_policy == "shell_command":
            if state.shell_pid is None or state.shell_pid not in state.shell_finish_proc_seq_by_pid:
                return "missing_shell_command_finish"
            observed_pids = set(state.proc_seq_to_global_by_pid)
            unfinished_children = (state.registered_pids | observed_pids) - state.finished_pids
            unfinished_children.discard(state.shell_pid)
            if unfinished_children:
                return "background_process_still_running"
            for pid, finish_proc_seq in state.shell_finish_proc_seq_by_pid.items():
                proc_seq_to_global = state.proc_seq_to_global_by_pid.get(pid, {})
                for proc_seq in range(1, finish_proc_seq):
                    if proc_seq not in proc_seq_to_global:
                        return "hook_proc_seq_gap"
            for pid, last_proc_seq in state.last_proc_seq_by_pid.items():
                proc_seq_to_global = state.proc_seq_to_global_by_pid.get(pid, {})
                for proc_seq in range(1, last_proc_seq + 1):
                    if proc_seq not in proc_seq_to_global:
                        return "hook_proc_seq_gap"
            return None
        if not state.registered_pids:
            return "missing_process_start"
        if state.registered_pids != state.finished_pids:
            return "missing_process_finish"
        for pid, last_proc_seq in state.last_proc_seq_by_pid.items():
            proc_seq_to_global = state.proc_seq_to_global_by_pid.get(pid, {})
            for proc_seq in range(1, last_proc_seq + 1):
                if proc_seq not in proc_seq_to_global:
                    return "hook_proc_seq_gap"
        return None

    @staticmethod
    def _first_sequence_gap(state: _CaptureCommandState) -> str | None:
        for proc_seq_to_global in state.proc_seq_to_global_by_pid.values():
            if not proc_seq_to_global:
                continue
            max_proc_seq = max(proc_seq_to_global)
            for proc_seq in range(1, max_proc_seq + 1):
                if proc_seq not in proc_seq_to_global:
                    return "hook_proc_seq_gap"
        return None

    @staticmethod
    def _high_water_by_pid(state: _CaptureCommandState) -> dict[int, int]:
        processed_by_pid: dict[int, set[int]] = {}
        for global_seq in state.processed:
            event = state.events_by_global_seq.get(global_seq)
            if event is None:
                continue
            pid, proc_seq = event
            processed_by_pid.setdefault(pid, set()).add(proc_seq)

        high_water: dict[int, int] = {}
        for pid, proc_seqs in processed_by_pid.items():
            current = 0
            while current + 1 in proc_seqs:
                current += 1
            high_water[pid] = current
        return high_water

    def _missing_state_reason(self, command_operation_id: str) -> str:
        status = self._terminal_status.get(command_operation_id)
        if status in {"complete", "incomplete"}:
            return f"capture_{status}"
        return "unknown_command_operation"
