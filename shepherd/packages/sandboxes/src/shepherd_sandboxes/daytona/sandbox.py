"""DaytonaSandbox — remote container with OverlayFS checkpoint/revert.

Lightweight wrapper around the Daytona SDK that adds:
- OverlayFS-based checkpoint/revert inside the sandbox (~50ms)
- Same interface as the local container device
- Works with Harbor benchmarks or standalone

The sandbox uses the same OverlayFS-on-tmpfs trick as the local
devcontainer — no special Daytona features needed, just exec().
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from typing_extensions import Self

logger = logging.getLogger(__name__)

# OverlayFS setup script (runs inside the sandbox)
_OVERLAY_SETUP = """
set -e
WORKDIR="{workdir}"
mkdir -p "$WORKDIR"
mkdir -p /trace
mount -t tmpfs tmpfs /trace
mkdir -p /trace/base /trace/layers/current /trace/work /trace/meta
cp -a "$WORKDIR/." /trace/base/ 2>/dev/null || true
echo "/trace/base" > /trace/meta/lowers
mount -t overlay overlay \
    -o "lowerdir=/trace/base,upperdir=/trace/layers/current,workdir=/trace/work" \
    "$WORKDIR"
echo "TRACE_OVERLAY_READY"
"""

_CHECKPOINT = """
set -e
WORKDIR="{workdir}"
CKPT_NAME="{name}"
umount -l "$WORKDIR" 2>/dev/null || umount "$WORKDIR"
mkdir -p "$WORKDIR"
mv /trace/layers/current /trace/layers/$CKPT_NAME
mkdir -p /trace/layers/current
rm -rf /trace/work && mkdir -p /trace/work
LOWER="/trace/layers/$CKPT_NAME"
if [ -f /trace/meta/lowers ]; then
    LOWER="$LOWER:$(cat /trace/meta/lowers)"
fi
echo "$LOWER" > /trace/meta/lowers
echo "$LOWER" > /trace/meta/ckpt_$CKPT_NAME
mount -t overlay overlay \
    -o "lowerdir=$LOWER,upperdir=/trace/layers/current,workdir=/trace/work" \
    "$WORKDIR"
echo "TRACE_CHECKPOINT_OK"
"""

_REVERT = """
set -e
WORKDIR="{workdir}"
CKPT_NAME="{name}"
if [ ! -f /trace/meta/ckpt_$CKPT_NAME ]; then
    echo "ERROR: checkpoint '$CKPT_NAME' not found" >&2
    exit 1
fi
LOWER=$(cat /trace/meta/ckpt_$CKPT_NAME)
umount -l "$WORKDIR" 2>/dev/null || true
mkdir -p "$WORKDIR"
rm -rf /trace/layers/current
mkdir -p /trace/layers/current
rm -rf /trace/work && mkdir -p /trace/work
echo "$LOWER" > /trace/meta/lowers
mount -t overlay overlay \
    -o "lowerdir=$LOWER,upperdir=/trace/layers/current,workdir=/trace/work" \
    "$WORKDIR"
echo "TRACE_REVERT_OK"
"""


@dataclass
class ExecResult:
    """Result from executing a command in the sandbox."""

    stdout: str
    exit_code: int
    duration_ms: float


@dataclass
class Checkpoint:
    """A saved checkpoint."""

    name: str
    step: int
    timestamp: float


class DaytonaSandbox:
    """Remote sandbox with OverlayFS checkpoint/revert.

    Usage:
        sandbox = DaytonaSandbox(image="ubuntu:22.04")
        await sandbox.start()
        result = await sandbox.exec("echo hello")
        await sandbox.checkpoint("step1")
        await sandbox.exec("rm -rf /important")
        await sandbox.revert("step1")  # undo!
        await sandbox.stop()

    Or as context manager:
        async with DaytonaSandbox(image="ubuntu:22.04") as sandbox:
            await sandbox.exec("echo hello")
    """

    def __init__(
        self,
        image: str = "ubuntu:22.04",
        workdir: str = "/app",
        api_key: str | None = None,
        **kwargs: Any,
    ):
        self._image = image
        self._workdir = workdir
        self._api_key = api_key
        self._kwargs = kwargs

        self._client: Any = None
        self._sandbox: Any = None
        self._existing_id: str | None = None
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
    ) -> DaytonaSandbox:
        """Wrap an existing Daytona sandbox."""
        instance = cls(workdir=workdir, api_key=api_key)
        instance._existing_id = sandbox_id
        return instance

    # ── Lifecycle ──

    async def start(self) -> None:
        """Create the sandbox and set up OverlayFS."""
        import os

        from daytona_sdk import (  # type: ignore[import-untyped,import-not-found,unused-ignore]
            AsyncDaytona,
            CreateSandboxFromImageParams,
            DaytonaConfig,
        )

        api_key = self._api_key or os.environ.get("DAYTONA_API_KEY", "")
        config = DaytonaConfig(api_key=api_key)
        self._client = AsyncDaytona(config=config)

        if hasattr(self, "_existing_id"):
            self._sandbox = await self._client.get(self._existing_id)
            logger.info(f"Attached to existing sandbox: {self._existing_id}")
        else:
            self._sandbox = await self._client.create(CreateSandboxFromImageParams(image=self._image, **self._kwargs))
            logger.info(f"Created sandbox: {self._sandbox.id} (image={self._image})")

        await self._setup_overlay()

    async def stop(self, delete: bool = True) -> None:
        """Stop and optionally delete the sandbox."""
        if self._sandbox and delete:
            try:
                await self._sandbox.delete()
                logger.info(f"Deleted sandbox: {self._sandbox.id}")
            except (RuntimeError, OSError) as e:
                logger.warning(f"Failed to delete sandbox: {e}")

        if self._client:
            await self._client.close()

        self._sandbox = None
        self._client = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ── Exec ──

    async def exec(self, command: str, timeout: int | None = None) -> ExecResult:
        """Execute a shell command in the sandbox."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not started. Call start() first.")

        wrapped = f"cd {self._workdir} && {command}"
        t0 = time.time()
        result = await self._sandbox.process.exec(wrapped, timeout=timeout)
        dur = (time.time() - t0) * 1000

        self._step += 1
        stdout = result.result or ""

        self._trajectory.append(
            {
                "step": self._step,
                "command": command,
                "exit_code": result.exit_code,
                "stdout_preview": stdout[:200],
                "duration_ms": dur,
            }
        )

        return ExecResult(stdout=stdout, exit_code=result.exit_code, duration_ms=dur)

    # ── Checkpoint / Revert ──

    async def checkpoint(self, name: str) -> None:
        """Save current state."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not started.")
        if not self._overlay_ready:
            raise RuntimeError("OverlayFS not available.")

        script = _CHECKPOINT.format(workdir=self._workdir, name=name)
        result = await self._sandbox.process.exec(script)

        if result.exit_code != 0 or "TRACE_CHECKPOINT_OK" not in (result.result or ""):
            raise RuntimeError(f"Checkpoint failed: {result.result}")

        self._checkpoints[name] = Checkpoint(
            name=name,
            step=self._step,
            timestamp=time.time(),
        )
        logger.debug(f"Checkpoint '{name}' at step {self._step}")

    async def revert(self, name: str) -> None:
        """Revert to a previously saved checkpoint."""
        if name not in self._checkpoints:
            available = list(self._checkpoints.keys())
            raise KeyError(f"No checkpoint '{name}'. Available: {available}")
        if not self._overlay_ready:
            raise RuntimeError("OverlayFS not available.")

        script = _REVERT.format(workdir=self._workdir, name=name)
        result = await self._sandbox.process.exec(script)

        if result.exit_code != 0 or "TRACE_REVERT_OK" not in (result.result or ""):
            raise RuntimeError(f"Revert failed: {result.result}")

        self._step = self._checkpoints[name].step
        logger.debug(f"Reverted to '{name}' (step {self._step})")

    def list_checkpoints(self) -> list[Checkpoint]:
        """Return all checkpoints ordered by step."""
        return sorted(self._checkpoints.values(), key=lambda c: c.step)

    # ── Info ──

    @property
    def sandbox_id(self) -> str | None:
        return self._sandbox.id if self._sandbox else None

    @property
    def step_count(self) -> int:
        return self._step

    @property
    def using_overlay(self) -> bool:
        return self._overlay_ready

    def get_trajectory(self) -> list[dict[str, Any]]:
        return list(self._trajectory)

    # ── Internal ──

    async def _setup_overlay(self) -> None:
        """Set up OverlayFS inside the sandbox."""
        script = _OVERLAY_SETUP.format(workdir=self._workdir)
        result = await self._sandbox.process.exec(script)

        if result.exit_code == 0 and "TRACE_OVERLAY_READY" in (result.result or ""):
            self._overlay_ready = True
            logger.info("OverlayFS ready inside Daytona sandbox")
        else:
            self._overlay_ready = False
            logger.warning(f"OverlayFS setup failed: {result.result}")
