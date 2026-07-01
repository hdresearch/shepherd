"""K8sSandbox — Kubernetes pod with OverlayFS checkpoint/revert.

Creates a privileged pod, execs commands via kubectl API, and uses
OverlayFS-on-tmpfs for checkpoint/revert (~50ms).

Verified on Vultr Kubernetes (real Linux 6.8.0 kernel, full caps).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from typing_extensions import Self

logger = logging.getLogger(__name__)

# Same OverlayFS scripts as Daytona (tmpfs-backed)
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
umount "$WORKDIR"
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
umount "$WORKDIR" 2>/dev/null || true
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


class K8sSandbox:
    """Kubernetes pod with OverlayFS checkpoint/revert.

    Creates a privileged pod, uses kubectl exec for commands,
    OverlayFS-on-tmpfs for checkpoint/revert.
    """

    def __init__(
        self,
        image: str = "ubuntu:22.04",
        workdir: str = "/app",
        kubeconfig: str | None = None,
        namespace: str = "default",
        **kwargs: Any,
    ):
        self._image = image
        self._workdir = workdir
        self._kubeconfig = kubeconfig
        self._namespace = namespace
        self._kwargs = kwargs

        self._api: Any = None
        self._pod_name: str | None = None
        self._overlay_ready = False
        self._checkpoints: dict[str, Checkpoint] = {}
        self._step = 0
        self._trajectory: list[dict[str, Any]] = []

    # ── Lifecycle ──

    def start(self) -> None:
        """Create a privileged pod and set up OverlayFS."""
        from kubernetes import client, config  # type: ignore[import-untyped,import-not-found,unused-ignore]
        from kubernetes.stream import stream  # type: ignore[import-untyped,import-not-found,unused-ignore]

        if self._kubeconfig:
            config.load_kube_config(config_file=self._kubeconfig)
        else:
            config.load_kube_config()

        self._api = client.CoreV1Api()
        self._stream = stream
        self._pod_name = f"shepherd-{uuid.uuid4().hex[:8]}"

        # Create privileged pod
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(name=self._pod_name, namespace=self._namespace),
            spec=client.V1PodSpec(
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=self._image,
                        command=["sleep", "3600"],
                        security_context=client.V1SecurityContext(privileged=True),
                    )
                ],
                restart_policy="Never",
            ),
        )
        self._api.create_namespaced_pod(namespace=self._namespace, body=pod)
        logger.info(f"Created pod: {self._pod_name}")

        # Wait for pod to be ready
        for _ in range(60):
            pod_status = self._api.read_namespaced_pod_status(
                name=self._pod_name,
                namespace=self._namespace,
            )
            if pod_status.status.phase == "Running":
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Pod {self._pod_name} did not start in 60s")

        logger.info(f"Pod ready: {self._pod_name}")
        self._setup_overlay()

    def stop(self, delete: bool = True) -> None:
        """Delete the pod."""
        if self._api and self._pod_name and delete:
            try:
                self._api.delete_namespaced_pod(
                    name=self._pod_name,
                    namespace=self._namespace,
                )
                logger.info(f"Deleted pod: {self._pod_name}")
            except (RuntimeError, OSError) as e:
                logger.warning(f"Failed to delete pod: {e}")

        self._pod_name = None
        self._api = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ── Exec ──

    def exec(self, command: str, timeout: int = 120) -> ExecResult:
        """Execute a command in the pod via kubectl exec."""
        if not self._api or not self._pod_name:
            raise RuntimeError("Pod not started.")

        wrapped = f"cd {self._workdir} && {command}"
        t0 = time.time()

        resp = self._stream(
            self._api.connect_get_namespaced_pod_exec,
            name=self._pod_name,
            namespace=self._namespace,
            command=["bash", "-c", wrapped],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )

        stdout_data = ""
        stderr_data = ""
        while resp.is_open():
            resp.update(timeout=timeout)
            if resp.peek_stdout():
                stdout_data += resp.read_stdout()
            if resp.peek_stderr():
                stderr_data += resp.read_stderr()
        resp.close()

        exit_code = resp.returncode if hasattr(resp, "returncode") else 0
        dur = (time.time() - t0) * 1000

        self._step += 1
        self._trajectory.append(
            {
                "step": self._step,
                "command": command,
                "exit_code": exit_code,
                "stdout_preview": stdout_data[:200],
                "duration_ms": dur,
            }
        )

        return ExecResult(stdout=stdout_data, stderr=stderr_data, exit_code=exit_code, duration_ms=dur)

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
    def pod_name(self) -> str | None:
        return self._pod_name

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
        """Execute a script without cd-ing into workdir (for mount/umount ops)."""
        if not self._api or not self._pod_name:
            raise RuntimeError("Pod not started.")

        t0 = time.time()
        resp = self._stream(
            self._api.connect_get_namespaced_pod_exec,
            name=self._pod_name,
            namespace=self._namespace,
            command=["bash", "-c", script],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )

        stdout_data = ""
        stderr_data = ""
        while resp.is_open():
            resp.update(timeout=30)
            if resp.peek_stdout():
                stdout_data += resp.read_stdout()
            if resp.peek_stderr():
                stderr_data += resp.read_stderr()
        resp.close()

        exit_code = resp.returncode if hasattr(resp, "returncode") else 0
        dur = (time.time() - t0) * 1000

        return ExecResult(stdout=stdout_data, stderr=stderr_data, exit_code=exit_code, duration_ms=dur)

    def _setup_overlay(self) -> None:
        script = _OVERLAY_SETUP.format(workdir=self._workdir)
        result = self._exec_raw(script)

        if "TRACE_OVERLAY_READY" in result.stdout:
            self._overlay_ready = True
            logger.info("OverlayFS ready inside K8s pod")
        else:
            self._overlay_ready = False
            logger.warning(f"OverlayFS setup failed: {result.stdout} {result.stderr}")
