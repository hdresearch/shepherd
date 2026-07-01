"""Internal helpers for the filesystem preload shim."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal, TypeAlias

FsCaptureOp: TypeAlias = Literal["write_open", "write_observed", "write_close", "metadata_change", "unlink"]
FS_CAPTURE_OPS: Final[frozenset[str]] = frozenset(
    ("write_open", "write_observed", "write_close", "metadata_change", "unlink")
)
FS_CAPTURE_SHELL_COMMAND_FINISH_OP: Final[str] = "shell_command_finish"

_FS_CAPTURE_SHIM_NAME = "fs_capture_shim.so"


@dataclass(frozen=True)
class FsCaptureEvent:
    """Normalized filesystem capture event translated from a hook payload."""

    op: FsCaptureOp
    scope: str
    scope_instance_id: str
    path: str
    pid: int
    proc_seq: int
    ppid: int | None = None
    exe: str | None = None
    cwd: str | None = None


def shim_source_path() -> Path:
    """Return the native shim source path."""
    return Path(__file__).resolve().parent / "_native" / "fs_capture_shim.c"


def ensure_fs_capture_shim(repo_path: str | Path) -> str:
    """Build the fs-capture preload shim if needed and return its path."""
    if _platform_name() != "linux":
        raise RuntimeError("session shell --capture is only supported on Linux.")

    source = shim_source_path()
    if not source.is_file():
        raise RuntimeError(f"fs capture shim source is missing: {source}")

    repo_dir = Path(repo_path)
    output_dir = repo_dir / "native"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / _FS_CAPTURE_SHIM_NAME

    if output.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
        return str(output)

    cmd = [
        "cc",
        "-shared",
        "-fPIC",
        "-O2",
        "-Wall",
        "-Wextra",
        "-o",
        str(output),
        str(source),
        "-ldl",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("fs capture requires a working C compiler (`cc`) on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(f"failed to compile fs capture shim: {details}") from exc
    return str(output)


def _platform_name() -> str:
    return sys.platform


def normalize_fs_capture_op(value: object) -> FsCaptureOp | None:
    """Return a supported direct-capture operation, or None for no-effect input."""
    if not isinstance(value, str):
        return None
    if value in FS_CAPTURE_OPS:
        return value  # type: ignore[return-value]
    return None


def normalize_fs_capture_path(value: object) -> str | None:
    """Return a safe workspace-relative capture path, or None for no-effect input."""
    if not isinstance(value, str) or value in ("", ".") or "\0" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute():
        return None
    parts = path.parts
    if not parts or ".." in parts or parts[0] == ".vcscore":
        return None
    return path.as_posix()
