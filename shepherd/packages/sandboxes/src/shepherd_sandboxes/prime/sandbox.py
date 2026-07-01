"""PrimeSandbox — Prime Intellect sandbox with OverlayFS checkpoint/revert.

Wraps the prime_sandboxes SDK. OverlayFS works directly (no tmpfs workaround
needed) — Prime gives full root + mount capabilities.

Sync API (Prime SDK is sync, not async).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from typing_extensions import Self

logger = logging.getLogger(__name__)

# Prime doesn't support umount (mounts succeed but can't be undone).
# Use copy-based checkpoints instead — slower but reliable.
_SETUP = """
set -e
WORKDIR="{workdir}"
mkdir -p "$WORKDIR" /trace/checkpoints
echo "TRACE_COPY_READY"
"""

_CHECKPOINT = """
set -e
WORKDIR="{workdir}"
CKPT_NAME="{name}"
rm -rf /trace/checkpoints/$CKPT_NAME
cp -a "$WORKDIR" /trace/checkpoints/$CKPT_NAME
echo "TRACE_CHECKPOINT_OK"
"""

_REVERT = """
set -e
WORKDIR="{workdir}"
CKPT_NAME="{name}"
if [ ! -d /trace/checkpoints/$CKPT_NAME ]; then
    echo "ERROR: checkpoint '$CKPT_NAME' not found" >&2
    exit 1
fi
rm -rf "$WORKDIR"
cp -a /trace/checkpoints/$CKPT_NAME "$WORKDIR"
echo "TRACE_REVERT_OK"
"""


class ExecResult:
    """Result from executing a command."""

    __slots__ = ("duration_ms", "exit_code", "stderr", "stdout")

    def __init__(self, stdout: str, stderr: str, exit_code: int, duration_ms: float):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration_ms = duration_ms


class Checkpoint:
    """A saved checkpoint."""

    __slots__ = ("name", "step", "timestamp")

    def __init__(self, name: str, step: int, timestamp: float):
        self.name = name
        self.step = step
        self.timestamp = timestamp


class PrimeSandbox:
    """Prime Intellect sandbox with OverlayFS checkpoint/revert.

    Sync API (Prime SDK is synchronous).

    Usage:
        sandbox = PrimeSandbox(image="python:3.11-slim")
        sandbox.start()
        sandbox.exec("echo hello")
        sandbox.checkpoint("safe")
        sandbox.exec("rm -rf /important")
        sandbox.revert("safe")
        sandbox.stop()
    """

    def __init__(
        self,
        image: str = "python:3.11-slim",
        workdir: str = "/app",
        api_key: str | None = None,
        timeout_minutes: int = 60,
        **kwargs: Any,
    ):
        self._image = image
        self._workdir = workdir
        self._api_key = api_key
        self._timeout_minutes = timeout_minutes
        self._kwargs = kwargs

        self._client: Any = None
        self._sandbox_id: str | None = None
        self._overlay_ready = False
        self._checkpoints: dict[str, Checkpoint] = {}
        self._step = 0
        self._trajectory: list[dict[str, Any]] = []

    @classmethod
    def from_existing(
        cls,
        sandbox_id: str,
        workdir: str = "/app",
        api_key: str | None = None,
    ) -> PrimeSandbox:
        """Wrap an existing Prime sandbox."""
        instance = cls(workdir=workdir, api_key=api_key)
        instance._sandbox_id = sandbox_id
        return instance

    # ── Lifecycle ──

    def start(self) -> None:
        """Create the sandbox and set up OverlayFS."""
        import os

        from prime_sandboxes import (  # type: ignore[import-untyped,import-not-found,unused-ignore]
            APIClient,
            CreateSandboxRequest,
            SandboxClient,
        )

        api_key = self._api_key or os.environ.get("PRIME_API_KEY", "")
        api_client = APIClient(api_key=api_key)
        self._client = SandboxClient(api_client=api_client)

        if self._sandbox_id is None:
            sandbox = self._client.create(
                CreateSandboxRequest(
                    name=f"shepherd-{int(time.time())}",
                    docker_image=self._image,
                    timeout_minutes=self._timeout_minutes,
                    **self._kwargs,
                )
            )
            self._sandbox_id = sandbox.id
            logger.info(f"Created sandbox: {self._sandbox_id}")
            self._client.wait_for_creation(self._sandbox_id)
        else:
            logger.info(f"Attached to existing sandbox: {self._sandbox_id}")

        self._setup_overlay()

    def stop(self, delete: bool = True) -> None:
        """Stop and optionally delete the sandbox."""
        if self._client and self._sandbox_id and delete:
            try:
                self._client.delete(self._sandbox_id)
                logger.info(f"Deleted sandbox: {self._sandbox_id}")
            except (RuntimeError, OSError) as e:
                logger.warning(f"Failed to delete sandbox: {e}")

        self._sandbox_id = None
        self._client = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ── Exec ──

    def exec(self, command: str, timeout: int = 120) -> ExecResult:
        """Execute a shell command in the sandbox."""
        if not self._client or not self._sandbox_id:
            raise RuntimeError("Sandbox not started.")

        wrapped = f"cd {self._workdir} && {command}"
        t0 = time.time()
        result = self._client.execute_command(self._sandbox_id, wrapped, timeout=timeout)
        dur = (time.time() - t0) * 1000

        self._step += 1
        self._trajectory.append(
            {
                "step": self._step,
                "command": command,
                "exit_code": result.exit_code,
                "stdout_preview": (result.stdout or "")[:200],
                "duration_ms": dur,
            }
        )

        return ExecResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.exit_code,
            duration_ms=dur,
        )

    # ── Checkpoint / Revert ──

    def checkpoint(self, name: str) -> None:
        """Save current state via OverlayFS."""
        if not self._overlay_ready:
            raise RuntimeError("OverlayFS not available.")

        script = _CHECKPOINT.format(workdir=self._workdir, name=name)
        result = self._client.execute_command(self._sandbox_id, script, timeout=30)

        if result.exit_code != 0 or "TRACE_CHECKPOINT_OK" not in (result.stdout or ""):
            raise RuntimeError(f"Checkpoint failed: {result.stdout} {result.stderr}")

        self._checkpoints[name] = Checkpoint(
            name=name,
            step=self._step,
            timestamp=time.time(),
        )

    def revert(self, name: str) -> None:
        """Revert to a previously saved checkpoint."""
        if name not in self._checkpoints:
            available = list(self._checkpoints.keys())
            raise KeyError(f"No checkpoint '{name}'. Available: {available}")

        script = _REVERT.format(workdir=self._workdir, name=name)
        result = self._client.execute_command(self._sandbox_id, script, timeout=30)

        if result.exit_code != 0 or "TRACE_REVERT_OK" not in (result.stdout or ""):
            raise RuntimeError(f"Revert failed: {result.stdout} {result.stderr}")

        self._step = self._checkpoints[name].step

    def list_checkpoints(self) -> list[Checkpoint]:
        return sorted(self._checkpoints.values(), key=lambda c: c.step)

    # ── Info ──

    @property
    def sandbox_id(self) -> str | None:
        return self._sandbox_id

    @property
    def step_count(self) -> int:
        return self._step

    @property
    def using_overlay(self) -> bool:
        return self._overlay_ready

    def get_trajectory(self) -> list[dict[str, Any]]:
        return list(self._trajectory)

    # ── Internal ──

    def _setup_overlay(self) -> None:
        """Set up copy-based checkpointing (OverlayFS umount blocked on Prime)."""
        script = _SETUP.format(workdir=self._workdir)
        result = self._client.execute_command(self._sandbox_id, script, timeout=30)

        if result.exit_code == 0 and "TRACE_COPY_READY" in (result.stdout or ""):
            self._overlay_ready = True  # "overlay_ready" is a misnomer but keeps interface consistent
            logger.info("Copy-based checkpointing ready inside Prime sandbox")
        else:
            self._overlay_ready = False
            logger.warning(f"Setup failed: {result.stdout} {result.stderr}")
