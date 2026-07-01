"""Durable seal handoff records for retained child scopes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import lookup_path, require_blob
from vcs_core._world_operation_builder import PreparedCandidateTupleRecord
from vcs_core._world_refs import encode_ref_component
from vcs_core.git_store import build_tree, create_commit_with_recovery, create_or_update_reference, create_signature
from vcs_core.types import SealCandidateHandoff

if TYPE_CHECKING:
    from vcs_core.store import Store
    from vcs_core.types import ScopeInfo

SEAL_HANDOFF_SCHEMA = "vcscore/seal-handoff/v1"
SEAL_HANDOFF_PATH = "meta/seal-handoff.json"
SEAL_CANDIDATE_TUPLE_PATH = "data/prepared-candidate-tuple.json"


@dataclass(frozen=True)
class LoadedSealHandoff:
    """A loaded seal handoff plus its full prepared candidate tuple."""

    handoff: SealCandidateHandoff
    candidate_tuple: PreparedCandidateTupleRecord


def seal_handoff_ref(scope: ScopeInfo | str, instance_id: str | None = None) -> str:
    """Return the deterministic handoff ref for one scope identity."""
    if isinstance(scope, str):
        if instance_id is None:
            raise ValueError("instance_id is required when scope is a name")
        name = scope
    else:
        name = scope.name
        instance_id = scope.instance_id
    return f"refs/vcscore/seals/{encode_ref_component(name)}/{encode_ref_component(instance_id)}"


def write_seal_handoff(
    store: Store,
    *,
    handoff: SealCandidateHandoff,
    candidate_tuple: PreparedCandidateTupleRecord,
) -> LoadedSealHandoff:
    """Persist a seal handoff, or return the existing identical handoff."""
    _validate_handoff_matches_tuple(handoff, candidate_tuple)
    expected_ref = seal_handoff_ref(handoff.scope_name, handoff.scope_instance_id)
    if handoff.handoff_ref != expected_ref:
        raise InvalidRepositoryStateError("seal handoff ref disagrees with scope identity")

    existing = read_seal_handoff(store, handoff.scope_name, handoff.scope_instance_id, missing_ok=True)
    if existing is not None:
        if existing.handoff != handoff or existing.candidate_tuple != candidate_tuple:
            raise InvalidRepositoryStateError(f"seal handoff already exists for {handoff.scope_name!r}")
        return existing

    repo = store._repo
    payload = _handoff_to_json(handoff)
    tuple_payload = candidate_tuple.to_json()
    tree_oid = build_tree(
        repo,
        None,
        [
            (SEAL_HANDOFF_PATH, json.dumps(payload, sort_keys=True).encode("utf-8")),
            (SEAL_CANDIDATE_TUPLE_PATH, json.dumps(tuple_payload, sort_keys=True).encode("utf-8")),
        ],
    )
    sig = create_signature("seal")
    oid = create_commit_with_recovery(
        repo,
        None,
        sig,
        sig,
        f"seal:{handoff.scope_name}",
        tree_oid,
        [],
    )
    create_or_update_reference(repo, handoff.handoff_ref, oid)
    return LoadedSealHandoff(handoff=handoff, candidate_tuple=candidate_tuple)


def read_seal_handoff(
    store: Store,
    scope: ScopeInfo | str,
    instance_id: str | None = None,
    *,
    missing_ok: bool = False,
) -> LoadedSealHandoff | None:
    """Load a seal handoff by retained scope identity."""
    ref = seal_handoff_ref(scope, instance_id)
    repo = store._repo
    if ref not in repo.references:
        if missing_ok:
            return None
        raise InvalidRepositoryStateError(f"seal handoff ref is missing: {ref}")
    commit = repo.references[ref].peel(pygit2.Commit)
    handoff_payload = _read_json_blob(repo, commit.tree, SEAL_HANDOFF_PATH)
    tuple_payload = _read_json_blob(repo, commit.tree, SEAL_CANDIDATE_TUPLE_PATH)
    handoff = _handoff_from_json(handoff_payload)
    candidate_tuple = PreparedCandidateTupleRecord.from_json(tuple_payload)
    _validate_handoff_matches_tuple(handoff, candidate_tuple)
    expected_ref = seal_handoff_ref(handoff.scope_name, handoff.scope_instance_id)
    if expected_ref != ref or handoff.handoff_ref != ref:
        raise InvalidRepositoryStateError("seal handoff identity disagrees with ref")
    return LoadedSealHandoff(handoff=handoff, candidate_tuple=candidate_tuple)


def _validate_handoff_matches_tuple(
    handoff: SealCandidateHandoff,
    candidate_tuple: PreparedCandidateTupleRecord,
) -> None:
    candidate = candidate_tuple.candidate
    if handoff.producer_operation_id != candidate.operation_id:
        raise InvalidRepositoryStateError("seal handoff producer_operation_id disagrees with candidate")
    if handoff.binding != candidate.binding:
        raise InvalidRepositoryStateError("seal handoff binding disagrees with candidate")
    if handoff.store_id != candidate.store_id:
        raise InvalidRepositoryStateError("seal handoff store_id disagrees with candidate")
    if handoff.resource_id != candidate.resource_id:
        raise InvalidRepositoryStateError("seal handoff resource_id disagrees with candidate")
    if handoff.candidate_id != candidate.candidate_id:
        raise InvalidRepositoryStateError("seal handoff candidate_id disagrees with candidate")
    if handoff.candidate_ref != candidate.ref:
        raise InvalidRepositoryStateError("seal handoff candidate_ref disagrees with candidate")
    if handoff.candidate_head != candidate.head:
        raise InvalidRepositoryStateError("seal handoff candidate_head disagrees with candidate")
    if handoff.candidate_tuple_digest != candidate_tuple.tuple_digest():
        raise InvalidRepositoryStateError("seal handoff candidate_tuple_digest disagrees with tuple")


def _handoff_to_json(handoff: SealCandidateHandoff) -> dict[str, object]:
    return {
        "schema": SEAL_HANDOFF_SCHEMA,
        "seal_operation_id": handoff.seal_operation_id,
        "producer_operation_id": handoff.producer_operation_id,
        "scope_name": handoff.scope_name,
        "scope_ref": handoff.scope_ref,
        "scope_instance_id": handoff.scope_instance_id,
        "scope_world_id": handoff.scope_world_id,
        "parent_ref": handoff.parent_ref,
        "parent_basis_world_oid": handoff.parent_basis_world_oid,
        "output_world_oid": handoff.output_world_oid,
        "binding": handoff.binding,
        "store_id": handoff.store_id,
        "resource_id": handoff.resource_id,
        "candidate_id": handoff.candidate_id,
        "candidate_ref": handoff.candidate_ref,
        "candidate_head": handoff.candidate_head,
        "candidate_tuple_digest": handoff.candidate_tuple_digest,
        "handoff_ref": handoff.handoff_ref,
        "changed_paths": list(handoff.changed_paths),
    }


def _handoff_from_json(value: dict[str, object]) -> SealCandidateHandoff:
    expected = {
        "schema",
        "seal_operation_id",
        "producer_operation_id",
        "scope_name",
        "scope_ref",
        "scope_instance_id",
        "scope_world_id",
        "parent_ref",
        "parent_basis_world_oid",
        "output_world_oid",
        "binding",
        "store_id",
        "resource_id",
        "candidate_id",
        "candidate_ref",
        "candidate_head",
        "candidate_tuple_digest",
        "handoff_ref",
        "changed_paths",
    }
    extra = set(value) - expected
    if extra:
        raise InvalidRepositoryStateError(f"unexpected seal handoff fields: {sorted(extra)!r}")
    if value.get("schema") != SEAL_HANDOFF_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported seal handoff schema: {value.get('schema')!r}")
    changed_paths = value.get("changed_paths", [])
    if not isinstance(changed_paths, list) or not all(isinstance(path, str) for path in changed_paths):
        raise InvalidRepositoryStateError("seal handoff changed_paths must be a string list")
    return SealCandidateHandoff(
        seal_operation_id=_required_str(value, "seal_operation_id"),
        producer_operation_id=_required_str(value, "producer_operation_id"),
        scope_name=_required_str(value, "scope_name"),
        scope_ref=_required_str(value, "scope_ref"),
        scope_instance_id=_required_str(value, "scope_instance_id"),
        scope_world_id=_optional_str(value, "scope_world_id"),
        parent_ref=_required_str(value, "parent_ref"),
        parent_basis_world_oid=_required_str(value, "parent_basis_world_oid"),
        output_world_oid=_required_str(value, "output_world_oid"),
        binding=_required_str(value, "binding"),
        store_id=_required_str(value, "store_id"),
        resource_id=_required_str(value, "resource_id"),
        candidate_id=_required_str(value, "candidate_id"),
        candidate_ref=_required_str(value, "candidate_ref"),
        candidate_head=_required_str(value, "candidate_head"),
        candidate_tuple_digest=_required_str(value, "candidate_tuple_digest"),
        handoff_ref=_required_str(value, "handoff_ref"),
        changed_paths=tuple(changed_paths),
    )


def _read_json_blob(repo: pygit2.Repository, tree: pygit2.Tree, path: str) -> dict[str, Any]:
    obj = lookup_path(repo, tree, path)
    if obj is None:
        raise InvalidRepositoryStateError(f"seal handoff is missing {path}")
    blob = require_blob(repo, obj.id, context=path)
    try:
        value = json.loads(bytes(blob.data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRepositoryStateError(f"seal handoff {path} is malformed JSON") from exc
    if not isinstance(value, dict):
        raise InvalidRepositoryStateError(f"seal handoff {path} must be a JSON object")
    return value


def _required_str(value: dict[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise InvalidRepositoryStateError(f"seal handoff field {field!r} is required")
    return item


def _optional_str(value: dict[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise InvalidRepositoryStateError(f"seal handoff field {field!r} must be a string when present")
    return item
