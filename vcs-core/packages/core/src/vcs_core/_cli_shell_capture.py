"""Captured interactive shell runtime helpers."""

from __future__ import annotations

import contextlib
import shlex
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import click

from vcs_core._cli_session_runtime import (
    PreparedSessionContext,
    SessionCliError,
    begin_shell_capture_lease,
    finish_shell_capture_lease,
    new_shell_capture_lease_id,
)


def run_captured_session_shell(shell: str, context: PreparedSessionContext) -> subprocess.CompletedProcess[bytes]:
    with tempfile.TemporaryDirectory(prefix="vcs-core-shell-capture-") as raw_runtime_dir:
        runtime_dir = Path(raw_runtime_dir)
        helper_path = runtime_dir / "shell_capture_helper.py"
        rcfile_path = runtime_dir / "bashrc"
        finish_path = runtime_dir / "finish-marker"
        lease_ready_path = runtime_dir / "lease-ready"
        shell_lease_id = new_shell_capture_lease_id()
        helper_path.write_text(
            shell_capture_helper_source(
                context.active_scope,
                context.session_socket_path,
                context.daemon_instance_id,
                shell_lease_id,
            ),
            encoding="utf-8",
        )
        helper_path.chmod(0o700)
        rcfile_path.write_text(
            shell_capture_bashrc(helper_path=helper_path, finish_path=finish_path),
            encoding="utf-8",
        )
        env = dict(context.env)
        env["VCS_CORE_SHELL_FINISH_PATH"] = str(finish_path)
        env["VCS_CORE_SHELL_LEASE_ID"] = shell_lease_id
        env["VCS_CORE_SHELL_LEASE_READY_PATH"] = str(lease_ready_path)
        argv = [shell, "--noprofile", "--rcfile", str(rcfile_path), "-i"]
        process = subprocess.Popen(
            argv,
            cwd=context.mount_path,
            env=env,
        )
        lease_acquired = False
        try:
            begin_shell_capture_lease(
                lease_id=shell_lease_id,
                scope_name=context.active_scope,
                shell_pid=process.pid,
                socket_path=context.session_socket_path,
                daemon_instance_id=context.daemon_instance_id,
                exit_code=1,
            )
            lease_acquired = True
            lease_ready_path.write_text("1\n", encoding="utf-8")
            return_code = process.wait()
            result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(argv, return_code)
            try:
                finish_shell_capture_lease(
                    lease_id=shell_lease_id,
                    return_code=return_code,
                    socket_path=context.session_socket_path,
                    daemon_instance_id=context.daemon_instance_id,
                )
            except SessionCliError as exc:
                click.echo(f"Warning: failed to release shell capture lease: {exc}", err=True)
            return result
        except BaseException:
            if not lease_ready_path.exists():
                lease_ready_path.write_text("1\n", encoding="utf-8")
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if lease_acquired:
                with contextlib.suppress(SessionCliError):
                    finish_shell_capture_lease(
                        lease_id=shell_lease_id,
                        return_code=process.returncode if process.returncode is not None else 1,
                        socket_path=context.session_socket_path,
                        daemon_instance_id=context.daemon_instance_id,
                    )
            raise


def shell_capture_helper_source(
    scope_name: str,
    session_socket_path: str,
    daemon_instance_id: str | None,
    shell_lease_id: str,
) -> str:
    return textwrap.dedent(
        f"""\
        #!{sys.executable}
        from __future__ import annotations

        import sys

        from vcs_core._cli_session_runtime import (
            SessionCliError,
            begin_shell_command_envelope,
            finish_exec_envelope,
        )

        SCOPE_NAME = {scope_name!r}
        SESSION_SOCKET_PATH = {session_socket_path!r}
        DAEMON_INSTANCE_ID = {daemon_instance_id!r}
        SHELL_LEASE_ID = {shell_lease_id!r}


        def _print_env(operation_id: str, env: dict[str, str]) -> None:
            rendered = dict(env)
            rendered.setdefault("VCS_CORE_COMMAND_OPERATION_ID", operation_id)
            for key in (
                "VCS_CORE_COMMAND_OPERATION_ID",
                "VCS_CORE_CAPTURE_EPOCH",
                "VCS_CORE_CAPTURE_ACTIVE",
            ):
                value = rendered.get(key)
                if value is not None:
                    print(f"{{key}}={{value}}")


        def main(argv: list[str]) -> int:
            try:
                action = argv[1]
                if action == "begin":
                    cwd = argv[2]
                    label = argv[3]
                    shell_pid = int(argv[4])
                    envelope = begin_shell_command_envelope(
                        command_text=label,
                        cwd=cwd,
                        scope_name=SCOPE_NAME,
                        shell_pid=shell_pid,
                        shell_lease_id=SHELL_LEASE_ID,
                        socket_path=SESSION_SOCKET_PATH,
                        daemon_instance_id=DAEMON_INSTANCE_ID,
                        exit_code=3,
                    )
                    _print_env(envelope.operation_id, envelope.env)
                    return 0
                if action == "outcome":
                    operation_id = argv[2]
                    status = int(argv[3])
                    finish_exec_envelope(
                        operation_id=operation_id,
                        outcome="success" if status == 0 else "failed_exit",
                        exit_code_value=status,
                        socket_path=SESSION_SOCKET_PATH,
                        daemon_instance_id=DAEMON_INSTANCE_ID,
                    )
                    return 0
            except (IndexError, ValueError, SessionCliError) as exc:
                print(f"vcs-core shell capture helper error: {{exc}}", file=sys.stderr)
                return 1
            action = argv[1] if len(argv) > 1 else ""
            print(f"vcs-core shell capture helper error: unknown action {{action!r}}", file=sys.stderr)
            return 1


        if __name__ == "__main__":
            raise SystemExit(main(sys.argv))
        """
    )


def shell_capture_bashrc(*, helper_path: Path, finish_path: Path) -> str:
    quoted_helper = shlex.quote(str(helper_path))
    quoted_finish = shlex.quote(str(finish_path))
    return textwrap.dedent(
        f"""\
        __mg_wait_for_shell_lease() {{
            if [[ -z "${{VCS_CORE_SHELL_LEASE_READY_PATH:-}}" ]]; then
                return 0
            fi
            local __mg_wait_i
            for __mg_wait_i in {{1..500}}; do
                if [[ -e "$VCS_CORE_SHELL_LEASE_READY_PATH" ]]; then
                    return 0
                fi
                sleep 0.01
            done
            printf 'vcs-core shell capture helper error: lease was not acquired\\n' >&2
            exit 125
        }}
        __mg_wait_for_shell_lease
        unset -f __mg_wait_for_shell_lease

        set -o history
        HISTFILE=/dev/null
        __mg_capture_helper={quoted_helper}
        __mg_finish_path={quoted_finish}
        __mg_active=0
        __mg_suppress=1
        __mg_command_id=''

        __mg_history_label() {{
          history 1 2>/dev/null | sed 's/^ *[0-9][0-9]*[ *]*//'
        }}

        __mg_clear_capture_env() {{
          unset VCS_CORE_COMMAND_OPERATION_ID VCS_CORE_CAPTURE_EPOCH VCS_CORE_CAPTURE_ACTIVE
          __mg_command_id=''
          __mg_active=0
        }}

        __mg_begin() {{
          local prior_status=$?
          if [ "${{__mg_suppress:-0}}" = "1" ] || [ "${{__mg_active:-0}}" = "1" ]; then
            return "$prior_status"
          fi
          case "$BASH_COMMAND" in
            __mg_*|trap\\ *|PROMPT_COMMAND=*|HISTFILE=*|set\\ -o\\ history)
              return "$prior_status"
              ;;
          esac
          __mg_clear_capture_env
          __mg_suppress=1
          local label
          label="$(__mg_history_label)"
          if [ -z "$label" ]; then
            label="$BASH_COMMAND"
          fi
          local env_output
          if env_output="$(VCS_CORE_HOOK_SUPPRESS=1 VCS_CORE_FS_CAPTURE_SUPPRESS=1 "$__mg_capture_helper" begin "$PWD" "$label" "$$")"; then
            while IFS='=' read -r key value; do
              case "$key" in
                VCS_CORE_COMMAND_OPERATION_ID|VCS_CORE_CAPTURE_EPOCH|VCS_CORE_CAPTURE_ACTIVE)
                  export "$key=$value"
                  ;;
              esac
            done <<< "$env_output"
            __mg_command_id="$VCS_CORE_COMMAND_OPERATION_ID"
            __mg_active=1
          else
            if [ -n "$env_output" ]; then
              printf '%s\\n' "$env_output" >&2
            fi
            printf 'vcs-core shell capture helper error: begin failed for %s\\n' "$label" >&2
          fi
          __mg_suppress=0
          return "$prior_status"
        }}

        __mg_finish() {{
          local status="$1"
          if [ "${{__mg_active:-0}}" = "1" ]; then
            __mg_suppress=1
            local command_id="$__mg_command_id"
            local helper_status=0
            export VCS_CORE_SHELL_FINISH_PATH="$__mg_finish_path"
            export VCS_CORE_SHELL_FINISH_ACTIVE=1
            : > "$__mg_finish_path"
            unset VCS_CORE_SHELL_FINISH_ACTIVE
            VCS_CORE_HOOK_SUPPRESS=1 VCS_CORE_FS_CAPTURE_SUPPRESS=1 "$__mg_capture_helper" outcome "$command_id" "$status" || helper_status=$?
            __mg_clear_capture_env
            if [ "$helper_status" -ne 0 ]; then
              printf 'vcs-core shell capture helper error: outcome failed for %s\\n' "$command_id" >&2
            fi
            __mg_suppress=0
          fi
          return "$status"
        }}

        __mg_prompt() {{
          local status=$?
          __mg_finish "$status"
          return "$status"
        }}

        __mg_exit() {{
          local status=$?
          __mg_finish "$status"
          return "$status"
        }}

        trap '__mg_exit' EXIT
        trap '__mg_begin' DEBUG
        PROMPT_COMMAND='__mg_prompt'
        __mg_suppress=0
        """
    )
