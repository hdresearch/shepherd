from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParentTreeEntry:
    path: str
    kind: str
    size: int | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None
    git_mode: int | None = None
    sha256: str | None = None
    link_target: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ParentTreeManifest:
    layer_name: str
    entries: dict[str, ParentTreeEntry]


@dataclass(frozen=True)
class ParentTreeDivergence:
    path: str
    reason: str
    before: ParentTreeEntry | None = None
    after: ParentTreeEntry | None = None


def capture_parent_tree_manifest(root: str | Path, *, layer_name: str) -> ParentTreeManifest:
    root_path = Path(root)
    entries: dict[str, ParentTreeEntry] = {}
    _capture_path(root_path, root_path, entries)
    return ParentTreeManifest(layer_name=layer_name, entries=entries)


def diff_parent_tree_manifest(manifest: ParentTreeManifest, root: str | Path) -> tuple[ParentTreeDivergence, ...]:
    root_path = Path(root)
    divergences: list[ParentTreeDivergence] = []
    current_entries: dict[str, ParentTreeEntry] = {}
    _capture_path(root_path, root_path, current_entries)
    for path, before in sorted(manifest.entries.items()):
        after = current_entries.get(path)
        if after is None:
            divergences.append(ParentTreeDivergence(path=path, reason="deleted", before=before))
            continue
        reason = _entry_divergence_reason(before, after)
        if reason is not None:
            divergences.append(ParentTreeDivergence(path=path, reason=reason, before=before, after=after))
    for path, after in sorted(current_entries.items()):
        if path not in manifest.entries:
            divergences.append(ParentTreeDivergence(path=path, reason="added", after=after))
    return tuple(divergences)


def _capture_path(root: Path, path: Path, entries: dict[str, ParentTreeEntry]) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        entries[_relative_path(root, path)] = ParentTreeEntry(
            path=_relative_path(root, path),
            kind="unverifiable",
            detail=f"stat failed: {exc}",
        )
        return

    rel = _relative_path(root, path)
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        try:
            link_target = str(path.readlink())
        except OSError as exc:
            entries[rel] = ParentTreeEntry(
                path=rel,
                kind="unverifiable",
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
                ctime_ns=st.st_ctime_ns,
                git_mode=stat.S_IMODE(mode),
                detail=f"readlink failed: {exc}",
            )
            return
        entries[rel] = ParentTreeEntry(
            path=rel,
            kind="symlink",
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            ctime_ns=st.st_ctime_ns,
            git_mode=stat.S_IMODE(mode),
            link_target=link_target,
        )
        return

    if stat.S_ISDIR(mode):
        if rel:
            entries[rel] = ParentTreeEntry(
                path=rel,
                kind="dir",
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
                ctime_ns=st.st_ctime_ns,
                git_mode=stat.S_IMODE(mode),
            )
        try:
            children = sorted(path.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            entries[rel or "."] = ParentTreeEntry(
                path=rel or ".",
                kind="unverifiable",
                detail=f"scandir failed: {exc}",
            )
            return
        for child in children:
            _capture_path(root, child, entries)
        return

    if stat.S_ISREG(mode):
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            entries[rel] = ParentTreeEntry(
                path=rel,
                kind="unverifiable",
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
                ctime_ns=st.st_ctime_ns,
                git_mode=stat.S_IMODE(mode),
                detail=f"read failed: {exc}",
            )
            return
        entries[rel] = ParentTreeEntry(
            path=rel,
            kind="file",
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            ctime_ns=st.st_ctime_ns,
            git_mode=stat.S_IMODE(mode),
            sha256=digest,
        )
        return

    entries[rel] = ParentTreeEntry(
        path=rel,
        kind="unsupported",
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
        git_mode=stat.S_IMODE(mode),
        detail=f"unsupported mode: {mode:o}",
    )


def _entry_divergence_reason(before: ParentTreeEntry, after: ParentTreeEntry) -> str | None:
    if before.kind in {"unsupported", "unverifiable"} or after.kind in {"unsupported", "unverifiable"}:
        return "unverifiable"
    if before.kind != after.kind:
        return "kind_changed"
    if before.kind == "dir":
        return None
    if before.kind == "file":
        if before.git_mode != after.git_mode:
            return "mode_changed"
        if before.sha256 != after.sha256:
            return "content_changed"
        return None
    if before.kind == "symlink":
        if before.link_target != after.link_target:
            return "target_changed"
        return None
    return "unverifiable"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _relative_path(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return str(path)
    value = rel.as_posix()
    return "" if value == "." else value
