"""Internal request-dispatch helpers for the session daemon."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, cast, overload

from vcs_core._admission.identifiers import ParseError, ScopeName, parse_optional_scope_name
from vcs_core._app import AppCommandBlocked, VcsCoreApp
from vcs_core._app_blockers import AppBlocker
from vcs_core._capture_shadow_status import capture_shadow_status_for_history
from vcs_core._command_envelope import command_execution_options_from_mapping
from vcs_core._execution_wire import (
    serialize_operation_history,
    serialize_operation_summaries,
    serialize_recovery_snapshot,
)
from vcs_core._managed_exec_service import ManagedExecutionService
from vcs_core.types import (
    OperationHistory,
    OperationSummary,
    RecoverySnapshot,
    WorkspaceChange,
    normalize_command_value,
    normalize_recorded_command_outcome,
)

if TYPE_CHECKING:
    from vcs_core._ipc import JsonObject
    from vcs_core._session import SessionDaemon
    from vcs_core.store import Store


class SessionCommandDispatcher:
    """Own the daemon-backed session command surface behind SessionDaemon."""

    def __init__(self, daemon: SessionDaemon) -> None:
        self._daemon = daemon
        self._managed_execution = getattr(daemon, "_managed_execution_service", ManagedExecutionService(daemon))

    def dispatch(self, method: str, params: JsonObject) -> JsonObject:
        handlers = {
            "fork": self._do_fork,
            "get_state": self._do_get_state,
            "hook_state": self._do_hook_state,
            "merge": self._do_merge,
            "discard": self._do_discard,
            "switch": self._do_switch,
            "exec": self._do_exec,
            "exec_managed_signal": self._do_exec_managed_signal,
            "shell_capture_lease_begin": self._do_shell_capture_lease_begin,
            "shell_capture_lease_outcome": self._do_shell_capture_lease_outcome,
            "exec_envelope_begin": self._do_exec_envelope_begin,
            "exec_envelope_outcome": self._do_exec_envelope_outcome,
            "shell_command_not_admitted": self._do_shell_command_not_admitted,
            "operations": self._do_operations,
            "operation_history": self._do_operation_history,
            "query_readiness": self._do_query_readiness,
            "revalidate_readiness": self._do_revalidate_readiness,
            "recovery": self._do_recovery,
            "push": self._do_push,
            "overlay_status": self._do_overlay_status,
            "status_summary": self._do_status_summary,
            "stop": self._do_stop,
        }
        handler = handlers.get(method)
        if handler is None:
            msg = f"Unknown method: {method!r}. Available: {', '.join(sorted(handlers))}"
            raise ValueError(msg)
        return handler(params)

    def _do_fork(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            name = _parse_scope_name_for_method("branch", _require_str(params, "name"), allow_ground=False)
            parent_name = (
                _parse_optional_scope_name_for_method("branch", _optional_str(params, "parent", default="ground"))
                or "ground"
            )
            isolated = _optional_bool(params, "isolated", default=True)

            with VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app:
                result = app.branch(name=name, parent=parent_name, isolated=isolated)
                scope = self._daemon._mg.lookup_scope(result.name)
                if scope is None:
                    raise RuntimeError(f"Created scope {result.name!r} is not active.")
                mount_path = str(self._daemon._mg.overlay_mount_path_for_scope(scope))

        return {
            "name": result.name,
            "ref": result.ref,
            "instance_id": result.instance_id,
            "world_id": result.world_id,
            "mount_path": mount_path,
            "isolated": result.isolated,
        }

    def _do_get_state(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            hook_capabilities = _optional_string_list(params, "hook_capabilities")
            if hook_capabilities is None and _optional_bool(params, "capture", default=False):
                hook_capabilities = ["fs_capture"]
            return self._daemon._session_state(hook_capabilities=hook_capabilities)

    def _do_hook_state(self, _params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            accepted_seq = self._daemon._hook_frontier.accepted_seq
            processed_seq = self._daemon._hook_frontier.processed_seq
            self._daemon._hook_accepted_seq = accepted_seq
            self._daemon._hook_processed_seq = processed_seq
            return {
                "hook_socket": self._daemon._hook_socket_path,
                "accepted_seq": accepted_seq,
                "processed_seq": processed_seq,
                "processed_frontier_seq": processed_seq,
                "persisted_seq": self._daemon._hook_persisted_seq,
                "failed_seq": self._daemon._hook_failed_seq,
                "outcomes": dict(self._daemon._hook_outcomes),
            }

    def _do_merge(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            name = _parse_scope_name_for_method("merge", _require_str(params, "name"))
            self._managed_execution.assert_scope_lifecycle_unblocked(name, action="merge")
            with VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app:
                result = app.merge(name=name)

            if self._daemon._current_scope_name == name:
                self._daemon._current_scope_name = result.into

        return {"merged": result.merged, "into": result.into}

    def _do_discard(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            name = _parse_scope_name_for_method("discard", _require_str(params, "name"))
            self._managed_execution.assert_scope_lifecycle_unblocked(name, action="discard")
            with VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app:
                result = app.discard(name=name)

            if self._daemon._current_scope_name == name:
                self._daemon._current_scope_name = result.parent

        return {"discarded": result.discarded}

    def _do_switch(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            name = _parse_scope_name_for_method("switch", _require_str(params, "name"))
            with VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app:
                scope = app.resolve_scope(name)
                app.retain_restored_scope(name)
                mount_path = str(self._daemon._mg.overlay_mount_path_for_scope(scope))

            self._daemon._current_scope_name = name

        return {"current_scope": name, "mount_path": mount_path}

    def _do_exec(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            binding_name = _require_str(params, "binding")
            command = _require_str(params, "command")
            scope_name = (
                _parse_optional_scope_name_for_method(
                    "exec", _optional_str(params, "scope", default=self._daemon._current_scope_name)
                )
                or self._daemon._current_scope_name
            )
            cmd_params = _require_mapping(params, "params")
            options_value = params.get("options")
            if options_value is not None and not isinstance(options_value, dict):
                raise ValueError("Expected object parameter 'options'.")
            execution_options = command_execution_options_from_mapping(options_value)
            self._managed_execution.assert_scope_writer_unblocked(scope_name, action="execute command on")

            with VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app:
                outcome = app.execute(
                    binding_name=binding_name,
                    command=command,
                    scope_name=scope_name,
                    params=cmd_params,
                    execution_options=execution_options,
                    command_source="typed-json",
                )

        return normalize_recorded_command_outcome(outcome)

    def _do_exec_managed_signal(self, params: JsonObject) -> JsonObject:
        operation_id = _require_str(params, "operation_id")
        signal_value = _require_int(params, "signal")
        if signal_value <= 0:
            raise ValueError("exec managed signal requires a positive signal.")
        self._daemon._signal_managed_exec(operation_id, signal_value)
        return {}

    def _do_shell_capture_lease_begin(self, params: JsonObject) -> JsonObject:
        return self._managed_execution.begin_shell_capture_lease(params)

    def _do_shell_capture_lease_outcome(self, params: JsonObject) -> JsonObject:
        return self._managed_execution.finish_shell_capture_lease(params)

    def _do_exec_envelope_begin(self, params: JsonObject) -> JsonObject:
        return self._managed_execution.begin_envelope(params)

    def _do_exec_envelope_outcome(self, params: JsonObject) -> JsonObject:
        return self._managed_execution.record_outcome(params)

    def _do_shell_command_not_admitted(self, params: JsonObject) -> JsonObject:
        return self._managed_execution.record_shell_command_not_admitted(params)

    def _do_operations(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            mode = _optional_str(params, "mode", default="visible") or "visible"
            max_count = _optional_int(params, "max_count", default=20)
            scope_name = _parse_optional_scope_name_for_method("operations", _optional_str(params, "scope"))
            with VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app:
                default_scope = app.resolve_scope(
                    scope_name if scope_name is not None else self._daemon._current_scope_name
                )
                archived_scope = None if scope_name is None else app.resolve_scope(scope_name)

                visible = (
                    self._daemon._mg.visible_operations(ref=default_scope.ref, max_count=max_count)
                    if mode in {"visible", "all"}
                    else ()
                )
                open_operations = (
                    self._daemon._mg.open_operations(scope=default_scope) if mode in {"open", "all"} else ()
                )
                archived = (
                    self._daemon._mg.archived_operations(
                        max_count=max_count,
                        world_id=None if archived_scope is None else archived_scope.world_id,
                    )
                    if mode in {"archived", "all"}
                    else ()
                )

            if mode == "visible":
                return self._operations_envelope(mode=mode, scope_name=scope_name, visible=visible)
            if mode == "open":
                return self._operations_envelope(mode=mode, scope_name=scope_name, open_operations=open_operations)
            if mode == "archived":
                return self._operations_envelope(mode=mode, scope_name=scope_name, archived=archived)
            if mode == "all":
                return self._operations_envelope(
                    mode=mode,
                    scope_name=scope_name,
                    visible=visible,
                    open_operations=open_operations,
                    archived=archived,
                )

        msg = f"Unknown operations mode: {mode!r}"
        raise ValueError(msg)

    def _do_operation_history(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            selector = _require_str(params, "selector")
            scope_name = _parse_optional_scope_name_for_method("operation show", _optional_str(params, "scope"))
            with VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app:
                scope = None if scope_name is None else app.resolve_scope(scope_name)
                history = self._daemon._mg.resolve_operation_history(selector, scope=scope)
                return self._operation_history_envelope(selector=selector, scope_name=scope_name, history=history)

    def _do_query_readiness(self, params: JsonObject) -> JsonObject:
        from vcs_core._query_readiness import ReadinessRequest

        with self._daemon._lock:
            request = ReadinessRequest.from_json(dict(params))
            return cast("JsonObject", self._daemon._mg.query_readiness(request).to_json())

    def _do_revalidate_readiness(self, params: JsonObject) -> JsonObject:
        from vcs_core._query_readiness import ReadinessRequest

        raw_request = params.get("request")
        raw_precondition = params.get("precondition")
        if not isinstance(raw_request, dict):
            raise TypeError("revalidate_readiness request must be an object")
        if not isinstance(raw_precondition, dict):
            raise TypeError("revalidate_readiness precondition must be an object")
        with self._daemon._lock:
            request = ReadinessRequest.from_json(dict(raw_request))
            return cast(
                "JsonObject", self._daemon._mg.revalidate_readiness_precondition(request, raw_precondition).to_json()
            )

    def _do_recovery(self, params: JsonObject) -> JsonObject:
        with self._daemon._lock:
            snapshot = self._daemon._mg.recovery_snapshot(
                archived_max_count=_optional_int(params, "max_count", default=20)
            )
            return self._recovery_envelope(snapshot)

    def _do_push(self, params: JsonObject) -> JsonObject:
        del params
        raise ValueError("push is not supported while a persistent session is active.")

    def _do_overlay_status(self, _params: JsonObject) -> JsonObject:
        with (
            self._daemon._lock,
            VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app,
        ):
            scope = app.resolve_scope(self._daemon._current_scope_name)
            changes = self._daemon._mg.overlay_changes_for_scope(scope)
            return {
                "scope": scope.name,
                "mount_path": str(self._daemon._mg.overlay_mount_path_for_scope(scope)),
                "changes": [_overlay_change_envelope(change) for change in changes],
            }

    def _do_status_summary(self, _params: JsonObject) -> JsonObject:
        with (
            self._daemon._lock,
            VcsCoreApp.active_view(self._daemon._mg, current_scope=self._daemon._current_scope_name) as app,
        ):
            summary = app.repo_status(current_scope=self._daemon._current_scope_name)
            scope = app.resolve_scope(self._daemon._current_scope_name)
            changes = self._daemon._mg.overlay_changes_for_scope(scope)
            mount_path = str(self._daemon._mg.overlay_mount_path_for_scope(scope))
            return {
                "pid": os.getpid(),
                "current_scope": self._daemon._current_scope_name,
                "current_world_id": scope.world_id,
                "mount_path": mount_path,
                "workspace": self._daemon._workspace,
                "started_at": self._daemon._started_at if self._daemon._started_at is not None else time.time(),
                "overlay_change_count": len(changes),
                "local_changes": summary.local_changes,
                "commits_ahead": summary.commits_ahead,
                "live_scopes": [entry.name for entry in summary.live_scopes],
                "retained_scopes": [entry.name for entry in summary.retained_scopes],
                "blockers": [
                    {"kind": blocker.kind, "subject": blocker.subject, "detail": blocker.detail}
                    for blocker in summary.blockers
                ],
                "pending_operations": None if summary.pending_plan is None else summary.pending_plan.total_operations,
            }

    def _do_stop(self, _params: JsonObject) -> JsonObject:
        self._daemon._running = False
        shutdown = getattr(self._daemon, "_shutdown_managed_execs", None)
        if callable(shutdown):
            shutdown()
        return {"stopped": True}

    def _operations_envelope(
        self,
        *,
        mode: str,
        scope_name: str | None,
        visible: tuple[OperationSummary, ...] | list[OperationSummary] = (),
        open_operations: tuple[OperationSummary, ...] | list[OperationSummary] = (),
        archived: tuple[OperationSummary, ...] | list[OperationSummary] = (),
    ) -> JsonObject:
        return {
            "requested_mode": mode,
            "scope": scope_name,
            "visible": serialize_operation_summaries(visible),
            "open": serialize_operation_summaries(open_operations),
            "archived": serialize_operation_summaries(archived),
        }

    def _operation_history_envelope(
        self,
        *,
        selector: str,
        scope_name: str | None,
        history: OperationHistory,
    ) -> JsonObject:
        serialized = serialize_operation_history(history)
        payload: JsonObject = {
            "requested_selector": selector,
            "scope": scope_name,
            "summary": serialized["summary"],
            "commits": serialized["commits"],
        }
        repo_path = getattr(self._daemon._mg, "_repo_path", None)
        if repo_path is not None:
            capture_shadow = capture_shadow_status_for_history(repo_path, history)
            payload["capture_shadow"] = normalize_command_value(capture_shadow)
        return payload

    @staticmethod
    def _recovery_envelope(snapshot: RecoverySnapshot) -> JsonObject:
        return serialize_recovery_snapshot(snapshot)


def _require_str(params: JsonObject, key: str) -> str:
    value = params.get(key)
    if isinstance(value, str):
        return value
    raise ValueError(f"Expected string parameter '{key}'.")


def _require_string_list(params: JsonObject, key: str) -> list[str]:
    value = params.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"Expected string-list parameter '{key}'.")


def _require_int(params: JsonObject, key: str) -> int:
    value = params.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"Expected integer parameter '{key}'.")


def _require_mapping(params: JsonObject, key: str) -> dict[str, object]:
    value = params.get(key)
    if isinstance(value, dict):
        return {str(map_key): map_value for map_key, map_value in value.items()}
    raise ValueError(f"Expected object parameter '{key}'.")


def _parse_scope_name_for_method(method: str, raw: str, *, allow_ground: bool = True) -> str:
    try:
        return str(ScopeName.parse(raw, allow_ground=allow_ground))
    except ParseError as exc:
        raise AppCommandBlocked(
            command=method,
            blockers=(AppBlocker(kind="invalid_input", subject=raw, detail=str(exc)),),
        ) from exc


def _parse_optional_scope_name_for_method(
    method: str,
    raw: str | None,
    *,
    allow_ground: bool = True,
) -> str | None:
    try:
        return parse_optional_scope_name(raw, allow_ground=allow_ground)
    except ParseError as exc:
        raise AppCommandBlocked(
            command=method,
            blockers=(AppBlocker(kind="invalid_input", subject="" if raw is None else raw, detail=str(exc)),),
        ) from exc


def _overlay_change_envelope(change: WorkspaceChange) -> JsonObject:
    path = change[0]
    content = change[1]
    envelope: JsonObject = {"path": path, "type": "delete" if content is None else "modify"}
    if content is not None and len(change) > 2:
        envelope["mode"] = change[2]
    return envelope


def _optional_str(params: JsonObject, key: str, default: str | None = None) -> str | None:
    value = params.get(key, default)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"Expected string parameter '{key}'.")


def _optional_bool(params: JsonObject, key: str, default: bool) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"Expected boolean parameter '{key}'.")


@overload
def _optional_int(params: JsonObject, key: str, default: int) -> int: ...


@overload
def _optional_int(params: JsonObject, key: str, default: None) -> int | None: ...


def _optional_int(params: JsonObject, key: str, default: int | None) -> int | None:
    value = params.get(key, default)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"Expected integer parameter '{key}'.")


def _optional_float(params: JsonObject, key: str, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"Expected number parameter '{key}'.")


def _optional_string_list(params: JsonObject, key: str) -> list[str] | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError(f"Expected string-list parameter '{key}'.")


def _find_open_operation(store: Store, operation_id: str) -> Any:
    for operation in store.list_open_operations():
        if operation.durable_id == operation_id and operation.kind == "vcs_core.session_exec":
            return operation
    raise ValueError(f"No open session exec envelope matches operation id {operation_id!r}.")
