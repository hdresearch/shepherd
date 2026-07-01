"""ModalSandbox — Modal sandbox with filesystem snapshot checkpoint/revert.

Modal uses gVisor which blocks mount syscalls, so OverlayFS doesn't work.
Uses Modal's native snapshot_filesystem() API instead:
- checkpoint() = snapshot_filesystem() → saves Image
- revert() = terminate current sandbox + create new from saved Image

Slower than OverlayFS (~1-3s per operation) but functional.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from typing_extensions import Self

logger = logging.getLogger(__name__)


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

    __slots__ = ("image", "name", "step", "timestamp")

    def __init__(self, name: str, step: int, timestamp: float, image: Any):
        self.name = name
        self.step = step
        self.timestamp = timestamp
        self.image = image  # Modal Image from snapshot_filesystem()


class ModalSandbox:
    """Modal sandbox with filesystem snapshot checkpoint/revert.

    Uses Modal's native snapshot API since gVisor blocks OverlayFS.
    Checkpoint creates a filesystem snapshot, revert creates a new
    sandbox from that snapshot.
    """

    def __init__(
        self,
        image: str = "python:3.11-slim",
        workdir: str = "/app",
        **kwargs: Any,
    ):
        self._image_name = image
        self._workdir = workdir
        self._kwargs = kwargs

        self._app: Any = None
        self._sandbox: Any = None
        self._base_image: Any = None
        self._checkpoints: dict[str, Checkpoint] = {}
        self._step = 0
        self._trajectory: list[dict[str, Any]] = []

    # ── Lifecycle ──

    def start(self) -> None:
        """Create a Modal sandbox."""
        import modal  # type: ignore[import-untyped,import-not-found,unused-ignore]

        self._app = modal.App.lookup("shepherd-sandbox", create_if_missing=True)
        self._base_image = modal.Image.from_registry(self._image_name)

        self._sandbox = modal.Sandbox.create(
            image=self._base_image,
            app=self._app,
            workdir=self._workdir,
            timeout=600,
            **self._kwargs,
        )
        logger.info(f"Created Modal sandbox: {self._sandbox.object_id}")

        # Ensure workdir exists
        proc = self._sandbox.exec("bash", "-c", f"mkdir -p {self._workdir}")
        proc.wait()

    def stop(self) -> None:
        """Terminate the sandbox."""
        if self._sandbox:
            try:
                self._sandbox.terminate()
                logger.info("Modal sandbox terminated")
            except (RuntimeError, OSError) as e:
                logger.warning(f"Failed to terminate sandbox: {e}")
            self._sandbox = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ── Exec ──

    def exec(self, command: str, timeout: int = 120) -> ExecResult:
        """Execute a shell command in the sandbox."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not started.")

        t0 = time.time()
        proc = self._sandbox.exec("bash", "-c", f"cd {self._workdir} && {command}")

        stdout = proc.stdout.read()
        stderr = proc.stderr.read()
        proc.wait()
        exit_code = proc.returncode

        dur = (time.time() - t0) * 1000

        self._step += 1
        self._trajectory.append(
            {
                "step": self._step,
                "command": command,
                "exit_code": exit_code,
                "stdout_preview": stdout[:200],
                "duration_ms": dur,
            }
        )

        return ExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code, duration_ms=dur)

    # ── Checkpoint / Revert ──

    def checkpoint(self, name: str) -> None:
        """Snapshot the filesystem. Saves as a Modal Image."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not started.")

        t0 = time.time()
        image = self._sandbox.snapshot_filesystem()
        dur = (time.time() - t0) * 1000

        self._checkpoints[name] = Checkpoint(
            name=name,
            step=self._step,
            timestamp=time.time(),
            image=image,
        )
        logger.debug(f"Checkpoint '{name}' at step {self._step} ({dur:.0f}ms)")

    def revert(self, name: str) -> None:
        """Revert by creating a new sandbox from the checkpoint's snapshot."""
        if name not in self._checkpoints:
            available = list(self._checkpoints.keys())
            raise KeyError(f"No checkpoint '{name}'. Available: {available}")

        import modal  # type: ignore[import-untyped,import-not-found,unused-ignore]

        ckpt = self._checkpoints[name]

        # Terminate current sandbox
        with contextlib.suppress(Exception):
            self._sandbox.terminate()

        # Create new sandbox from checkpoint image
        self._sandbox = modal.Sandbox.create(
            image=ckpt.image,
            app=self._app,
            workdir=self._workdir,
            timeout=600,
            **self._kwargs,
        )

        self._step = ckpt.step
        logger.debug(f"Reverted to '{name}' (step {ckpt.step})")

    def list_checkpoints(self) -> list[Checkpoint]:
        return sorted(self._checkpoints.values(), key=lambda c: c.step)

    # ── Info ──

    @property
    def sandbox_id(self) -> str | None:
        return self._sandbox.object_id if self._sandbox else None

    @property
    def step_count(self) -> int:
        return self._step

    @property
    def using_overlay(self) -> bool:
        return False  # Modal uses snapshots, not OverlayFS

    def get_trajectory(self) -> list[dict[str, Any]]:
        return list(self._trajectory)
