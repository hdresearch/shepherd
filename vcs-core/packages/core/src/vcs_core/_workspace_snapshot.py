"""Render store workspace trees as filesystem snapshots for session overlays."""

from __future__ import annotations

import json
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcs_core.store import Store


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """A rendered immutable-ish workspace tree plus sidecar metadata."""

    root: Path
    metadata_path: Path
    tree_oid: str
    file_count: int


def _default_snapshots_root(store: Store) -> Path:
    return Path(store.repo_path) / "runtime" / "snapshots"


def _metadata_payload(*, ref: str, tree_oid: str, file_count: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ref": ref,
        "workspace_tree_oid": tree_oid,
        "file_count": file_count,
        "complete": True,
        "rendered_at": time.time(),
    }


def _write_snapshot_contents(store: Store, ref: str, root: Path) -> int:
    file_count = 0
    for path, _oid, mode in store.list_workspace_files(ref):
        content = store.read_workspace_file(ref, path)
        if content is None:
            continue
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(stat.S_IMODE(mode))
        file_count += 1
    return file_count


def _validate_snapshot(store: Store, ref: str, *, root: Path, metadata_path: Path, tree_oid: str) -> int:
    if not root.is_dir() or not metadata_path.is_file():
        raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} is incomplete.")
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} has invalid metadata.") from exc
    if metadata.get("workspace_tree_oid") != tree_oid or metadata.get("complete") is not True:
        raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} does not match the requested tree.")

    expected_paths: set[str] = set()
    expected_count = 0
    for path, _oid, mode in store.list_workspace_files(ref):
        content = store.read_workspace_file(ref, path)
        if content is None:
            continue
        expected_paths.add(path)
        candidate = root / path
        if not candidate.is_file() or candidate.is_symlink():
            raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} is missing {path!r}.")
        if candidate.read_bytes() != content:
            raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} has stale content at {path!r}.")
        if stat.S_IMODE(candidate.stat().st_mode) != stat.S_IMODE(mode):
            raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} has stale mode at {path!r}.")
        expected_count += 1

    actual_paths = {candidate.relative_to(root).as_posix() for candidate in root.rglob("*") if not candidate.is_dir()}
    if actual_paths != expected_paths:
        raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} contains unexpected files.")
    if metadata.get("file_count") != expected_count:
        raise RuntimeError(f"Workspace snapshot cache for tree {tree_oid} has invalid file count metadata.")
    return expected_count


def render_workspace_snapshot(
    store: Store,
    ref: str,
    *,
    snapshots_root: Path | None = None,
) -> WorkspaceSnapshot:
    """Render the workspace tree at ref into `.vcscore/runtime/snapshots`.

    The metadata sidecar intentionally lives beside `root/`, not under it, so
    overlay lowerdirs expose only workspace files.
    """
    tree_oid = store._get_workspace_tree_oid(ref)
    if tree_oid is None:
        raise ValueError(f"Cannot resolve workspace tree for {ref!r}.")

    root_parent = (snapshots_root or _default_snapshots_root(store)) / str(tree_oid)
    root = root_parent / "root"
    metadata_path = root_parent / "metadata.json"
    if root_parent.exists():
        file_count = _validate_snapshot(store, ref, root=root, metadata_path=metadata_path, tree_oid=str(tree_oid))
        return WorkspaceSnapshot(root=root, metadata_path=metadata_path, tree_oid=str(tree_oid), file_count=file_count)

    snapshots_base = root_parent.parent
    snapshots_base.mkdir(parents=True, exist_ok=True)
    temp_parent = snapshots_base / f".tmp-{tree_oid}-{uuid.uuid4().hex}"
    temp_root = temp_parent / "root"
    temp_metadata = temp_parent / "metadata.json"
    temp_root.mkdir(parents=True, exist_ok=False)
    try:
        file_count = _write_snapshot_contents(store, ref, temp_root)
        temp_metadata.write_text(
            json.dumps(_metadata_payload(ref=ref, tree_oid=str(tree_oid), file_count=file_count), sort_keys=True)
        )
        try:
            temp_parent.rename(root_parent)
        except FileExistsError:
            shutil.rmtree(temp_parent, ignore_errors=True)
            file_count = _validate_snapshot(store, ref, root=root, metadata_path=metadata_path, tree_oid=str(tree_oid))
    except Exception:
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise

    return WorkspaceSnapshot(
        root=root,
        metadata_path=metadata_path,
        tree_oid=str(tree_oid),
        file_count=file_count,
    )
