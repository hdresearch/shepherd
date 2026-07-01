"""Path resolution helpers for Python interception candidates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


@dataclass(frozen=True)
class PatchPathCandidate:
    """A path argument plus the POSIX directory fd context used to resolve it."""

    path: str | os.PathLike[str]
    dir_fd: int | None = None


@dataclass(frozen=True)
class PatchPathResolution:
    """Resolved path candidate, or an explicit unsafe-to-classify state."""

    path: Path | None = None
    unknown: bool = False


PatchPathCandidateLike: TypeAlias = PatchPathCandidate | str | os.PathLike[str] | object


def resolve_patch_path(candidate: PatchPathCandidateLike) -> Path | None:
    """Resolve a patch path candidate to an absolute path.

    fd-relative paths must resolve through the fd. If the fd target cannot be
    resolved, fail closed instead of guessing via cwd.
    """
    return resolve_patch_path_status(candidate).path


def resolve_patch_path_status(candidate: PatchPathCandidateLike) -> PatchPathResolution:
    """Resolve a patch path candidate while preserving fd-resolution failures."""
    if isinstance(candidate, PatchPathCandidate):
        return _resolve_path_with_dir_fd(candidate.path, candidate.dir_fd)
    if not isinstance(candidate, (str, os.PathLike)):
        return PatchPathResolution()
    return _resolve_path_with_dir_fd(candidate, None)


def workspace_relative(candidate: PatchPathCandidateLike, workspace: Path) -> str | None:
    resolved = resolve_patch_path(candidate)
    if resolved is None:
        return None
    try:
        relative = resolved.relative_to(workspace.resolve())
    except ValueError:
        return None
    if relative.parts and relative.parts[0] == ".vcscore":
        return None
    return relative.as_posix()


def _resolve_path_with_dir_fd(path_like: str | os.PathLike[str], dir_fd: int | None) -> PatchPathResolution:
    raw = os.fspath(path_like)
    path = Path(raw)
    if path.is_absolute():
        return PatchPathResolution(path=path.resolve())
    if dir_fd is None or dir_fd == getattr(os, "AT_FDCWD", object()):
        return PatchPathResolution(path=(Path.cwd() / path).resolve())
    fd_directory = _resolve_fd_directory(dir_fd)
    if fd_directory is None:
        return PatchPathResolution(unknown=True)
    return PatchPathResolution(path=(fd_directory / path).resolve())


def _resolve_fd_directory(dir_fd: int) -> Path | None:
    if not isinstance(dir_fd, int) or isinstance(dir_fd, bool) or dir_fd < 0:
        return None
    fcntl_path = _resolve_fd_directory_with_fcntl(dir_fd)
    if fcntl_path is not None:
        return fcntl_path
    for fd_root in (Path("/proc/self/fd"), Path("/dev/fd")):
        fd_path = fd_root / str(dir_fd)
        try:
            resolved = fd_path.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir():
            return resolved
    return None


def _resolve_fd_directory_with_fcntl(dir_fd: int) -> Path | None:
    try:
        import fcntl
    except ImportError:
        return None
    f_getpath = getattr(fcntl, "F_GETPATH", None)
    if f_getpath is None:
        return None
    try:
        raw = fcntl.fcntl(dir_fd, f_getpath, b"\0" * 1024)
    except (OSError, ValueError):
        return None
    path_text = raw.split(b"\0", 1)[0].decode(errors="surrogateescape")
    if not path_text:
        return None
    path = Path(path_text).resolve()
    return path if path.is_dir() else None
