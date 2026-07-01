"""Projection from vcs-core's current Git carrier into commons-vcs Objects."""

from __future__ import annotations

import base64
import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pygit2
from commons_vcs import Edge, Object, Repo

from vcs_core.git_store import read_effect_json

if TYPE_CHECKING:
    from vcs_core.types import ScopeInfo

_CARRIER_LOCAL_EFFECT_KEYS = frozenset(
    {
        "type",
        "substrate",
        "scope",
        "timestamp",
        "world_id",
        "scope_instance_id",
    }
)
_STATUS_BY_DELTA = {
    pygit2.GIT_DELTA_ADDED: "added",
    pygit2.GIT_DELTA_MODIFIED: "modified",
    pygit2.GIT_DELTA_DELETED: "deleted",
}
_SUPPORTED_FILEMODES = {
    pygit2.GIT_FILEMODE_BLOB,
    pygit2.GIT_FILEMODE_BLOB_EXECUTABLE,
}
_GROUND_REF = "refs/vcscore/ground"


@dataclass(frozen=True)
class ProjectedCarrierCommit:
    """Digest mapping for one projected vcs-core carrier commit."""

    carrier_oid: str
    effect_id: str
    commit_id: str


@dataclass(frozen=True)
class ProjectedScopeHistory:
    """Result of shadow-appending one vcs-core scope history."""

    scope_id: str
    entries: tuple[ProjectedCarrierCommit, ...]

    @property
    def head_id(self) -> str | None:
        if not self.entries:
            return None
        return self.entries[-1].commit_id


def append_projected_scope_history(
    commons_repo: Repo,
    git_repo: pygit2.Repository,
    *,
    scope: ScopeInfo,
    head_oid: str,
) -> ProjectedScopeHistory:
    """Append a scope's current carrier history into a commons-vcs Repo.

    The current vcs-core carrier remains authoritative. This helper is for
    shadow verification: it follows the carrier's first-parent chain from
    `head_oid` back to `scope.creation_oid` and projects only commits created
    inside that scope.
    """
    scope_id = commons_repo.append(project_scope_object(scope))
    parent_id: str | None = None
    entries: list[ProjectedCarrierCommit] = []
    for carrier_commit in _scope_commits_oldest_first(git_repo, head_oid, scope=scope):
        effect_id = commons_repo.append(project_effect_object(git_repo, carrier_commit))
        commit_id = commons_repo.append(
            project_commit_object(
                git_repo,
                carrier_commit,
                effect_id=effect_id,
                scope_id=scope_id,
                parent_id=parent_id,
            )
        )
        entries.append(
            ProjectedCarrierCommit(
                carrier_oid=str(carrier_commit.id),
                effect_id=effect_id,
                commit_id=commit_id,
            )
        )
        parent_id = commit_id
    return ProjectedScopeHistory(scope_id=scope_id, entries=tuple(entries))


def project_scope_object(scope: ScopeInfo) -> Object:
    """Project a durable vcs-core scope identity into commons-vcs."""
    if scope.world_id is None:
        raise ValueError(f"scope {scope.ref!r} is missing durable world_id")
    return Object(
        schema_ref="vcscore/scope/v1",
        body={
            "name": scope.name,
            "world_id": scope.world_id,
            "scope_instance_id": scope.instance_id,
        },
    )


def project_effect_object(repo: pygit2.Repository, commit: pygit2.Commit) -> Object:
    """Project one real vcs-core carrier commit's effect metadata."""
    metadata = read_effect_json(repo, commit)
    effect_type = metadata.get("type")
    substrate = metadata.get("substrate")
    if not isinstance(effect_type, str) or not effect_type:
        raise ValueError(f"commit {commit.id} is missing effect type metadata")
    if not isinstance(substrate, str) or not substrate:
        raise ValueError(f"commit {commit.id} is missing substrate metadata")

    payload = _identity_payload(metadata)
    body: dict[str, Any] = {
        "effect_type": effect_type,
        "substrate": substrate,
        "payload": payload,
    }
    changes = _workspace_changes(repo, commit)
    if changes:
        body["workspace_changes"] = changes
    return Object(schema_ref="vcscore/effect/v1", body=body)


def project_commit_object(
    repo: pygit2.Repository,
    commit: pygit2.Commit,
    *,
    effect_id: str,
    scope_id: str,
    parent_id: str | None = None,
) -> Object:
    """Project one vcs-core carrier commit into a commons-vcs commit Object."""
    edges = [Edge("effect", effect_id), Edge("scope", scope_id)]
    if parent_id is not None:
        edges.append(Edge("parent", parent_id))
    workspace_tree = _workspace_tree_oid(commit)
    return Object(
        schema_ref="vcscore/commit/v1",
        body={
            "workspace_tree": workspace_tree,
            "git_object_format": _git_object_format(workspace_tree),
        },
        edges=tuple(edges),
    )


def _canonical_project(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("vcs-core metadata floats must be finite")
        return {"__type__": "float", "value": format(value, ".17g")}
    if isinstance(value, bytes):
        return {
            "__type__": "bytes",
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, Mapping):
        items: list[tuple[str, object]] = []
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"cannot project mapping key {key!r}; keys must be strings")
            items.append((key, child))
        return {key: _canonical_project(child) for key, child in sorted(items)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview)):
        return [_canonical_project(child) for child in value]
    raise TypeError(f"cannot project {type(value).__name__} into commons-vcs JSON")


def _identity_payload(metadata: Mapping[Any, object]) -> dict[str, object]:
    payload_items: list[tuple[str, object]] = []
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise TypeError(f"cannot project effect metadata key {key!r}; keys must be strings")
        if key in _CARRIER_LOCAL_EFFECT_KEYS:
            continue
        payload_items.append((key, value))
    return {key: _canonical_project(value) for key, value in sorted(payload_items)}


def _workspace_tree_oid(commit: pygit2.Commit) -> str:
    entry = commit.tree["workspace"]
    return str(entry.id)


def _git_object_format(oid_hex: str) -> str:
    if len(oid_hex) == 40:
        return "sha1"
    if len(oid_hex) == 64:
        return "sha256"
    raise ValueError(f"unsupported Git object id length: {oid_hex!r}")


def _scope_commits_oldest_first(
    repo: pygit2.Repository,
    head_oid: str,
    *,
    scope: ScopeInfo,
) -> list[pygit2.Commit]:
    commits: list[pygit2.Commit] = []
    cursor_oid = head_oid
    seen: set[str] = set()
    while not is_scope_creation_boundary(repo, scope, cursor_oid):
        if cursor_oid in seen:
            raise ValueError(f"cycle in carrier first-parent chain at {cursor_oid}")
        seen.add(cursor_oid)
        obj = repo[pygit2.Oid(hex=cursor_oid)]
        if not isinstance(obj, pygit2.Commit):
            raise TypeError(f"carrier object is not a commit: {cursor_oid}")
        commits.append(obj)
        if not obj.parent_ids:
            raise ValueError(f"carrier history did not reach scope creation boundary for {scope.ref}")
        cursor_oid = str(obj.parent_ids[0])
    commits.reverse()
    return commits


def is_scope_creation_boundary(repo: pygit2.Repository, scope: ScopeInfo, carrier_oid: str) -> bool:
    """Return whether a carrier commit is the non-projected boundary for a scope."""
    if carrier_oid == scope.creation_oid:
        return True
    if scope.creation_oid or scope.ref != _GROUND_REF:
        return False
    obj = repo[pygit2.Oid(hex=carrier_oid)]
    if not isinstance(obj, pygit2.Commit):
        raise TypeError(f"carrier object is not a commit: {carrier_oid}")
    return not obj.parent_ids


def _workspace_changes(repo: pygit2.Repository, commit: pygit2.Commit) -> list[dict[str, str]]:
    if not commit.parent_ids:
        return []
    parent = repo[commit.parent_ids[0]]
    if not isinstance(parent, pygit2.Commit):
        raise TypeError(f"commit {commit.id} has non-commit parent {commit.parent_ids[0]}")
    old_tree = parent.tree["workspace"].id
    new_tree = commit.tree["workspace"].id
    diff = repo.diff(old_tree, new_tree)
    changes: list[dict[str, str]] = []
    for patch in diff:
        if patch is None:
            continue
        delta = patch.delta
        status = _STATUS_BY_DELTA.get(delta.status)
        if status is None:
            raise ValueError(
                f"unsupported Git delta status for {delta.new_file.path or delta.old_file.path}: {delta.status}"
            )
        path = delta.old_file.path if status == "deleted" else delta.new_file.path
        change = {
            "path": str(path),
            "status": status,
        }
        if status != "deleted":
            mode = int(delta.new_file.mode)
            if mode not in _SUPPORTED_FILEMODES:
                raise ValueError(f"unsupported Git filemode for {path}: {mode:o}")
            change["git_filemode"] = f"{mode:o}"
            blob = repo[delta.new_file.id]
            if blob.type != pygit2.GIT_OBJECT_BLOB:
                raise ValueError(f"workspace path {path} does not point at a blob")
            payload = cast("bytes", cast("Any", blob).data)
            change["content_digest"] = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        changes.append(change)
    return sorted(changes, key=lambda item: item["path"])
