"""Helpers for operation-oriented CLI queries and rendering."""

from __future__ import annotations

import sys
from dataclasses import dataclass

import click

from vcs_core import _cli_delegation
from vcs_core._cli_errors import exit_app_error


def _exit_app_error(exc: Exception) -> None:
    exit_app_error(exc)


@dataclass(frozen=True)
class _OperationSummaryView:
    operation_id: str
    label: str | None
    world_name: str
    world_id: str | None
    visibility: str
    status: str
    parent_operation_id: str | None
    final_phase: str | None
    kind: str
    effect_count_display: str
    carrier_ref: str
    anchor_oid: str | None
    archived_origin: str | None

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        return self.operation_id

    @property
    def world_display(self) -> str:
        if self.world_id:
            if self.world_name and self.world_name != self.world_id:
                return f"{self.world_id} ({self.world_name})"
            return self.world_id
        return self.world_name


@dataclass(frozen=True)
class _OperationCommitView:
    oid: str
    effect_type: str
    message: str
    metadata: object


@dataclass(frozen=True)
class _OperationsResultView:
    requested_mode: str
    visible: tuple[_OperationSummaryView, ...]
    open_operations: tuple[_OperationSummaryView, ...]
    archived: tuple[_OperationSummaryView, ...]


@dataclass(frozen=True)
class _OperationHistoryView:
    summary: _OperationSummaryView
    commits: tuple[_OperationCommitView, ...]
    capture_shadow: object | None = None


@dataclass(frozen=True)
class _RecoverySnapshotView:
    orphaned_scope_refs: tuple[str, ...]
    open_operations: tuple[_OperationSummaryView, ...]
    archived_recovery_operations: tuple[_OperationSummaryView, ...]
    orphaned_operations: tuple[_OperationSummaryView, ...]
    workspace_authority_pending: tuple[str, ...]


def summary_identity(summary: object) -> str:
    return _decode_operation_summary(summary).operation_id


def run_operations(
    *,
    scope_name: str | None,
    show_open: bool,
    show_archived: bool,
    show_all: bool,
    max_count: int,
) -> None:
    """Run the `operations` CLI flow with session-aware delegation."""
    selected_modes = [flag for flag in (show_open, show_archived, show_all) if flag]
    if len(selected_modes) > 1:
        click.echo("Error: choose at most one of --open, --archived, or --all.")
        sys.exit(1)

    mode = "all" if show_all else "open" if show_open else "archived" if show_archived else "visible"
    params: dict[str, object] = {"mode": mode, "max_count": max_count}
    if scope_name is not None:
        params["scope"] = scope_name

    def _fallback() -> None:
        from vcs_core._app import AppOpenMode, VcsCoreApp
        from vcs_core._errors import InvalidRepositoryStateError

        try:
            app_context = VcsCoreApp.open_existing(".", mode=AppOpenMode.CONTROL)
            with app_context as app:
                default_scope = app.resolve_scope(scope_name if scope_name is not None else "ground")
                archived_scope = app.resolve_scope(scope_name) if scope_name is not None else None
                mg = app.mg
                if mode == "visible":
                    try:
                        operations = mg.visible_operations(ref=default_scope.ref, max_count=max_count)
                    except InvalidRepositoryStateError as exc:
                        click.echo(f"Error: {exc}")
                        sys.exit(1)
                    render_operations_result(
                        {
                            "requested_mode": mode,
                            "scope": scope_name,
                            "visible": operations,
                            "open": [],
                            "archived": [],
                        }
                    )
                    return
                if mode == "open":
                    render_operations_result(
                        {
                            "requested_mode": mode,
                            "scope": scope_name,
                            "visible": [],
                            "open": mg.open_operations(scope=default_scope),
                            "archived": [],
                        }
                    )
                    return
                if mode == "archived":
                    render_operations_result(
                        {
                            "requested_mode": mode,
                            "scope": scope_name,
                            "visible": [],
                            "open": [],
                            "archived": mg.archived_operations(
                                max_count=max_count,
                                world_id=None if archived_scope is None else archived_scope.world_id,
                            ),
                        }
                    )
                    return
                try:
                    visible_operations = mg.visible_operations(ref=default_scope.ref, max_count=max_count)
                except InvalidRepositoryStateError as exc:
                    click.echo(f"Error: {exc}")
                    sys.exit(1)
                render_operations_result(
                    {
                        "requested_mode": mode,
                        "scope": scope_name,
                        "visible": visible_operations,
                        "open": mg.open_operations(scope=default_scope),
                        "archived": mg.archived_operations(
                            max_count=max_count,
                            world_id=None if archived_scope is None else archived_scope.world_id,
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            _exit_app_error(exc)

    _cli_delegation.with_session_result(
        "operations",
        params,
        on_result=render_operations_result,
        on_fallback=_fallback,
    )


def run_operation_show(*, selector: str, scope_name: str | None) -> None:
    """Run the `operation show` CLI flow with session-aware delegation."""
    params: dict[str, object] = {"selector": selector}
    if scope_name is not None:
        params["scope"] = scope_name

    def _fallback() -> None:
        from vcs_core._app import AppOpenMode, VcsCoreApp
        from vcs_core._errors import InvalidRepositoryStateError

        try:
            with VcsCoreApp.open_existing(".", mode=AppOpenMode.CONTROL) as app:
                scope = app.resolve_scope(scope_name) if scope_name is not None else None
                try:
                    history = app.mg.resolve_operation_history(selector, scope=scope)
                except (InvalidRepositoryStateError, ValueError) as exc:
                    click.echo(f"Error: {exc}")
                    sys.exit(1)
                render_operation_history(
                    {
                        "requested_selector": selector,
                        "scope": scope_name,
                        "summary": history.summary,
                        "commits": history.commits,
                        "capture_shadow": _capture_shadow_status(app.mg, history),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            _exit_app_error(exc)

    _cli_delegation.with_session_result(
        "operation_history",
        params,
        on_result=render_operation_history,
        on_fallback=_fallback,
    )


def run_recovery(*, max_count: int) -> None:
    """Run the `recovery` CLI flow with session-aware delegation."""

    def _fallback() -> None:
        from vcs_core._app import AppOpenMode, VcsCoreApp

        try:
            with VcsCoreApp.open_existing(".", mode=AppOpenMode.RECOVERY) as app:
                snapshot = app.mg.recovery_snapshot(archived_max_count=max_count)
                render_recovery_snapshot(
                    {
                        "orphaned_scope_refs": snapshot.orphaned_scope_refs,
                        "open_operations": snapshot.open_operations,
                        "archived_recovery_operations": snapshot.archived_recovery_operations,
                        "orphaned_operations": snapshot.orphaned_operations,
                        "workspace_authority_pending": snapshot.workspace_authority_pending,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            _exit_app_error(exc)

    _cli_delegation.with_session_result(
        "recovery",
        {"max_count": max_count},
        on_result=render_recovery_snapshot,
        on_fallback=_fallback,
    )


def render_operations_result(result: object) -> None:
    view = _decode_operations_result(result)

    if view.requested_mode == "all":
        _render_operation_list(view.visible, title="Visible operations")
        _render_operation_list(view.open_operations, title="Open operations")
        _render_operation_list(view.archived, title="Archived operations")
        return
    if view.requested_mode == "open":
        _render_operation_list(view.open_operations)
        return
    if view.requested_mode == "archived":
        _render_operation_list(view.archived)
        return
    _render_operation_list(view.visible)


def render_operation_history(history: object) -> None:
    view = _decode_operation_history(history)
    click.echo(f"Operation:    {view.summary.operation_id}")
    click.echo(f"Label:        {view.summary.display_name}")
    click.echo(f"World:        {view.summary.world_name}")
    if view.summary.world_id:
        click.echo(f"World ID:     {view.summary.world_id}")
    click.echo(f"Visibility:   {view.summary.visibility}")
    click.echo(f"Status:       {view.summary.status}")
    for line in _operation_command_lines(view.commits):
        click.echo(line)
    for line in _capture_shadow_lines(view.capture_shadow):
        click.echo(line)
    if view.summary.parent_operation_id:
        click.echo(f"Parent:       {view.summary.parent_operation_id}")
    if view.summary.final_phase:
        click.echo(f"Phase:        {view.summary.final_phase}")
    click.echo(f"Kind:         {view.summary.kind}")
    click.echo(f"Effects:      {view.summary.effect_count_display}")
    if view.summary.archived_origin is not None:
        click.echo(f"Archived via: {view.summary.archived_origin}")
    click.echo(f"Carrier:      {view.summary.carrier_ref}")
    if view.summary.anchor_oid:
        click.echo(f"Anchor:       {view.summary.anchor_oid}")
    click.echo("Commits:")
    for commit in view.commits:
        summary_line = commit.message.splitlines()[0] if commit.message else ""
        click.echo(f"  {commit.oid[:8]}  {commit.effect_type:20s}  {summary_line}")


def _capture_shadow_status(mg: object, history: object) -> dict[str, object] | None:
    from vcs_core._capture_shadow_status import capture_shadow_status_for_history

    repo_path = getattr(mg, "_repo_path", None)
    if not isinstance(repo_path, str):
        return None
    return capture_shadow_status_for_history(repo_path, history)  # type: ignore[arg-type]


def render_recovery_snapshot(snapshot: object) -> None:
    view = _decode_recovery_snapshot(snapshot)

    if (
        not view.orphaned_scope_refs
        and not view.open_operations
        and not view.archived_recovery_operations
        and not view.orphaned_operations
        and not view.workspace_authority_pending
    ):
        click.echo("No recovery state.")
        return

    click.echo("Recovery:")
    click.echo("  Orphaned scopes:")
    if view.orphaned_scope_refs:
        for ref in view.orphaned_scope_refs:
            click.echo(f"    {ref.rsplit('/', 1)[-1]}")
    else:
        click.echo("    (none)")
    _render_operation_list(view.open_operations, title="  Open operations")
    _render_operation_list(view.archived_recovery_operations, title="  Archived recovery operations")
    _render_operation_list(view.orphaned_operations, title="  Orphaned operations")
    click.echo("  Pending workspace authority:")
    if view.workspace_authority_pending:
        for operation_id in view.workspace_authority_pending:
            click.echo(f"    {operation_id}")
    else:
        click.echo("    (none)")


def _decode_operations_result(result: object) -> _OperationsResultView:
    return _OperationsResultView(
        requested_mode=_string_field(result, "requested_mode", default="visible"),
        visible=_summary_views(result, "visible"),
        open_operations=_summary_views(result, "open"),
        archived=_summary_views(result, "archived"),
    )


def _decode_operation_history(history: object) -> _OperationHistoryView:
    return _OperationHistoryView(
        summary=_decode_operation_summary(_raw_field(history, "summary")),
        commits=tuple(_decode_operation_commit(commit) for commit in _sequence_field(history, "commits")),
        capture_shadow=_raw_field(history, "capture_shadow"),
    )


def _decode_recovery_snapshot(snapshot: object) -> _RecoverySnapshotView:
    return _RecoverySnapshotView(
        orphaned_scope_refs=tuple(str(ref) for ref in _sequence_field(snapshot, "orphaned_scope_refs")),
        open_operations=_summary_views(snapshot, "open_operations"),
        archived_recovery_operations=_summary_views(snapshot, "archived_recovery_operations"),
        orphaned_operations=_summary_views(snapshot, "orphaned_operations"),
        workspace_authority_pending=tuple(
            str(item) for item in _sequence_field(snapshot, "workspace_authority_pending")
        ),
    )


def _decode_operation_summary(summary: object) -> _OperationSummaryView:
    archived_via = _optional_string_field(summary, "archived_via")
    archived_origin = None
    if archived_via == "operation_ref":
        archived_origin = "archived operation ref"
    elif archived_via == "discarded_world_ref":
        archived_origin = "discarded world"
    return _OperationSummaryView(
        operation_id=_string_field(summary, "operation_id", default=""),
        label=_optional_string_field(summary, "label"),
        world_name=_string_field(summary, "world_name", default=""),
        world_id=_optional_string_field(summary, "world_id"),
        visibility=_string_field(summary, "visibility", default=""),
        status=_string_field(summary, "status", default=""),
        parent_operation_id=_optional_string_field(summary, "parent_operation_id"),
        final_phase=_optional_string_field(summary, "final_phase"),
        kind=_string_field(summary, "kind", default=""),
        effect_count_display=_display_field(summary, "effect_count"),
        carrier_ref=_string_field(summary, "carrier_ref", default=""),
        anchor_oid=_optional_string_field(summary, "anchor_oid"),
        archived_origin=archived_origin,
    )


def _decode_operation_commit(commit: object) -> _OperationCommitView:
    metadata = _raw_field(commit, "metadata")
    effect_type = "?"
    if isinstance(metadata, dict):
        raw_effect_type = metadata.get("type")
        if isinstance(raw_effect_type, str):
            effect_type = raw_effect_type
    return _OperationCommitView(
        oid=_string_field(commit, "oid", default=""),
        effect_type=effect_type,
        message=_string_field(commit, "message", default=""),
        metadata=metadata,
    )


def _summary_views(value: object, field: str) -> tuple[_OperationSummaryView, ...]:
    return tuple(_decode_operation_summary(summary) for summary in _sequence_field(value, field))


def _raw_field(value: object, field: str) -> object:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _sequence_field(value: object, field: str) -> tuple[object, ...]:
    raw = _raw_field(value, field)
    if isinstance(raw, list):
        return tuple(raw)
    if isinstance(raw, tuple):
        return raw
    return ()


def _string_field(value: object, field: str, *, default: str) -> str:
    raw = _raw_field(value, field)
    return raw if isinstance(raw, str) else default


def _optional_string_field(value: object, field: str) -> str | None:
    raw = _raw_field(value, field)
    return raw if isinstance(raw, str) and raw else None


def _display_field(value: object, field: str) -> str:
    raw = _raw_field(value, field)
    return "" if raw is None else str(raw)


def _render_operation_list(operations: tuple[_OperationSummaryView, ...], *, title: str | None = None) -> None:
    if title is not None:
        click.echo(f"{title}:")
    if not operations:
        click.echo("  (none)" if title is not None else "No operations.")
        return
    prefix = "  " if title is not None else ""
    for summary in operations:
        line = (
            f"{prefix}{summary.operation_id}  "
            f"[{summary.visibility}/{summary.status}]  "
            f"{summary.display_name}  "
            f"{summary.kind}  "
            f"effects:{summary.effect_count_display}  "
            f"world:{summary.world_display}"
        )
        if summary.archived_origin is not None:
            line = f"{line}  archived via: {summary.archived_origin}"
        click.echo(line)


def _operation_command_lines(commits: tuple[_OperationCommitView, ...]) -> tuple[str, ...]:
    started: dict[str, object] | None = None
    completed: dict[str, object] | None = None
    for commit in commits:
        if not isinstance(commit.metadata, dict):
            continue
        command = commit.metadata.get("command")
        if not isinstance(command, dict):
            continue
        effect_type = commit.metadata.get("type")
        if effect_type == "OperationStarted":
            started = command
        elif effect_type in {"OperationCompleted", "OperationAborted"}:
            completed = command
    if started is None and completed is None:
        return ()

    lines: list[str] = []
    if started is not None:
        transport = started.get("transport")
        submitted_text = started.get("submitted_text")
        if transport == "shell" and isinstance(submitted_text, str) and submitted_text:
            lines.append(f"Shell Command: {submitted_text}")
        argv = started.get("argv")
        if transport != "shell" and isinstance(argv, list) and all(isinstance(item, str) for item in argv):
            lines.append(f"Command:      {' '.join(argv)}")
        cwd = started.get("cwd")
        if isinstance(cwd, str) and cwd:
            lines.append(f"CWD:          {cwd}")
        capture_requested = started.get("capture_requested")
        if isinstance(capture_requested, bool):
            lines.append(f"Capture:      {str(capture_requested).lower()}")
    if completed is not None:
        status = completed.get("status")
        if isinstance(status, str) and status:
            lines.append(f"Cmd Status:   {status}")
        capture_status = completed.get("capture_status")
        if isinstance(capture_status, str) and capture_status:
            lines.append(f"Capture State: {capture_status}")
        capture_stream_status = completed.get("capture_stream_status")
        if isinstance(capture_stream_status, str) and capture_stream_status:
            lines.append(f"Capture Stream: {capture_stream_status}")
        exit_code = completed.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            lines.append(f"Exit Code:    {exit_code}")
        signal = completed.get("signal")
        if isinstance(signal, int) and not isinstance(signal, bool):
            lines.append(f"Signal:       {signal}")
        duration = completed.get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            lines.append(f"Duration:     {float(duration):.3f}s")
        launch_error = completed.get("launch_error")
        if isinstance(launch_error, str) and launch_error:
            lines.append(f"Launch Error: {launch_error}")
        abandoned_reason = completed.get("abandoned_reason")
        if isinstance(abandoned_reason, str) and abandoned_reason:
            lines.append(f"Abandoned:    {abandoned_reason}")
        capture_incomplete_reason = completed.get("capture_incomplete_reason")
        if isinstance(capture_incomplete_reason, str) and capture_incomplete_reason:
            lines.append(f"Capture Note: {capture_incomplete_reason}")
    return tuple(lines)


def _capture_shadow_lines(status: object) -> tuple[str, ...]:
    if not isinstance(status, dict):
        return ()
    state = status.get("state")
    if not isinstance(state, str) or not state:
        return ()
    lines = [f"Capture Shadow: {state}"]
    manifest_digest = status.get("manifest_digest")
    if isinstance(manifest_digest, str) and manifest_digest:
        lines.append(f"Shadow Manifest: {manifest_digest}")
    candidate_head = status.get("candidate_head")
    if isinstance(candidate_head, str) and candidate_head:
        lines.append(f"Shadow Candidate: {candidate_head}")
    raw_count = status.get("raw_evidence_count")
    proof_count = status.get("proof_evidence_count")
    if isinstance(raw_count, int) and not isinstance(raw_count, bool):
        lines.append(f"Shadow Raw Evidence: {raw_count}")
    if isinstance(proof_count, int) and not isinstance(proof_count, bool):
        lines.append(f"Shadow Proof Evidence: {proof_count}")
    validation_error = status.get("validation_error")
    if isinstance(validation_error, str) and validation_error:
        lines.append(f"Shadow Error: {validation_error}")
    return tuple(lines)
