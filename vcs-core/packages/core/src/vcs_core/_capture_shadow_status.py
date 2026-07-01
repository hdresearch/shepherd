"""Read-only capture shadow status queries."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from vcs_core._capture_reducer import CAPTURE_REDUCTION_KIND, reduction_operation_id
from vcs_core._world_refs import candidate_ref
from vcs_core._world_storage_installation import (
    DEFAULT_WORKSPACE_STORE_ID,
    default_world_storage_exists,
    open_existing_default_world_storage,
)
from vcs_core._world_types import canonical_digest

if TYPE_CHECKING:
    from pathlib import Path

    from vcs_core.types import OperationHistory

SESSION_EXEC_KIND = "vcs_core.session_exec"


def capture_shadow_status_for_history(repo_path: str | Path, history: OperationHistory) -> dict[str, object] | None:
    """Return read-only capture shadow status for operation history, when relevant."""
    if history.summary.kind == SESSION_EXEC_KIND:
        if not _history_requested_capture(history):
            return None
        reducer_id = reduction_operation_id(history.summary.operation_id)
    elif history.summary.kind == CAPTURE_REDUCTION_KIND:
        reducer_id = history.summary.operation_id
    else:
        return None
    status = capture_shadow_status(repo_path, reducer_id=reducer_id, authority_ref=history.summary.world_ref)
    if (
        status.get("state") == "candidate_only"
        and history.summary.visibility == "visible"
        and history.summary.carrier_ref != history.summary.world_ref
    ):
        return capture_shadow_status(repo_path, reducer_id=reducer_id, authority_ref=history.summary.carrier_ref)
    return status


def capture_shadow_status(
    repo_path: str | Path, *, reducer_id: str, authority_ref: str | None = None
) -> dict[str, object]:
    """Return capture shadow status without initializing world-vector storage."""
    if not default_world_storage_exists(repo_path):
        return {"state": "absent"}
    try:
        manager = open_existing_default_world_storage(repo_path)
        workspace_store = manager.store(DEFAULT_WORKSPACE_STORE_ID)
        ref = candidate_ref(reducer_id, "workspace")
        if ref not in workspace_store.repo.references:
            return {"state": "absent"}
        head = str(workspace_store.repo.references[ref].target)
        provenance = workspace_store.validate_prepared_candidate(
            head,
            evidence_resolver=manager.world_store.resolve_evidence_ref,
        )
        payload = _read_revision_payload(workspace_store.repo, head)
        manifest = payload.get("state_manifest")
        if not isinstance(manifest, dict):
            return {"state": "invalid", "candidate_head": head, "validation_error": "missing state_manifest"}
        records = tuple(manager.world_store.resolve_evidence_ref(ref) for ref in provenance.preparation.evidence_refs)
        selected_head = None
        authority_world_oid = None
        if authority_ref is not None and authority_ref in manager.world_store.repo.references:
            authority_world_oid = str(manager.world_store.repo.references[authority_ref].target)
            world = manager.read_world(authority_ref)
            try:
                selected_head = world.snapshot.head_for("workspace").head
            except KeyError:
                selected_head = None
        if selected_head is None:
            discovered = _find_authority_selecting_workspace_head(manager, head)
            if discovered is not None:
                authority_ref, authority_world_oid, selected_head = discovered
    except Exception as exc:  # noqa: BLE001
        return {"state": "invalid", "validation_error": str(exc) or exc.__class__.__name__}

    raw_count = sum(1 for record in records if record.evidence_kind == "capture:filesystem-event")
    proof_count = sum(1 for record in records if record.evidence_kind == "reduce:reduced-state-proof")
    diagnostic_count = sum(1 for record in records if record.evidence_kind.startswith("diagnostic:"))
    if diagnostic_count:
        state = "diagnostic"
    elif selected_head == head:
        state = "selected"
    elif selected_head is None:
        state = "candidate_only"
    else:
        state = "stale"
    return {
        "state": state,
        "binding": "workspace",
        "authority_ref": authority_ref,
        "authority_world_oid": authority_world_oid,
        "selected_head": selected_head,
        "candidate_head": head,
        "manifest_digest": canonical_digest(manifest),
        "raw_evidence_count": raw_count,
        "proof_evidence_count": proof_count,
        "diagnostic_evidence_count": diagnostic_count,
    }


def _history_requested_capture(history: OperationHistory) -> bool:
    for commit in history.commits:
        metadata = commit.metadata
        command = metadata.get("command")
        if not isinstance(command, dict):
            continue
        if command.get("capture_requested") is True or "capture_status" in command:
            return True
    return False


def _read_revision_payload(repo: Any, head: str) -> dict[str, object]:
    commit = repo[head]
    blob = repo[commit.tree["revision.json"].id]
    payload = json.loads(bytes(blob.data).decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("workspace revision payload must be an object")
    return payload


def _find_authority_selecting_workspace_head(manager: Any, head: str) -> tuple[str, str, str] | None:
    authority_refs = sorted(
        ref
        for ref in manager.world_store.repo.references
        if ref == "refs/vcscore/ground" or ref.startswith("refs/vcscore/scopes/")
    )
    for ref in authority_refs:
        world_oid = str(manager.world_store.repo.references[ref].target)
        world = manager.read_world(world_oid)
        try:
            selected_head = world.snapshot.head_for("workspace").head
        except KeyError:
            continue
        if selected_head == head:
            return ref, world_oid, selected_head
    return None
