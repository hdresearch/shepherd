from __future__ import annotations

import json
import shutil
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pygit2

from vcs_core._errors import RefResolutionError
from vcs_core._pygit2_helpers import (
    lookup_path,
    require_blob,
    require_object,
    require_tree,
    topological_commits,
    tree_entry_filemode,
    tree_entry_id,
)
from vcs_core.git_store import (
    count_between,
    diff_workspace_trees,
    read_effect_json,
    walk_workspace_tree,
)
from vcs_core.types import CommitInfo, DiffSummary, Status

if TYPE_CHECKING:
    from vcs_core.store import Store


def walk_pending(owner: Store, *, max_count: int = 500) -> list[CommitInfo]:
    """Walk commits between materialized and ground (oldest first)."""
    ground = owner._repo.references[owner.GROUND_REF].peel(pygit2.Commit)
    materialized = owner._repo.references[owner.MAT_REF].peel(pygit2.Commit)
    if ground.id == materialized.id:
        return []

    commits: list[CommitInfo] = []
    for commit in topological_commits(owner._repo, ground.id):
        if commit.id == materialized.id:
            break
        commits.append(owner._commit_info(commit))
        if len(commits) >= max_count:
            break
    commits.reverse()
    return commits


def status(owner: Store) -> Status:
    """Materialization status: local changes and commits ahead."""
    ground = owner._repo.references[owner.GROUND_REF].peel(pygit2.Commit)
    materialized = owner._repo.references[owner.MAT_REF].peel(pygit2.Commit)

    local_changes = 0
    workspace_materialized = materialized.tree["workspace"].id
    workspace_ground = ground.tree["workspace"].id
    if workspace_materialized != workspace_ground:
        files = diff_workspace_trees(owner._repo, workspace_materialized, workspace_ground)
        local_changes = len(files)

    commits_ahead = 0
    if ground.id != materialized.id:
        commits_ahead = count_between(owner._repo, materialized, ground)

    return Status(local_changes=local_changes, commits_ahead=commits_ahead)


def log(owner: Store, *, ref: str | None = None, max_count: int = 50) -> list[CommitInfo]:
    """Commit history from the given ref (default: ground)."""
    resolved_ref = ref or owner.GROUND_REF
    tip = owner._repo.references[resolved_ref].peel(pygit2.Commit)
    commits: list[CommitInfo] = []
    for commit in topological_commits(owner._repo, tip.id):
        if len(commits) >= max_count:
            break
        commits.append(owner._commit_info(commit))
    return commits


def diff(owner: Store) -> DiffSummary:
    """File changes between materialized and ground."""
    ground = owner._repo.references[owner.GROUND_REF].peel(pygit2.Commit)
    materialized = owner._repo.references[owner.MAT_REF].peel(pygit2.Commit)
    files = diff_workspace_trees(owner._repo, materialized.tree["workspace"].id, ground.tree["workspace"].id)
    return DiffSummary(files=files)


def filter_effects(
    owner: Store,
    *,
    effect_type: str | None = None,
    substrate: str | None = None,
    ref: str | None = None,
    max_count: int = 100,
    scope: str | None = None,
) -> list[CommitInfo]:
    """Filter commits by effect type, substrate, and/or scope."""
    resolved_ref = ref or owner.GROUND_REF
    tip = owner._repo.references[resolved_ref].peel(pygit2.Commit)
    commits: list[CommitInfo] = []
    for commit in topological_commits(owner._repo, tip.id):
        metadata = read_effect_json(owner._repo, commit)
        if effect_type and metadata.get("type") != effect_type:
            continue
        if substrate and metadata.get("substrate") != substrate:
            continue
        if scope and metadata.get("scope") != scope:
            continue
        commits.append(owner._commit_info(commit))
        if len(commits) >= max_count:
            break
    return commits


def resolve_workspace_entry(owner: Store, ref: str, path: str) -> pygit2.Object | None:
    """Resolve a workspace path at ref to its Git object, if present."""
    try:
        tip = owner._repo.references[ref].peel(pygit2.Commit)
        workspace_tree = require_tree(owner._repo, tip.tree["workspace"].id, context=f"workspace root for {ref}")
    except KeyError:
        return None
    return lookup_path(owner._repo, workspace_tree, path)


def file_exists_in_workspace(owner: Store, ref: str, path: str) -> bool:
    return isinstance(resolve_workspace_entry(owner, ref, path), pygit2.Blob)


def read_workspace_file(owner: Store, ref: str, path: str) -> bytes | None:
    obj = resolve_workspace_entry(owner, ref, path)
    if not isinstance(obj, pygit2.Blob):
        return None
    return bytes(obj.data)


def workspace_file_mode(owner: Store, ref: str, path: str) -> int | None:
    """Return the Git filemode for a workspace file, or None if not found."""
    try:
        tip = owner._repo.references[ref].peel(pygit2.Commit)
        workspace_tree = require_tree(owner._repo, tip.tree["workspace"].id, context=f"workspace root for {ref}")
    except KeyError:
        return None

    parts = path.split("/")
    current_tree = workspace_tree
    for part in parts[:-1]:
        next_tree_oid = tree_entry_id(current_tree, part)
        if next_tree_oid is None:
            return None
        try:
            current_tree = require_tree(owner._repo, next_tree_oid, context=f"workspace directory {path}")
        except (KeyError, TypeError):
            return None

    return tree_entry_filemode(current_tree, parts[-1])


def resolve_to_commit(owner: Store, commitish: str) -> pygit2.Commit | None:
    """Resolve a ref name or hex OID to a commit, or None."""
    try:
        return owner._repo.references[commitish].peel(pygit2.Commit)
    except (KeyError, ValueError):
        pass

    try:
        obj = require_object(owner._repo, pygit2.Oid(hex=commitish))
        if isinstance(obj, pygit2.Commit):
            return obj
    except (ValueError, KeyError):
        pass

    try:
        obj = owner._repo.revparse_single(commitish)
        if isinstance(obj, pygit2.Commit):
            return obj
    except (KeyError, ValueError, pygit2.GitError):
        pass
    return None


def get_workspace_tree_oid(owner: Store, commitish: str) -> pygit2.Oid | None:
    """Resolve a commitish to the workspace tree OID, or None."""
    commit = resolve_to_commit(owner, commitish)
    if commit is None:
        return None
    try:
        return commit.tree["workspace"].id
    except KeyError:
        return None


def list_workspace_files(owner: Store, ref: str) -> list[tuple[str, str, int]]:
    """List all files in the workspace tree at the given ref or OID."""
    commit = resolve_to_commit(owner, ref)
    if commit is None:
        raise RefResolutionError(f"Cannot resolve ref {ref!r} to a commit.")
    try:
        workspace_tree_oid = commit.tree["workspace"].id
    except KeyError:
        return []
    return [(path, str(oid), mode) for path, oid, mode in walk_workspace_tree(owner._repo, workspace_tree_oid)]


def checkout_workspace_tree(owner: Store, ref: str, dest: str) -> int:
    """Extract all workspace files at ref or OID to dest directory."""
    commit = resolve_to_commit(owner, ref)
    if commit is None:
        raise RefResolutionError(f"Cannot resolve ref {ref!r} to a commit.")

    dest_path = Path(dest)
    resolved_dest = dest_path.resolve()
    deny_exact = {
        Path("/"),
        Path.home(),
        Path(owner._repo_path).resolve().parent,
    }
    deny_subtree = {
        Path(owner._repo_path).resolve(),
    }
    if resolved_dest in deny_exact or any(resolved_dest.is_relative_to(path) for path in deny_subtree):
        msg = (
            f"Refusing to use {str(dest_path)!r} as checkout destination: "
            f"resolves to protected path {str(resolved_dest)!r}."
        )
        raise ValueError(msg)

    if dest_path.exists():
        marker = dest_path / owner._CHECKOUT_MARKER
        if not marker.exists():
            msg = (
                f"Refusing to overwrite {str(dest_path)!r}: directory exists "
                f"but was not created by checkout_workspace_tree "
                f"(no {owner._CHECKOUT_MARKER} marker). "
                f"Remove it manually or choose a different destination."
            )
            raise ValueError(msg)
        shutil.rmtree(dest_path)

    dest_path.mkdir(parents=True, exist_ok=True)

    try:
        workspace_tree_oid = commit.tree["workspace"].id
    except KeyError:
        workspace_tree_oid = None

    written = 0
    if workspace_tree_oid is not None:
        for path, oid, mode in walk_workspace_tree(owner._repo, workspace_tree_oid):
            blob = require_blob(owner._repo, oid, context=f"workspace checkout {path}")
            file_dest = dest_path / path
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            file_dest.write_bytes(bytes(blob.data))
            if mode != pygit2.GIT_FILEMODE_BLOB:
                file_dest.chmod(stat.S_IMODE(mode))
            written += 1

    marker_path = dest_path / owner._CHECKOUT_MARKER
    marker_path.write_text(json.dumps({"ref": ref, "extracted_at": time.time()}))
    return written
