"""Session daemon for persistent overlay sessions.

The daemon holds a VcsCore instance with overlay mounted, listens on a
Unix domain socket for IPC requests, and manages scope lifecycle across
CLI invocations.

Start:  vcs-core session start  (or daemonize() from Python)
Stop:   vcs-core session stop   (or stop_session() from Python)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import stat
import struct
import sys
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from vcs_core._capture_authority import CaptureAuthority, CaptureDrainResult
from vcs_core._hook_frontier import HookEventFrontier
from vcs_core._hooks import HOOK_OUTCOMES, HookManager, HookOutcome, parse_hook_event_line
from vcs_core._ipc import (
    SESSION_LOG,
    JsonObject,
    SessionInfo,
    is_session_alive,
    read_session_info,
    remove_session_info,
    send_request,
    write_session_info,
)
from vcs_core._managed_exec import ManagedExecController
from vcs_core._managed_exec_service import ManagedExecState, ManagedExecutionService
from vcs_core._session_dispatch import SessionCommandDispatcher
from vcs_core._session_exec_envelope import completion_command_metadata
from vcs_core._session_paths import (
    session_hook_socket_path,
    session_runtime_root,
    session_socket_path,
)
from vcs_core._session_transport import read_request, send_response

if TYPE_CHECKING:
    from collections.abc import Iterator

    from vcs_core._app import VcsCoreApp
    from vcs_core.types import ScopeInfo

logger = logging.getLogger(__name__)
_UNIX_SOCKET_PATH_LIMIT = 103


class SessionDaemon:
    """Long-running process that holds an overlay mount and serves IPC requests."""

    def __init__(self, workspace: str) -> None:
        self._workspace = os.path.abspath(workspace)
        self._repo_path = os.path.join(self._workspace, ".vcscore")  # noqa: PTH118
        self._running = False
        self._current_scope_name = "ground"
        self._started_at: float | None = None
        self._mg: Any = None  # VcsCore instance, set during start()
        self._lock = threading.RLock()
        self._runtime_root = session_runtime_root(self._repo_path)
        self._hook_socket_path = session_hook_socket_path(self._repo_path)
        self._hook_accepted_seq = 0
        self._hook_processed_seq = 0
        self._hook_frontier = HookEventFrontier()
        self._hook_persisted_seq = 0
        self._hook_failed_seq = 0
        self._hook_outcomes: dict[HookOutcome, int] = dict.fromkeys(HOOK_OUTCOMES, 0)
        self._hook_manager: HookManager | None = None
        self._capture_authority = CaptureAuthority()
        self._daemon_instance_id = uuid.uuid4().hex
        self._managed_execs: dict[str, ManagedExecState] = {}
        self._managed_execution_service = ManagedExecutionService(self)
        self._managed_exec_controller = ManagedExecController(self)
        self._dispatcher = SessionCommandDispatcher(self)

    def start(self, *, foreground: bool = False) -> None:
        """Start the session daemon.

        If foreground is False (default), forks and detaches from the
        terminal. Otherwise runs in the calling process.
        """
        if not foreground:
            self._daemonize()
            return
        self._run()

    def _daemonize(self) -> None:
        """Fork, detach, redirect stdio, then run."""
        pid = os.fork()
        if pid > 0:
            # Parent: wait briefly for session.json, then exit
            return

        # Child: new session, detach from terminal
        os.setsid()

        # Redirect stdio to session log
        log_path = os.path.join(self._repo_path, SESSION_LOG)  # noqa: PTH118
        log_fd = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
        os.dup2(log_fd, sys.stdout.fileno())
        os.dup2(log_fd, sys.stderr.fileno())
        os.close(log_fd)

        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, sys.stdin.fileno())
        os.close(devnull)

        try:
            self._run()
        except Exception:
            logger.exception("Session daemon crashed")
            sys.exit(1)
        sys.exit(0)

    def _run(self) -> None:
        """Activate VcsCore, bind socket, serve requests."""
        from vcs_core._workspace_external_state import assert_workspace_admissible
        from vcs_core.vcscore import VcsCore

        self._mg = VcsCore.from_config(self._workspace)
        assert_workspace_admissible(self._mg.store, Path(self._workspace))
        self._mg.activate()
        self._recover_abandoned_session_operations()

        socket_path = session_socket_path(self._repo_path)
        self._prepare_runtime_root()
        self._validate_socket_path(socket_path)
        self._validate_socket_path(self._hook_socket_path)
        self._cleanup_stale_socket(socket_path)
        self._cleanup_stale_socket(self._hook_socket_path)

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(socket_path)
        self._secure_socket_path(socket_path)
        srv.listen(4)
        srv.settimeout(1.0)  # allow periodic _running check

        hook_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        hook_srv.bind(self._hook_socket_path)
        self._secure_socket_path(self._hook_socket_path)
        hook_srv.listen(8)
        hook_srv.settimeout(1.0)

        self._hook_manager = HookManager(
            self._mg,
            workspace=Path(self._workspace),
            repo_path=Path(self._repo_path),
            socket_path=self._hook_socket_path,
        )
        self._hook_manager.install_bindings(self._mg.bindings)

        self._started_at = time.time()
        write_session_info(
            self._repo_path,
            SessionInfo(
                pid=os.getpid(),
                socket_path=socket_path,
                mount_path=self._current_scope_mount_path(),
                workspace=self._workspace,
                started_at=self._started_at,
                daemon_instance_id=self._daemon_instance_id,
            ),
        )

        self._running = True
        hook_thread = threading.Thread(target=self._run_hook_server, args=(hook_srv,), daemon=True)
        hook_thread.start()
        logger.info("Session daemon started (PID %d, socket %s)", os.getpid(), socket_path)

        try:
            while self._running:
                try:
                    conn, _ = srv.accept()
                except TimeoutError:
                    continue
                thread = threading.Thread(target=self._handle_connection_thread, args=(conn,), daemon=True)
                thread.start()
        finally:
            self._running = False
            srv.close()
            hook_srv.close()
            hook_thread.join(timeout=1.0)
            self._shutdown_managed_execs()
            self._cleanup(socket_path, self._hook_socket_path)

    def _handle_connection(self, conn: socket.socket) -> None:
        """Read one JSON request, dispatch, write JSON response."""
        self._authorize_peer(conn)
        while True:
            try:
                request = read_request(conn)
            except json.JSONDecodeError as exc:
                send_response(conn, ok=False, error=f"Invalid JSON: {exc}")
                return
            except TypeError as exc:
                send_response(conn, ok=False, error=str(exc))
                return
            if request is None:
                return

            try:
                if request["method"] == "exec_managed":
                    self._handle_managed_exec_connection(conn, request["params"])
                    return
                result = self._dispatch(request["method"], request["params"])
                send_response(conn, ok=True, result=result)
            except Exception as exc:  # noqa: BLE001
                from vcs_core._app import AppError, app_error_message

                error = app_error_message(exc) if isinstance(exc, AppError) else str(exc)
                send_response(conn, ok=False, error=error)
            return

    def _handle_connection_thread(self, conn: socket.socket) -> None:
        with conn:
            try:
                self._handle_connection(conn)
            except Exception:
                logger.exception("Error handling IPC request")

    def _dispatch(self, method: str, params: JsonObject) -> JsonObject:
        return self._dispatcher.dispatch(method, params)

    def _handle_managed_exec_connection(self, conn: socket.socket, params: JsonObject) -> None:
        """Run a noninteractive command under daemon ownership and stream frames."""
        self._managed_exec_controller.handle_connection(conn, params)

    def _signal_managed_exec(self, operation_id: str, signal_value: int) -> None:
        with self._lock:
            state = self._managed_execs.get(operation_id)
        if state is None:
            raise ValueError(f"Managed session exec is not running: {operation_id}")
        os.killpg(state.pgid, signal_value)

    def _shutdown_managed_execs(self, *, timeout_seconds: float = 2.0) -> None:
        with self._lock:
            states = tuple(self._managed_execs.values())
        for state in states:
            with suppress(PermissionError, ProcessLookupError):
                os.killpg(state.pgid, signal.SIGTERM)
        if states:
            time.sleep(0.05)
        for state in states:
            if state.process.poll() is None:
                with suppress(PermissionError, ProcessLookupError):
                    os.killpg(state.pgid, signal.SIGKILL)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                if not self._managed_execs:
                    return
            time.sleep(0.01)

    def _run_hook_server(self, srv: socket.socket) -> None:
        while self._running:
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_hook_connection, args=(conn,), daemon=True)
            thread.start()

    def _handle_hook_connection(self, conn: socket.socket) -> None:
        try:
            self._authorize_peer(conn)
        except PermissionError:
            logger.warning("Rejected hook connection from another uid", exc_info=True)
            return
        buffer = b""
        with conn:
            while self._running:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    if raw_line.strip():
                        self._handle_hook_line(raw_line)

    def _handle_hook_line(self, raw_line: str | bytes) -> None:
        if self._hook_manager is None:
            return
        seq = 0
        try:
            with self._lock:
                seq = self._hook_frontier.accept_next()
                self._hook_accepted_seq = self._hook_frontier.accepted_seq
            event = parse_hook_event_line(raw_line)
            result = self._hook_manager.dispatch(
                event,
                global_seq=seq,
                capture_authority=self._capture_authority,
            )
        except ValueError:
            with self._lock:
                self._hook_outcomes["malformed"] += 1
                if seq:
                    self._hook_frontier.mark_terminal(seq)
                    self._hook_processed_seq = self._hook_frontier.processed_seq
            logger.warning("Dropping malformed hook event: %r", raw_line, exc_info=True)
            return
        except Exception:
            with self._lock:
                self._hook_outcomes["failed"] += 1
                self._hook_failed_seq = max(self._hook_failed_seq, seq)
                self._hook_frontier.mark_terminal(seq)
                self._hook_processed_seq = self._hook_frontier.processed_seq
            logger.exception("Error processing hook event")
            return

        with self._lock:
            self._hook_outcomes[result.outcome] += 1
            self._hook_frontier.mark_terminal(seq)
            self._hook_processed_seq = self._hook_frontier.processed_seq
            if result.outcome == "persisted" and seq > self._hook_persisted_seq:
                self._hook_persisted_seq = seq

    def _wait_for_hook_drain(
        self,
        *,
        min_accepted_seq: int = 0,
        timeout_seconds: float = 1.0,
        quiet_period_seconds: float = 0.05,
    ) -> bool:
        """Wait until hook events through a snapshot have processed, then stay quiet briefly."""
        deadline = time.monotonic() + timeout_seconds
        stable_since: float | None = None
        stable_accepted_seq = -1
        while True:
            with self._lock:
                accepted_seq = self._hook_frontier.accepted_seq
                processed_seq = self._hook_frontier.processed_seq
                self._hook_accepted_seq = accepted_seq
                self._hook_processed_seq = processed_seq
            now = time.monotonic()
            if processed_seq >= min_accepted_seq and accepted_seq == processed_seq:
                if accepted_seq != stable_accepted_seq:
                    stable_accepted_seq = accepted_seq
                    stable_since = now
                elif stable_since is not None and now - stable_since >= quiet_period_seconds:
                    return True
            else:
                stable_since = None
                stable_accepted_seq = -1
            if now >= deadline:
                return False
            time.sleep(min(0.01, max(0.0, deadline - now)))

    def _wait_for_capture_drain(
        self,
        command_operation_id: str,
        *,
        timeout_seconds: float = 1.0,
        quiet_period_seconds: float = 0.05,
    ) -> CaptureDrainResult:
        """Wait until one command's correlated capture events are processed."""
        return self._capture_authority.drain(
            command_operation_id,
            timeout_seconds=timeout_seconds,
            quiet_period_seconds=quiet_period_seconds,
        )

    # --- Helpers ---

    @contextmanager
    def _current_scope_view(self) -> Iterator[tuple[VcsCoreApp, ScopeInfo]]:
        """Resolve the selected UI scope through the app identity boundary."""
        from vcs_core._app import VcsCoreApp

        with VcsCoreApp.active_view(self._mg, current_scope=self._current_scope_name) as app:
            scope = app.resolve_scope(self._current_scope_name)
            app.retain_restored_scope(self._current_scope_name)
            yield app, scope

    def _current_scope_mount_path(self) -> str:
        """Return the current scope mount path from an app-resolved handle."""
        with self._current_scope_view() as (_app, scope):
            return str(self._mg.overlay_mount_path_for_scope(scope))

    def _session_state(self, *, hook_capabilities: list[str] | None = None) -> JsonObject:
        with self._current_scope_view() as (_app, scope):
            mount_path = str(self._mg.overlay_mount_path_for_scope(scope))
        hook_static_env: dict[str, str] = {}
        hook_static_prepend_path: list[str] = []
        hook_static_prepend_env: dict[str, list[str]] = {}
        hook_scope_env: dict[str, str] = {}
        hook_scope_prepend_path: list[str] = []
        hook_scope_prepend_env: dict[str, list[str]] = {}
        if self._hook_manager is not None:
            activation = self._hook_manager.activation(hook_capabilities)
            static_env = self._hook_manager.static_env(activation=activation)
            scope_env = self._hook_manager.scope_env(scope, activation=activation)
            hook_static_env = dict(static_env.env)
            hook_static_prepend_path = list(static_env.prepend_path)
            hook_static_prepend_env = {key: list(values) for key, values in static_env.prepend_env.items()}
            hook_scope_env = dict(scope_env.env)
            hook_scope_prepend_path = list(scope_env.prepend_path)
            hook_scope_prepend_env = {key: list(values) for key, values in scope_env.prepend_env.items()}
        return {
            "pid": os.getpid(),
            "current_scope": self._current_scope_name,
            "current_scope_instance_id": scope.instance_id,
            "current_world_id": scope.world_id,
            "mount_path": mount_path,
            "workspace": self._workspace,
            "started_at": self._started_at if self._started_at is not None else time.time(),
            "daemon_instance_id": self._daemon_instance_id,
            "hook_socket": self._hook_socket_path,
            "hook_static_env": hook_static_env,
            "hook_static_prepend_path": hook_static_prepend_path,
            "hook_static_prepend_env": hook_static_prepend_env,
            "hook_scope_env": hook_scope_env,
            "hook_scope_prepend_path": hook_scope_prepend_path,
            "hook_scope_prepend_env": hook_scope_prepend_env,
        }

    def _recover_abandoned_session_exec_envelopes(self) -> None:
        """Close stale session-exec envelopes left open by a prior daemon."""
        self._recover_abandoned_session_operations(kinds={"vcs_core.session_exec"})

    def _recover_abandoned_session_operations(self, *, kinds: set[str] | None = None) -> None:
        """Close stale daemon-owned shell/exec operations left open by a prior daemon."""
        if self._mg is None:
            return
        target_kinds = kinds or {"vcs_core.session_exec", "vcs_core.session_shell"}
        abandoned_refs: set[str] = set()
        for operation in self._mg.store.list_open_operations():
            if operation.kind not in target_kinds:
                continue
            try:
                ended_at = time.time()
                start_metadata = self._mg.store._read_operation_start_metadata(operation.ref)
                metadata: dict[str, object]
                if operation.kind == "vcs_core.session_shell":
                    metadata = {"shell": self._abandoned_shell_lease_metadata(start_metadata, ended_at=ended_at)}
                else:
                    command_metadata = completion_command_metadata(
                        start_metadata,
                        outcome="abandoned",
                        ended_at=ended_at,
                        exit_code=None,
                        signal=None,
                        launch_error=None,
                        abandoned_reason="session daemon startup recovery",
                    )
                    start_command = start_metadata.get("command")
                    if isinstance(start_command, dict) and start_command.get("capture_requested") is True:
                        command_metadata["capture_status"] = "incomplete"
                        command_metadata["capture_stream_status"] = "incomplete"
                        command_metadata["capture_incomplete_reason"] = "session_daemon_startup_recovery"
                        capture_epoch = start_command.get("capture_epoch")
                        if isinstance(capture_epoch, str) and capture_epoch:
                            command_metadata["capture_epoch"] = capture_epoch
                    metadata = {"command": command_metadata}
                self._mg.store.abort_operation(
                    operation,
                    metadata=metadata,
                    status="error",
                )
                abandoned_refs.add(operation.ref)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to mark abandoned session operation %s", operation.ref, exc_info=True)
        if abandoned_refs:
            self._mg._orphaned_operations = [
                operation for operation in self._mg._orphaned_operations if operation.ref not in abandoned_refs
            ]

    @staticmethod
    def _abandoned_shell_lease_metadata(start_metadata: dict[str, object], *, ended_at: float) -> dict[str, object]:
        start_shell = start_metadata.get("shell")
        shell: dict[str, object] = {
            "status": "abandoned",
            "ended_at": ended_at,
            "abandoned_reason": "session daemon startup recovery",
            "capture_status": "incomplete",
            "capture_stream_status": "incomplete",
            "capture_incomplete_reason": "session_daemon_startup_recovery",
        }
        if isinstance(start_shell, dict):
            for field in (
                "scope",
                "capture_requested",
                "client_pid",
                "shell_pid",
                "daemon_instance_id",
                "started_at",
            ):
                value = start_shell.get(field)
                if value is not None:
                    shell[field] = value
            started_at = start_shell.get("started_at")
            if isinstance(started_at, (int, float)) and not isinstance(started_at, bool):
                shell["duration_seconds"] = max(0.0, ended_at - float(started_at))
        return shell

    def _cleanup(self, socket_path: str, hook_socket_path: str) -> None:
        """Clean up session state on shutdown."""
        try:
            self._mg.deactivate()
            if self._hook_manager is not None:
                self._hook_manager.shutdown()
        except Exception:
            logger.exception("Error during deactivation")
        remove_session_info(self._repo_path)
        with suppress(FileNotFoundError):
            Path(socket_path).unlink()
        with suppress(FileNotFoundError):
            Path(hook_socket_path).unlink()
        with suppress(OSError):
            Path(socket_path).parent.rmdir()
        with suppress(OSError):
            Path(socket_path).parent.parent.rmdir()
        logger.info("Session daemon stopped")

    @staticmethod
    def _cleanup_stale_socket(socket_path: str) -> None:
        """Remove a stale socket file if it exists."""
        if os.path.exists(socket_path):  # noqa: PTH110
            with suppress(OSError):
                Path(socket_path).unlink()

    def _prepare_runtime_root(self) -> None:
        """Create owner-only runtime directories before binding privileged IPC sockets."""
        for path in (self._runtime_root.parent, self._runtime_root):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._assert_owner_only_directory(path)

    @staticmethod
    def _assert_owner_only_directory(path: Path) -> None:
        path_stat = path.stat()
        if not stat.S_ISDIR(path_stat.st_mode):
            raise RuntimeError(f"Session runtime path is not a directory: {path}")
        if path_stat.st_uid != os.getuid():
            raise RuntimeError(f"Session runtime path is not owned by the current user: {path}")
        if stat.S_IMODE(path_stat.st_mode) & 0o077:
            path.chmod(0o700)

    @staticmethod
    def _secure_socket_path(socket_path: str) -> None:
        """Restrict the socket filesystem entry where the platform honors socket modes."""
        with suppress(OSError):
            Path(socket_path).chmod(0o600)

    @staticmethod
    def _authorize_peer(conn: socket.socket) -> None:
        peer_uid = _peer_uid(conn)
        if peer_uid is not None and peer_uid != os.getuid():
            raise PermissionError(f"Session IPC peer uid {peer_uid} does not match daemon uid {os.getuid()}.")

    @staticmethod
    def _validate_socket_path(socket_path: str) -> None:
        """Fail early when a Unix-domain socket path exceeds platform limits."""
        if len(socket_path) <= _UNIX_SOCKET_PATH_LIMIT:
            return
        msg = (
            f"Session socket path is too long ({len(socket_path)} > {_UNIX_SOCKET_PATH_LIMIT}): {socket_path}. "
            "Use a shorter runtime root."
        )
        raise RuntimeError(msg)


def _peer_uid(conn: socket.socket) -> int | None:
    """Return the Unix-socket peer uid on platforms that expose it to Python."""
    so_peercred = getattr(socket, "SO_PEERCRED", None)
    if so_peercred is None:
        return None
    try:
        data = conn.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("3i", data)
    return cast("int", uid)


def _prepare_session_start(workspace: str | Path) -> tuple[str, str]:
    """Validate that ``workspace`` can own a new session daemon.

    Returns ``(workspace_path, repo_path)`` as normalized strings. This is shared
    by the CLI daemon launcher and the public session-capture facade so they agree
    on repository validation and double-start refusal.
    """
    from vcs_core._errors import InvalidRepositoryStateError
    from vcs_core._workspace_external_state import assert_workspace_admissible
    from vcs_core.store import Store

    workspace_path = Path(os.path.abspath(os.fspath(workspace)))
    repo_path = str(workspace_path / ".vcscore")
    if not os.path.exists(repo_path):  # noqa: PTH110
        raise RuntimeError("not a vcs-core repository. Run `vcs-core init` first.")
    try:
        store = Store.open_existing(repo_path)
    except (FileNotFoundError, InvalidRepositoryStateError) as exc:
        raise RuntimeError(str(exc)) from exc
    assert_workspace_admissible(store, workspace_path)

    if is_session_alive(repo_path):
        info = read_session_info(repo_path)
        msg = f"Session already running (PID {info.pid if info else '?'})."
        raise RuntimeError(msg)

    return str(workspace_path), repo_path


def daemonize(workspace: str, *, foreground: bool = False) -> int:
    """Start a session daemon. Returns the daemon PID (0 if foreground)."""
    workspace_path, repo_path = _prepare_session_start(workspace)
    daemon = SessionDaemon(workspace_path)

    if foreground:
        daemon.start(foreground=True)
        return 0

    daemon.start(foreground=False)

    # Parent: wait for session.json to appear
    deadline = time.time() + 5.0
    while time.time() < deadline:
        info = read_session_info(repo_path)
        if info is None or not is_session_alive(repo_path):
            time.sleep(0.1)
            continue
        try:
            resp = send_request(info.socket_path, "get_state", {"hook_capabilities": []})
        except (ConnectionError, OSError):
            time.sleep(0.1)
            continue
        if resp.get("ok"):
            return info.pid
        time.sleep(0.1)

    msg = "Session daemon did not start within 5 seconds. Check .vcscore/session.log."
    raise RuntimeError(msg)


def stop_session(workspace: str) -> None:
    """Stop a running session daemon."""
    repo_path = os.path.join(os.path.abspath(workspace), ".vcscore")  # noqa: PTH118
    info = read_session_info(repo_path)

    if info is None or not is_session_alive(repo_path):
        # Clean up stale files
        remove_session_info(repo_path)
        msg = "No session is running."
        raise RuntimeError(msg)

    with suppress(ConnectionError, OSError):
        send_request(info.socket_path, "stop")

    # Wait for process to exit
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not is_session_alive(repo_path):
            remove_session_info(repo_path)
            return
        time.sleep(0.1)

    # Force kill if still alive
    with suppress(ProcessLookupError):
        os.kill(info.pid, signal.SIGKILL)
    remove_session_info(repo_path)
