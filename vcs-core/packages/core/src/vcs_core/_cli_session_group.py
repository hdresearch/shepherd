"""Session-oriented CLI commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import click

from vcs_core import _cli_ipc
from vcs_core._cli_errors import emit_error_message
from vcs_core._cli_session_runtime import (
    AUTO_CAPTURE_DEBUG_SENTINEL,
    SessionCliError,
    begin_exec_envelope,
    finish_exec_envelope,
    load_live_session_status,
    prepare_session_context,
    resolve_debug_log_path,
    resolve_exec_cwd,
    run_managed_exec,
)
from vcs_core._cli_shell_capture import (
    run_captured_session_shell as _run_captured_session_shell,
)
from vcs_core._cli_shell_capture import (
    shell_capture_bashrc,
)
from vcs_core._cli_workspace_boundary import environment_boundary_line, managed_workspace_line

if TYPE_CHECKING:
    from pathlib import Path


def _exit_session_command_error(exc: SessionCliError) -> None:
    emit_error_message(str(exc), err=True)
    sys.exit(exc.exit_code)


@click.group("session")
def session_group() -> None:
    """Manage persistent overlay sessions."""


@session_group.command("start")
@click.option("--foreground", is_flag=True, help="Run in foreground (don't daemonize)")
def session_start(foreground: bool) -> None:
    """Start a persistent session with overlay sandbox.

    The session daemon mounts the overlay, holds it open, and listens
    for IPC requests from other CLI commands. Use `vcs-core session shell`
    to open a subshell in the overlay working directory.
    """
    from vcs_core._session import daemonize

    try:
        pid = daemonize(".", foreground=foreground)
    except RuntimeError as exc:
        click.echo(f"Error: {exc}")
        sys.exit(1)

    if not foreground:
        try:
            resp = _cli_ipc.try_session_ipc("get_state")
        except _cli_ipc.SessionIpcError as exc:
            click.echo(f"Error: {exc}")
            sys.exit(1)
        result = _cli_ipc.response_result(resp)
        mount_path = result.get("mount_path", "(unknown)")

        click.echo(f"Session started (PID {pid})")
        click.echo(f"Working directory: {mount_path}")
        click.echo()
        click.echo("Next steps:")
        click.echo("  vcs-core session shell --scope task --create")
        click.echo("                              # create an isolated scope and open a shell there")
        click.echo("  vcs-core session status     # check session state")
        click.echo("  vcs-core session stop       # stop the session")


@session_group.command("stop")
def session_stop() -> None:
    """Stop the running session daemon."""
    from vcs_core._session import stop_session

    try:
        stop_session(".")
        click.echo("Session stopped.")
    except RuntimeError as exc:
        click.echo(f"Error: {exc}")
        sys.exit(1)


def _shell_capture_bashrc(*, helper_path: Path, finish_path: Path) -> str:
    return shell_capture_bashrc(helper_path=helper_path, finish_path=finish_path)


@session_group.command("shell")
@click.option("--scope", "scope_name", default=None, help="Switch to this scope before opening the shell")
@click.option("--create", is_flag=True, help="Create --scope as a new isolated scope before opening the shell")
@click.option("--parent", default=None, help="Parent scope when used with --create (default: ground)")
@click.option("--capture", is_flag=True, help="Enable Bash filesystem capture on Linux")
def session_shell(scope_name: str | None, create: bool, parent: str | None, capture: bool) -> None:
    """Open a subshell in the session's overlay working directory.

    Workspace file changes under the overlay are captured by the overlay.
    Use `vcs-core log --graph` from within to see the effect history.
    Exit the subshell to return to the real workspace.
    """
    if capture and sys.platform != "linux":
        click.echo("Error: `vcs-core session shell --capture` is Linux-only; use `vcs-core session exec --capture`.")
        sys.exit(1)

    try:
        context = prepare_session_context(
            scope_name=scope_name,
            create=create,
            parent=parent,
            capture=capture,
            usage_error_exit_code=1,
            env_error_exit_code=1,
        )
    except SessionCliError as exc:
        _exit_session_command_error(exc)

    shell = shutil.which("bash") if capture else os.environ.get("SHELL", "/bin/bash")
    if capture and shell is None:
        click.echo("Error: `vcs-core session shell --capture` requires Bash on PATH.", err=True)
        sys.exit(1)
    if shell is None:
        shell = "/bin/bash"

    click.echo(f"Entering session shell for scope '{context.active_scope}' at {context.mount_path}")
    if capture:
        click.echo("  (Bash filesystem capture is enabled; exit shell to return)")
    else:
        click.echo("  (workspace file changes under the overlay are sandboxed; exit shell to return)")
    click.echo()

    try:
        if capture:
            result = _run_captured_session_shell(shell, context)
        else:
            result = subprocess.run([shell], cwd=context.mount_path, env=context.env, check=False)
    except SessionCliError as exc:
        _exit_session_command_error(exc)
    if context.active_scope != "ground":
        click.echo()
        click.echo(f"Scope '{context.active_scope}' remains open.")
        click.echo("  vcs-core session status")
        click.echo(f"  vcs-core merge {context.active_scope}")
        click.echo(f"  vcs-core discard {context.active_scope}")
    sys.exit(result.returncode)


@session_group.command("exec")
@click.option("--scope", "scope_name", default=None, help="Switch to this scope before running the command")
@click.option("--create", is_flag=True, help="Create --scope as a new isolated scope before running the command")
@click.option("--parent", default=None, help="Parent scope when used with --create (default: ground)")
@click.option("--capture", is_flag=True, help="Enable LD_PRELOAD-based filesystem capture on Linux")
@click.option(
    "--capture-debug",
    "capture_debug",
    is_flag=False,
    default=None,
    flag_value=AUTO_CAPTURE_DEBUG_SENTINEL,
    help="Set VCS_CORE_FS_CAPTURE_DEBUG_LOG for the child process.",
)
@click.option("--cwd", "cwd_subpath", default=None, help="Run the child command from a subdirectory of the mount")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def session_exec(
    scope_name: str | None,
    create: bool,
    parent: str | None,
    capture: bool,
    capture_debug: str | None,
    cwd_subpath: str | None,
    command: tuple[str, ...],
) -> None:
    """Run one non-interactive command in the session overlay."""
    if not command:
        _exit_session_command_error(
            SessionCliError(
                "`vcs-core session exec` requires a command after `--`.",
                exit_code=2,
            )
        )
    if create and scope_name is None:
        _exit_session_command_error(
            SessionCliError(
                "`vcs-core session exec --create` requires `--scope <name>`.",
                exit_code=2,
            )
        )
    if parent is not None and not create:
        _exit_session_command_error(SessionCliError("`--parent` is only valid together with `--create`.", exit_code=2))

    legacy_exec_requested = os.environ.get("VCS_CORE_TEST_LEGACY_EXEC") == "1"
    if not legacy_exec_requested:
        try:
            result_code = run_managed_exec(
                argv=command,
                scope_name=scope_name,
                create=create,
                parent=parent,
                cwd_subpath=cwd_subpath,
                capture_requested=capture,
                capture_debug=capture_debug,
                env={**os.environ, "VCS_CORE_SESSION": "1"},
                stdout=sys.stdout.buffer,
                stderr=sys.stderr.buffer,
                exit_code=3,
            )
        except SessionCliError as exc:
            _exit_session_command_error(exc)
        sys.exit(result_code)

    if capture:
        _exit_session_command_error(
            SessionCliError(
                "`vcs-core session exec --capture` requires daemon-managed execution; "
                "unset VCS_CORE_TEST_LEGACY_EXEC to use capture.",
                exit_code=2,
            )
        )

    try:
        context = prepare_session_context(
            scope_name=scope_name,
            create=create,
            parent=parent,
            capture=capture,
            usage_error_exit_code=2,
            env_error_exit_code=3,
        )
        cwd = resolve_exec_cwd(context.mount_path, cwd_subpath)
    except SessionCliError as exc:
        _exit_session_command_error(exc)

    env = dict(context.env)
    if capture_debug is not None:
        debug_log_path, announced = resolve_debug_log_path(
            capture_debug,
            context.active_scope,
            context.workspace,
        )
        env["VCS_CORE_FS_CAPTURE_DEBUG_LOG"] = debug_log_path
        if not capture:
            click.echo("Warning: --capture-debug has no effect without --capture.", err=True)
        if announced:
            click.echo(f"Capture debug log: {debug_log_path}", err=True)

    _run_legacy_session_exec(
        command=command,
        cwd=cwd,
        scope_name=context.active_scope,
        capture_requested=capture,
        env=env,
    )


def _run_legacy_session_exec(
    *,
    command: tuple[str, ...],
    cwd: str,
    scope_name: str,
    capture_requested: bool,
    env: dict[str, str],
) -> None:
    import subprocess

    try:
        envelope = begin_exec_envelope(
            argv=command,
            cwd=cwd,
            scope_name=scope_name,
            capture_requested=capture_requested,
            exit_code=3,
        )
    except SessionCliError as exc:
        _exit_session_command_error(exc)
    env.update(envelope.env)

    try:
        result = subprocess.run(list(command), cwd=cwd, env=env, check=False)
    except KeyboardInterrupt:
        try:
            finish_exec_envelope(
                operation_id=envelope.operation_id,
                outcome="interrupted",
                signal_value=2,
            )
        except SessionCliError as exc:
            click.echo(f"Warning: failed to record session exec outcome: {exc}", err=True)
        raise
    except FileNotFoundError:
        try:
            finish_exec_envelope(
                operation_id=envelope.operation_id,
                outcome="launch_error",
                exit_code_value=127,
                launch_error=f"command not found: {command[0]}",
            )
        except SessionCliError as exc:
            click.echo(f"Warning: failed to record session exec outcome: {exc}", err=True)
        click.echo(f"Error: command not found: {command[0]}", err=True)
        sys.exit(127)
    except PermissionError:
        try:
            finish_exec_envelope(
                operation_id=envelope.operation_id,
                outcome="launch_error",
                exit_code_value=126,
                launch_error=f"target not executable: {command[0]}",
            )
        except SessionCliError as exc:
            click.echo(f"Warning: failed to record session exec outcome: {exc}", err=True)
        click.echo(f"Error: target not executable: {command[0]}", err=True)
        sys.exit(126)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        launch_error = f"failed to launch {command[0]}: {detail}"
        try:
            finish_exec_envelope(
                operation_id=envelope.operation_id,
                outcome="launch_error",
                exit_code_value=126,
                launch_error=launch_error,
            )
        except SessionCliError as finish_exc:
            click.echo(f"Warning: failed to record session exec outcome: {finish_exc}", err=True)
        click.echo(f"Error: {launch_error}", err=True)
        sys.exit(126)
    outcome = "success"
    exit_code_value: int | None = result.returncode
    signal_value: int | None = None
    if result.returncode < 0:
        outcome = "signaled"
        signal_value = -result.returncode
        exit_code_value = None
    elif result.returncode != 0:
        outcome = "failed_exit"
    try:
        finish_exec_envelope(
            operation_id=envelope.operation_id,
            outcome=outcome,
            exit_code_value=exit_code_value,
            signal_value=signal_value,
        )
    except SessionCliError as exc:
        click.echo(f"Warning: failed to record session exec outcome: {exc}", err=True)
    sys.exit(result.returncode)


@session_group.command("status")
def session_status() -> None:
    """Show session status."""
    render_live_session_status()


def render_live_session_status() -> None:
    """Render the current live session state using the daemon-backed surface."""
    import time as _time

    try:
        status = load_live_session_status()
    except SessionCliError as exc:
        click.echo(f"Error: {exc}")
        sys.exit(exc.exit_code)

    if status is None:
        click.echo("No session running.")
        return

    uptime = _time.time() - status.started_at
    if uptime < 60:
        uptime_str = f"{uptime:.0f}s"
    elif uptime < 3600:
        uptime_str = f"{uptime / 60:.0f}m"
    else:
        uptime_str = f"{uptime / 3600:.1f}h"

    click.echo(f"Session active (PID {status.pid}, uptime {uptime_str})")
    click.echo(managed_workspace_line(status.workspace, indent="  "))
    click.echo(environment_boundary_line(indent="  "))
    click.echo(f"  Mount path: {status.mount_path}")
    click.echo(f"  Scope:      {status.current_scope}")
    if status.current_world_id:
        click.echo(f"  World ID:   {status.current_world_id}")
    click.echo(f"  Overlay changes: {status.overlay_change_count}")
    click.echo(f"  Local changes:   {status.local_changes}")
    click.echo(f"  Commits ahead:   {status.commits_ahead}")
    click.echo(f"  Live scopes:     {status.live_scope_count}")
    click.echo(f"  Retained scopes: {status.retained_scope_count}")
    if status.pending_operations is not None:
        click.echo(f"  Pending materialization operations: {status.pending_operations}")
    if status.blocker_count:
        click.echo(f"  Blockers:        {status.blocker_count}")


@click.command("switch")
@click.argument("name")
def switch_cmd(name: str) -> None:
    """Switch to a different scope's overlay view.

    Requires a running session. Prints the new working directory path.
    Use `cd <path>` or re-enter `vcs-core session shell` to work there.
    """
    try:
        resp = _cli_ipc.try_session_ipc("switch", {"name": name})
    except (_cli_ipc.SessionIpcError, ValueError) as exc:
        click.echo(f"Error: {exc}")
        sys.exit(1)

    if resp is None:
        click.echo("Error: no session running. Start one with `vcs-core session start`.")
        sys.exit(1)

    if not _cli_ipc.response_ok(resp):
        error = _cli_ipc.response_error(resp)
        click.echo(error if error.startswith("Error: ") else f"Error: {error}")
        sys.exit(1)

    result = _cli_ipc.response_result(resp)
    mount_path = result.get("mount_path", "")
    click.echo(f"Switched to '{name}'.")
    click.echo(f"Working directory: {mount_path}")
