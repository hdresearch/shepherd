"""E2BSandbox — Firecracker micro-VM with OverlayFS checkpoint/revert.

E2B runs real Linux (Firecracker) as non-root user with sudo.
All mount/umount commands prefixed with sudo.

Verified: Linux 6.1.158, full OverlayFS cycle works with sudo.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from typing_extensions import Self

logger = logging.getLogger(__name__)

# OverlayFS scripts with sudo (E2B runs as non-root)
_OVERLAY_SETUP = """
set -e
WORKDIR="{workdir}"
sudo mkdir -p "$WORKDIR" /tmp/trace
sudo mount -t tmpfs tmpfs /tmp/trace
sudo mkdir -p /tmp/trace/base /tmp/trace/layers/current /tmp/trace/work /tmp/trace/meta
sudo cp -a "$WORKDIR/." /tmp/trace/base/ 2>/dev/null || true
echo "/tmp/trace/base" | sudo tee /tmp/trace/meta/lowers > /dev/null
sudo mount -t overlay overlay \
    -o "lowerdir=/tmp/trace/base,upperdir=/tmp/trace/layers/current,workdir=/tmp/trace/work" \
    "$WORKDIR"
echo "TRACE_OVERLAY_READY"
"""

_CHECKPOINT = """
set -e
WORKDIR="{workdir}"
CKPT_NAME="{name}"
sudo umount "$WORKDIR"
sudo mkdir -p "$WORKDIR"
sudo mv /tmp/trace/layers/current /tmp/trace/layers/$CKPT_NAME
sudo mkdir -p /tmp/trace/layers/current
sudo rm -rf /tmp/trace/work && sudo mkdir -p /tmp/trace/work
LOWER="/tmp/trace/layers/$CKPT_NAME"
if [ -f /tmp/trace/meta/lowers ]; then
    LOWER="$LOWER:$(cat /tmp/trace/meta/lowers)"
fi
echo "$LOWER" | sudo tee /tmp/trace/meta/lowers > /dev/null
echo "$LOWER" | sudo tee /tmp/trace/meta/ckpt_$CKPT_NAME > /dev/null
sudo mount -t overlay overlay \
    -o "lowerdir=$LOWER,upperdir=/tmp/trace/layers/current,workdir=/tmp/trace/work" \
    "$WORKDIR"
echo "TRACE_CHECKPOINT_OK"
"""

_REVERT = """
set -e
WORKDIR="{workdir}"
CKPT_NAME="{name}"
if [ ! -f /tmp/trace/meta/ckpt_$CKPT_NAME ]; then
    echo "ERROR: checkpoint '$CKPT_NAME' not found" >&2
    exit 1
fi
LOWER=$(cat /tmp/trace/meta/ckpt_$CKPT_NAME)
sudo umount "$WORKDIR" 2>/dev/null || true
sudo mkdir -p "$WORKDIR"
sudo rm -rf /tmp/trace/layers/current
sudo mkdir -p /tmp/trace/layers/current
sudo rm -rf /tmp/trace/work && sudo mkdir -p /tmp/trace/work
echo "$LOWER" | sudo tee /tmp/trace/meta/lowers > /dev/null
sudo mount -t overlay overlay \
    -o "lowerdir=$LOWER,upperdir=/tmp/trace/layers/current,workdir=/tmp/trace/work" \
    "$WORKDIR"
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


class E2BSandbox:
    """E2B Firecracker sandbox with OverlayFS checkpoint/revert.

    Uses sudo for mount operations (E2B runs as non-root user).
    """

    def __init__(
        self,
        template: str | None = None,
        workdir: str = "/app",
        timeout: int = 300,
        **kwargs: Any,
    ):
        self._template = template
        self._workdir = workdir
        self._timeout = timeout
        self._kwargs = kwargs

        self._sandbox: Any = None
        self._overlay_ready = False
        self._checkpoints: dict[str, Checkpoint] = {}
        self._step = 0
        self._trajectory: list[dict[str, Any]] = []

    # ── Lifecycle ──

    def start(self) -> None:
        """Create an E2B sandbox and set up OverlayFS."""
        from e2b import Sandbox  # type: ignore[import-untyped,import-not-found,unused-ignore]

        create_kwargs: dict[str, Any] = {"timeout": self._timeout, **self._kwargs}
        if self._template:
            create_kwargs["template"] = self._template

        self._sandbox = Sandbox.create(**create_kwargs)
        logger.info(f"Created E2B sandbox: {self._sandbox.sandbox_id}")

        # Ensure workdir exists
        self._sandbox.commands.run(f"sudo mkdir -p {self._workdir}")

        self._setup_overlay()

    def stop(self) -> None:
        """Kill the sandbox."""
        if self._sandbox:
            try:
                self._sandbox.kill()
                logger.info("E2B sandbox killed")
            except (RuntimeError, OSError) as e:
                logger.warning(f"Failed to kill sandbox: {e}")
            self._sandbox = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ── Exec ──

    def exec(self, command: str, timeout: int | None = None) -> ExecResult:
        """Execute a shell command in the sandbox."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not started.")

        wrapped = f"cd {self._workdir} && {command}"
        t0 = time.time()
        result = self._sandbox.commands.run(wrapped, timeout=timeout)
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
        if not self._overlay_ready:
            raise RuntimeError("OverlayFS not available.")

        script = _CHECKPOINT.format(workdir=self._workdir, name=name)
        result = self._exec_raw(script)

        if "TRACE_CHECKPOINT_OK" not in result.stdout:
            raise RuntimeError(f"Checkpoint failed: {result.stdout} {result.stderr}")

        self._checkpoints[name] = Checkpoint(
            name=name,
            step=self._step,
            timestamp=time.time(),
        )

    def revert(self, name: str) -> None:
        if name not in self._checkpoints:
            available = list(self._checkpoints.keys())
            raise KeyError(f"No checkpoint '{name}'. Available: {available}")

        script = _REVERT.format(workdir=self._workdir, name=name)
        result = self._exec_raw(script)

        if "TRACE_REVERT_OK" not in result.stdout:
            raise RuntimeError(f"Revert failed: {result.stdout} {result.stderr}")

        self._step = self._checkpoints[name].step

    def list_checkpoints(self) -> list[Checkpoint]:
        return sorted(self._checkpoints.values(), key=lambda c: c.step)

    # ── Info ──

    @property
    def sandbox_id(self) -> str | None:
        return self._sandbox.sandbox_id if self._sandbox else None

    @property
    def step_count(self) -> int:
        return self._step

    @property
    def using_overlay(self) -> bool:
        return self._overlay_ready

    def get_trajectory(self) -> list[dict[str, Any]]:
        return list(self._trajectory)

    # ── Internal ──

    def _exec_raw(self, script: str) -> ExecResult:
        """Execute without cd-ing into workdir (for mount/umount)."""
        t0 = time.time()
        result = self._sandbox.commands.run(script)
        dur = (time.time() - t0) * 1000
        return ExecResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.exit_code,
            duration_ms=dur,
        )

    def _setup_overlay(self) -> None:
        script = _OVERLAY_SETUP.format(workdir=self._workdir)
        result = self._exec_raw(script)

        if "TRACE_OVERLAY_READY" in result.stdout:
            self._overlay_ready = True
            logger.info("OverlayFS ready inside E2B sandbox")
        else:
            self._overlay_ready = False
            logger.warning(f"OverlayFS setup failed: {result.stdout} {result.stderr}")
