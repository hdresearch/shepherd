"""Internal helpers for reconstructing logical operation histories."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import require_commit, topological_commits
from vcs_core.git_store import read_effect_json
from vcs_core.types import CommitInfo

_TERMINAL_PHASES = {"completed", "aborted"}
_NON_TERMINAL_PHASES = {"effect", "started"}
_ALLOWED_PHASES = _TERMINAL_PHASES | _NON_TERMINAL_PHASES


@dataclass(frozen=True)
class ProjectedOperation:
    """Internal projected operation history derived from one anchor commit."""

    operation_id: str
    label: str | None
    kind: str
    world_id: str
    world_ref: str
    parent_operation_id: str | None
    world_disposition: str | None
    nested_parent_scope_ref: str | None
    nested_child_scope_ref: str | None
    nested_ancestry_chain: tuple[str, ...]
    phase: str
    result: str | None
    effect_count: int
    started_at: float | None
    closed_at: float | None
    anchor_oid: str
    commits: tuple[CommitInfo, ...]


def operation_id_from_metadata(metadata: dict[str, Any]) -> str | None:
    """Return the durable operation id from reserved metadata when present."""
    mg = metadata.get("mg")
    if isinstance(mg, dict):
        operation = mg.get("operation")
        if isinstance(operation, dict):
            operation_id = operation.get("id")
            if isinstance(operation_id, str) and operation_id:
                return operation_id
    return None


def require_pointer_epoch_metadata(metadata: dict[str, Any], *, context: str) -> None:
    """Reject operation history written before the pointer-linked epoch."""
    if pointer_metadata_available(metadata):
        return
    raise InvalidRepositoryStateError(
        "Unsupported pre-cutover execution history in "
        f"{context}: operation queries now require pointer-linked mg metadata."
    )


def pointer_metadata_available(metadata: dict[str, Any]) -> bool:
    """True when the metadata carries the reserved pointer-linked shape."""
    mg = metadata.get("mg")
    if not isinstance(mg, dict):
        return False
    operation = mg.get("operation")
    return (
        isinstance(operation, dict) and isinstance(operation.get("id"), str) and isinstance(operation.get("phase"), str)
    )


def find_latest_matching_anchor(
    repo: pygit2.Repository,
    ref: str,
    *,
    expected_operation_id: str,
    terminal_only: bool,
) -> pygit2.Commit | None:
    """Return the newest matching commit for one logical operation on ref."""
    tip = repo.references[ref].peel(pygit2.Commit)
    for commit in topological_commits(repo, tip.id):
        metadata = read_effect_json(repo, commit)
        operation_id = operation_id_from_metadata(metadata)
        if operation_id != expected_operation_id:
            continue

        if terminal_only:
            if pointer_metadata_available(metadata):
                if _phase_from_metadata(metadata) != "completed":
                    continue
            elif metadata.get("type") != "OperationCompleted":
                continue
        return commit
    return None


def derive_status(*, phase: str, result: str | None) -> str:
    """Map durable lifecycle phase metadata to the public summary status."""
    if phase in _NON_TERMINAL_PHASES:
        return "open"
    if phase == "completed":
        return "ok" if result in (None, "ok") else "error"
    if phase == "aborted":
        return "error"
    raise InvalidRepositoryStateError(f"Invalid repository state: unknown operation phase {phase!r}.")


def project_pointer_history(
    repo: pygit2.Repository,
    anchor_commit: pygit2.Commit,
) -> ProjectedOperation:
    """Project one logical operation by following mg.operation.prev_oid."""
    anchor_metadata = read_effect_json(repo, anchor_commit)
    operation_id = operation_id_from_metadata(anchor_metadata)
    if operation_id is None:
        raise InvalidRepositoryStateError(
            f"Invalid repository state: pointer-linked anchor {anchor_commit.id} is missing mg.operation.id."
        )

    anchor_phase = _phase_from_metadata(anchor_metadata)
    if anchor_phase not in _ALLOWED_PHASES:
        raise InvalidRepositoryStateError(
            f"Invalid repository state: anchor {anchor_commit.id} has unsupported phase {anchor_phase!r}."
        )

    expected_seq = _required_int(anchor_metadata, ("mg", "operation", "seq"), anchor_commit)
    stable_world_id = _required_str(anchor_metadata, ("mg", "world", "id"), anchor_commit)
    stable_world_ref = _required_str(anchor_metadata, ("mg", "world", "ref"), anchor_commit)
    stable_world_instance_id = _required_str(
        anchor_metadata,
        ("mg", "world", "instance_id"),
        anchor_commit,
    )
    stable_parent_id = _optional_str(anchor_metadata, ("mg", "operation", "parent_id"))
    stable_world_disposition = _optional_world_disposition(anchor_metadata, anchor_commit)
    stable_nested_edge = _optional_nested_edge(anchor_metadata, anchor_commit)

    commits: list[CommitInfo] = []
    visited: set[str] = set()
    effect_count = 0
    commit = anchor_commit
    first_commit = True

    while True:
        oid = str(commit.id)
        if oid in visited:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: pointer-linked operation {operation_id!r} contains a prev_oid cycle."
            )
        visited.add(oid)

        metadata = read_effect_json(repo, commit)
        require_pointer_epoch_metadata(
            metadata,
            context=f"pointer-linked operation {operation_id!r} on commit {commit.id}",
        )
        current_operation_id = operation_id_from_metadata(metadata)
        if current_operation_id != operation_id:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} hops to commit {commit.id} owned by {current_operation_id!r}."
            )

        current_world_id = _required_str(metadata, ("mg", "world", "id"), commit)
        if current_world_id != stable_world_id:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} changes mg.world.id across its history."
            )

        current_world_ref = _required_str(metadata, ("mg", "world", "ref"), commit)
        if current_world_ref != stable_world_ref:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} changes mg.world.ref across its history."
            )

        current_world_instance_id = _required_str(metadata, ("mg", "world", "instance_id"), commit)
        if current_world_instance_id != stable_world_instance_id:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} changes mg.world.instance_id across its history."
            )

        current_parent_id = _optional_str(metadata, ("mg", "operation", "parent_id"))
        if current_parent_id != stable_parent_id:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} changes mg.operation.parent_id across its history."
            )

        current_world_disposition = _optional_world_disposition(metadata, commit)
        if current_world_disposition != stable_world_disposition:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} changes mg.operation.world_disposition across its history."
            )

        current_nested_edge = _optional_nested_edge(metadata, commit)
        if current_nested_edge != stable_nested_edge:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} changes mg.operation.nested across its history."
            )

        phase = _phase_from_metadata(metadata)
        if phase not in _ALLOWED_PHASES:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: pointer-linked operation {operation_id!r} has unsupported phase {phase!r}."
            )

        seq = _required_int(metadata, ("mg", "operation", "seq"), commit)
        if seq != expected_seq:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} has non-contiguous seq values (expected {expected_seq}, found {seq})."
            )

        if first_commit:
            first_commit = False
        elif phase in _TERMINAL_PHASES:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} contains a terminal phase before its anchor boundary."
            )

        prev_oid = _optional_str(metadata, ("mg", "operation", "prev_oid"))
        if prev_oid is None:
            if phase != "started" or seq != 0:
                raise InvalidRepositoryStateError(
                    "Invalid repository state: pointer-linked operation "
                    f"{operation_id!r} terminates on a non-start commit."
                )
        elif phase == "started":
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} contains a started commit before the end of the chain."
            )

        commits.append(
            CommitInfo(
                oid=oid,
                message=commit.message.strip(),
                timestamp=commit.commit_time,
                metadata=metadata,
                parent_oids=[str(parent_oid) for parent_oid in commit.parent_ids],
            )
        )
        if phase == "effect":
            effect_count += 1

        if prev_oid is None:
            break

        try:
            next_commit = require_commit(repo, pygit2.Oid(hex=prev_oid), context=f"prev_oid {prev_oid}")
        except (KeyError, TypeError) as exc:
            raise InvalidRepositoryStateError(
                "Invalid repository state: pointer-linked operation "
                f"{operation_id!r} references missing prev_oid {prev_oid!r}."
            ) from exc
        commit = next_commit
        expected_seq -= 1

    anchor_effect_count = _required_int(anchor_metadata, ("mg", "operation", "effect_count"), anchor_commit)
    if anchor_effect_count != effect_count:
        raise InvalidRepositoryStateError(
            "Invalid repository state: pointer-linked operation "
            f"{operation_id!r} reports effect_count={anchor_effect_count}, but projects {effect_count} effect commit(s)."
        )

    return ProjectedOperation(
        operation_id=operation_id,
        label=_optional_str(anchor_metadata, ("mg", "operation", "label")),
        kind=_required_str(anchor_metadata, ("mg", "operation", "kind"), anchor_commit),
        world_id=stable_world_id,
        world_ref=stable_world_ref,
        parent_operation_id=stable_parent_id,
        world_disposition=stable_world_disposition,
        nested_parent_scope_ref=None if stable_nested_edge is None else stable_nested_edge[0],
        nested_child_scope_ref=None if stable_nested_edge is None else stable_nested_edge[1],
        nested_ancestry_chain=() if stable_nested_edge is None else stable_nested_edge[2],
        phase=anchor_phase,
        result=_optional_str(anchor_metadata, ("mg", "operation", "result")),
        effect_count=effect_count,
        started_at=_optional_float(anchor_metadata, ("mg", "operation", "started_at")),
        closed_at=_optional_float(anchor_metadata, ("mg", "operation", "closed_at")),
        anchor_oid=str(anchor_commit.id),
        commits=tuple(commits),
    )


def _phase_from_metadata(metadata: dict[str, Any]) -> str:
    phase = _optional_str(metadata, ("mg", "operation", "phase"))
    if phase is not None:
        return phase
    effect_type = metadata.get("type")
    if effect_type == "OperationStarted":
        return "started"
    if effect_type == "OperationCompleted":
        return "completed"
    if effect_type == "OperationAborted":
        return "aborted"
    return "effect"


def _required_int(metadata: dict[str, Any], path: tuple[str, ...], commit: pygit2.Commit) -> int:
    value = _walk(metadata, path)
    if isinstance(value, int):
        return value
    raise InvalidRepositoryStateError(
        f"Invalid repository state: commit {commit.id} is missing integer metadata at {'.'.join(path)!r}."
    )


def _required_str(metadata: dict[str, Any], path: tuple[str, ...], commit: pygit2.Commit) -> str:
    value = _walk(metadata, path)
    if isinstance(value, str):
        return value
    raise InvalidRepositoryStateError(
        f"Invalid repository state: commit {commit.id} is missing string metadata at {'.'.join(path)!r}."
    )


def _optional_str(metadata: dict[str, Any], path: tuple[str, ...]) -> str | None:
    value = _walk(metadata, path)
    return value if isinstance(value, str) else None


def _optional_float(metadata: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value = _walk(metadata, path)
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_world_disposition(metadata: dict[str, Any], commit: pygit2.Commit) -> str | None:
    value = _walk(metadata, ("mg", "operation", "world_disposition"))
    if value is None:
        return None
    if value in {"adopt", "release"}:
        return str(value)
    raise InvalidRepositoryStateError(
        f"Invalid repository state: commit {commit.id} has invalid mg.operation.world_disposition."
    )


def _optional_nested_edge(metadata: dict[str, Any], commit: pygit2.Commit) -> tuple[str, str, tuple[str, ...]] | None:
    value = _walk(metadata, ("mg", "operation", "nested"))
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidRepositoryStateError(
            f"Invalid repository state: commit {commit.id} has invalid mg.operation.nested."
        )
    parent_ref = value.get("parent_scope_ref")
    child_ref = value.get("child_scope_ref")
    chain = value.get("ancestry_chain")
    if (
        not isinstance(parent_ref, str)
        or not parent_ref
        or not isinstance(child_ref, str)
        or not child_ref
        or not isinstance(chain, list)
        or not chain
        or not all(isinstance(item, str) and item for item in chain)
    ):
        raise InvalidRepositoryStateError(
            f"Invalid repository state: commit {commit.id} has invalid mg.operation.nested."
        )
    return parent_ref, child_ref, tuple(chain)


def _walk(metadata: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = metadata
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
