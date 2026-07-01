from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygit2

from vcs_core._errors import InvalidRepositoryStateError, StaleScopeError
from vcs_core._operation_projection import (
    derive_status,
    find_latest_matching_anchor,
    pointer_metadata_available,
    project_pointer_history,
    require_pointer_epoch_metadata,
)
from vcs_core._projection_store import (
    ArchivedOperationCandidate,
    ArchivedOperationsByIdSnapshot,
    ProjectionCarrier,
    ProjectionCarrierKind,
    archived_operation_projection_current_head,
    archived_operation_projection_digest,
    archived_operation_projection_frontier,
    archived_operation_projection_is_fresh,
    load_archived_operations_by_id_snapshot,
    publish_archived_operations_by_id_additions,
    publish_archived_operations_by_id_snapshot,
)
from vcs_core._pygit2_helpers import require_commit, topological_commits
from vcs_core.git_store import read_effect_json
from vcs_core.types import ArchivedVia, OperationHistory, OperationSummary, OperationVisibility

if TYPE_CHECKING:
    from vcs_core._runtime_types import OperationRefInfo
    from vcs_core.store import ArchivedOperationMembershipCacheRecord, Store
else:
    from vcs_core._runtime_types import OperationRefInfo


def list_operation_refs(owner: Store) -> list[str]:
    return sorted(ref for ref in owner._repo.references if ref.startswith(f"{owner.OP_REF_PREFIX}/"))


def read_operation_start_commit(owner: Store, ref: str) -> pygit2.Commit:
    tip = owner._repo.references[ref].peel(pygit2.Commit)
    expected_identity = ref.rsplit("/", 1)[-1]
    if ref.startswith("refs/vcscore/archive/ops/"):
        tip_meta = read_effect_json(owner._repo, tip)
        expected_identity = str(owner._operation_id_from_metadata(tip_meta) or expected_identity)
    commit = tip
    while True:
        meta = read_effect_json(owner._repo, commit)
        require_pointer_epoch_metadata(meta, context=f"operation ref {ref!r}")
        current_operation_id = owner._operation_id_from_metadata(meta)
        if meta.get("type") == "OperationStarted" and current_operation_id == expected_identity:
            return commit
        if not commit.parent_ids:
            break
        commit = require_commit(owner._repo, commit.parent_ids[0], context=f"operation start parent for {ref}")
    msg = f"Operation start metadata missing for ref: {ref}"
    raise StaleScopeError(msg)


def read_operation_start_metadata(owner: Store, ref: str) -> dict[str, Any]:
    return read_effect_json(owner._repo, owner._read_operation_start_commit(ref))


def list_open_operations(
    owner: Store,
    *,
    scope_ref: str | None = None,
    session_id: str | None = None,
) -> list[OperationRefInfo]:
    operations: list[OperationRefInfo] = []
    for ref in owner.list_operation_refs():
        start_commit = owner._read_operation_start_commit(ref)
        meta = read_effect_json(owner._repo, start_commit)
        require_pointer_epoch_metadata(meta, context=f"open operation ref {ref!r}")
        mg = meta["mg"]
        world = mg["world"]
        operation = mg["operation"]
        op_scope_ref = world.get("ref")
        if not isinstance(op_scope_ref, str) or not op_scope_ref:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} is missing mg.world.ref."
            )
        owner._validated_world_name_for_ref(op_scope_ref, context=f"open operation ref {ref!r}")
        if scope_ref is not None and op_scope_ref != scope_ref:
            continue
        meta_session_id = mg.get("session_id")
        if meta_session_id is not None and (not isinstance(meta_session_id, str) or not meta_session_id):
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} has invalid mg.session_id."
            )
        if session_id is not None and meta_session_id != session_id:
            continue
        scope_instance_id = world.get("instance_id")
        if not isinstance(scope_instance_id, str) or not scope_instance_id:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} is missing mg.world.instance_id."
            )
        world_id = world.get("id")
        if not isinstance(world_id, str) or not world_id:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} is missing mg.world.id."
            )
        parent_operation_id = operation.get("parent_id")
        if parent_operation_id is not None and (not isinstance(parent_operation_id, str) or not parent_operation_id):
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} has invalid mg.operation.parent_id."
            )
        parent_op_ref = owner.operation_ref(parent_operation_id) if parent_operation_id is not None else None
        world_disposition = operation.get("world_disposition")
        if world_disposition is not None and world_disposition not in {"adopt", "release"}:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} has invalid mg.operation.world_disposition."
            )
        nested_parent_scope_ref = None
        nested_child_scope_ref = None
        nested_ancestry_chain: tuple[str, ...] = ()
        nested = operation.get("nested")
        if nested is not None:
            if not isinstance(nested, dict):
                raise InvalidRepositoryStateError(
                    f"Invalid repository state: open operation ref {ref!r} has invalid mg.operation.nested."
                )
            nested_parent_scope_ref = nested.get("parent_scope_ref")
            nested_child_scope_ref = nested.get("child_scope_ref")
            raw_chain = nested.get("ancestry_chain")
            if (
                not isinstance(nested_parent_scope_ref, str)
                or not nested_parent_scope_ref
                or not isinstance(nested_child_scope_ref, str)
                or not nested_child_scope_ref
                or not isinstance(raw_chain, list)
                or not raw_chain
                or not all(isinstance(item, str) and item for item in raw_chain)
            ):
                raise InvalidRepositoryStateError(
                    f"Invalid repository state: open operation ref {ref!r} has invalid mg.operation.nested."
                )
            nested_ancestry_chain = tuple(raw_chain)
        if not start_commit.parent_ids:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} start commit is missing a base parent."
            )
        base_oid = str(start_commit.parent_ids[0])
        operation_id = operation.get("id")
        if not isinstance(operation_id, str) or not operation_id:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} is missing mg.operation.id."
            )
        operation_label = operation.get("label")
        if operation_label is not None and (not isinstance(operation_label, str) or not operation_label):
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} has invalid mg.operation.label."
            )
        operation_kind = operation.get("kind")
        if not isinstance(operation_kind, str) or not operation_kind:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: open operation ref {ref!r} is missing mg.operation.kind."
            )
        operations.append(
            OperationRefInfo(
                handle_id=operation_id,
                kind=operation_kind,
                ref=ref,
                scope_ref=op_scope_ref,
                scope_instance_id=scope_instance_id,
                parent_op_ref=parent_op_ref,
                base_oid=base_oid,
                session_id=str(meta_session_id) if meta_session_id is not None else None,
                operation_id=operation_id,
                parent_operation_id=parent_operation_id,
                operation_label=operation_label or operation_id,
                world_id=world_id,
                world_disposition=world_disposition,
                nested_parent_scope_ref=nested_parent_scope_ref,
                nested_child_scope_ref=nested_child_scope_ref,
                nested_ancestry_chain=nested_ancestry_chain,
            )
        )
    return operations


def list_operation_archive_refs(
    owner: Store,
    *,
    world_id: str | None = None,
    operation_id: str | None = None,
) -> list[str]:
    archive_refs = sorted(ref for ref in owner._repo.references if ref.startswith("refs/vcscore/archive/ops/"))
    if world_id is None and operation_id is None:
        return archive_refs

    matches: list[str] = []
    for ref in archive_refs:
        summary = owner.read_operation_history(ref).summary
        if world_id is not None and summary.world_id != world_id:
            continue
        if operation_id is not None and summary.operation_id != operation_id:
            continue
        matches.append(ref)
    return matches


def operation_summary_from_projection(
    owner: Store,
    *,
    projection: Any,
    visibility: OperationVisibility,
    carrier_ref: str,
    archived_via: ArchivedVia | None = None,
) -> OperationSummary:
    world_name = owner._validated_world_name_for_ref(
        projection.world_ref,
        context=f"operation summary on carrier ref {carrier_ref!r}",
    )
    return OperationSummary(
        operation_id=projection.operation_id,
        label=projection.label,
        kind=projection.kind,
        status=derive_status(phase=projection.phase, result=projection.result),
        visibility=visibility,
        world_id=projection.world_id,
        world_name=world_name,
        world_ref=projection.world_ref,
        carrier_ref=carrier_ref,
        archived_via=archived_via,
        parent_operation_id=projection.parent_operation_id,
        effect_count=projection.effect_count,
        started_at=projection.started_at,
        closed_at=projection.closed_at,
        anchor_oid=projection.anchor_oid,
        final_phase=projection.phase,
    )


def operation_summary_sort_key(summary: OperationSummary) -> float:
    return (
        summary.closed_at
        if summary.closed_at is not None
        else summary.started_at
        if summary.started_at is not None
        else -1.0
    )


def raise_duplicate_visible_operation_id(ref: str, operation_id: str) -> None:
    msg = (
        "Invalid repository state: multiple visible operations share durable "
        f"operation_id {operation_id!r} on ref {ref!r}."
    )
    raise InvalidRepositoryStateError(msg)


def read_operation_history(owner: Store, ref: str) -> OperationHistory:
    if ref not in owner._repo.references:
        msg = f"Operation ref missing: {ref}"
        raise StaleScopeError(msg)
    if not ref.startswith((f"{owner.OP_REF_PREFIX}/", "refs/vcscore/archive/ops/")):
        raise ValueError(f"Ref {ref!r} is not an operation or archived-operation ref.")

    start_metadata = owner._read_operation_start_metadata(ref)
    anchor = find_latest_matching_anchor(
        owner._repo,
        ref,
        expected_operation_id=str(owner._operation_id_from_metadata(start_metadata)),
        terminal_only=False,
    )
    if anchor is None:
        msg = f"Operation anchor missing for ref: {ref}"
        raise StaleScopeError(msg)
    anchor_metadata = read_effect_json(owner._repo, anchor)
    require_pointer_epoch_metadata(anchor_metadata, context=f"operation ref {ref!r}")
    projection = project_pointer_history(owner._repo, anchor)
    visibility: OperationVisibility = "archived" if ref.startswith("refs/vcscore/archive/ops/") else "staged"
    return OperationHistory(
        summary=operation_summary_from_projection(
            owner,
            projection=projection,
            visibility=visibility,
            carrier_ref=ref,
            archived_via="operation_ref" if visibility == "archived" else None,
        ),
        commits=projection.commits,
    )


def completed_operation_commits_on_ref(owner: Store, ref: str) -> list[pygit2.Commit]:
    tip = owner._repo.references[ref].peel(pygit2.Commit)
    completion_commits: list[pygit2.Commit] = []
    for commit in topological_commits(owner._repo, tip.id):
        metadata = read_effect_json(owner._repo, commit)
        if pointer_metadata_available(metadata):
            if metadata.get("mg", {}).get("operation", {}).get("phase") == "completed":
                completion_commits.append(commit)
            continue
        if metadata.get("type") == "OperationCompleted":
            require_pointer_epoch_metadata(metadata, context=f"completed operations on ref {ref!r}")
    return completion_commits


def read_committed_operation_history(
    owner: Store,
    ref: str,
    *,
    operation_id: str,
    visibility: OperationVisibility,
    archived_via: ArchivedVia | None = None,
) -> OperationHistory:
    if ref not in owner._repo.references:
        msg = f"Ref missing: {ref}"
        raise StaleScopeError(msg)

    match: pygit2.Commit | None = None
    for commit in owner._completed_operation_commits_on_ref(ref):
        metadata = read_effect_json(owner._repo, commit)
        require_pointer_epoch_metadata(metadata, context=f"committed operation history on ref {ref!r}")
        current_operation_id = owner._operation_id_from_metadata(metadata)
        if current_operation_id != operation_id:
            continue
        if match is not None:
            owner._raise_duplicate_visible_operation_id(ref, operation_id)
        match = commit
    if match is not None:
        projection = project_pointer_history(owner._repo, match)
        return OperationHistory(
            summary=owner._operation_summary_from_projection(
                projection=projection,
                visibility=visibility,
                carrier_ref=ref,
                archived_via=archived_via,
            ),
            commits=projection.commits,
        )
    msg = f"Committed operation missing from ref {ref!r}: {operation_id!r}"
    raise StaleScopeError(msg)


def read_visible_operation_history(owner: Store, ref: str, *, operation_id: str) -> OperationHistory:
    return owner._read_committed_operation_history(ref, operation_id=operation_id, visibility="visible")


def read_discarded_world_operation_history(owner: Store, ref: str, *, operation_id: str) -> OperationHistory:
    return owner._read_committed_operation_history(
        ref,
        operation_id=operation_id,
        visibility="archived",
        archived_via="discarded_world_ref",
    )


def summaries_from_committed_carrier(
    owner: Store,
    ref: str,
    *,
    visibility: OperationVisibility,
    max_count: int,
    archived_via: ArchivedVia | None = None,
) -> list[OperationSummary]:
    summaries: list[OperationSummary] = []
    seen_operation_ids: set[str] = set()
    for completion_commit in owner._completed_operation_commits_on_ref(ref):
        completion_metadata = read_effect_json(owner._repo, completion_commit)
        require_pointer_epoch_metadata(completion_metadata, context=f"committed operations on ref {ref!r}")
        projection = project_pointer_history(owner._repo, completion_commit)
        summary = owner._operation_summary_from_projection(
            projection=projection,
            visibility=visibility,
            carrier_ref=ref,
            archived_via=archived_via,
        )
        operation_id = summary.operation_id
        if operation_id in seen_operation_ids:
            owner._raise_duplicate_visible_operation_id(ref, operation_id)
        seen_operation_ids.add(operation_id)
        summaries.append(summary)
        if len(summaries) >= max_count:
            break
    return summaries


def open_operations(
    owner: Store,
    *,
    scope_ref: str | None = None,
    session_id: str | None = None,
) -> list[OperationSummary]:
    return [
        owner.read_operation_history(operation.ref).summary
        for operation in owner.list_open_operations(scope_ref=scope_ref, session_id=session_id)
    ]


def committed_carrier_operations(owner: Store, ref: str, *, max_count: int = 50) -> list[OperationSummary]:
    if ref not in owner._repo.references:
        msg = f"Ref missing: {ref}"
        raise StaleScopeError(msg)
    if ref == owner.GROUND_REF or ref.startswith("refs/vcscore/scopes/"):
        return owner._summaries_from_committed_carrier(ref, visibility="visible", max_count=max_count)
    if owner._is_discarded_world_archive_ref(ref):
        return owner._summaries_from_committed_carrier(
            ref,
            visibility="archived",
            max_count=max_count,
            archived_via="discarded_world_ref",
        )
    raise ValueError(f"Ref {ref!r} is not a committed world carrier.")


def archived_recovery_operations(
    owner: Store,
    *,
    max_count: int = 50,
    world_id: str | None = None,
    operation_id: str | None = None,
) -> list[OperationSummary]:
    summaries = [
        owner.read_operation_history(ref).summary
        for ref in owner.list_operation_archive_refs(world_id=world_id, operation_id=operation_id)
    ]
    summaries = [summary for summary in summaries if summary.status != "ok"]
    summaries.sort(key=owner._operation_summary_sort_key, reverse=True)
    return summaries[:max_count]


def archived_operations(
    owner: Store,
    *,
    max_count: int = 50,
    world_id: str | None = None,
    operation_id: str | None = None,
) -> list[OperationSummary]:
    if operation_id is not None:
        summary = owner._validated_projected_archived_operation_summary(operation_id=operation_id, world_id=world_id)
        if summary is not None:
            return [summary]
    return owner._canonical_archived_operations(max_count=max_count, world_id=world_id, operation_id=operation_id)


def canonical_archived_operations(
    owner: Store,
    *,
    max_count: int,
    world_id: str | None,
    operation_id: str | None,
) -> list[OperationSummary]:
    summaries: list[OperationSummary] = []
    for ref in owner.list_operation_archive_refs(world_id=world_id, operation_id=operation_id):
        summaries.append(owner.read_operation_history(ref).summary)
    for ref in owner.list_discarded_world_archive_refs():
        for summary in owner._summaries_from_committed_carrier(
            ref,
            visibility="archived",
            max_count=1_000_000,
            archived_via="discarded_world_ref",
        ):
            if world_id is not None and summary.world_id != world_id:
                continue
            if operation_id is not None and summary.operation_id != operation_id:
                continue
            summaries.append(summary)
    summaries.sort(key=owner._operation_summary_sort_key, reverse=True)
    return summaries[:max_count]


def visible_operations(owner: Store, ref: str | None = None, *, max_count: int = 50) -> list[OperationSummary]:
    ref = ref or owner.GROUND_REF
    if ref not in owner._repo.references:
        msg = f"Ref missing: {ref}"
        raise StaleScopeError(msg)
    return owner._summaries_from_committed_carrier(ref, visibility="visible", max_count=max_count)


def operation_id_exists(owner: Store, operation_id: str) -> bool:
    for ref in [owner.GROUND_REF, *owner.list_scope_refs()]:
        for summary in owner.visible_operations(ref=ref, max_count=1_000_000):
            if summary.operation_id == operation_id:
                return True
    if owner._validated_projected_archived_operation_summary(operation_id=operation_id, world_id=None) is not None:
        return True
    if owner._archived_operation_id_exists_in_current_process(operation_id):
        return True
    return any(operation.operation_id == operation_id for operation in owner.list_open_operations())


def archived_operation_id_exists_in_current_process(owner: Store, operation_id: str) -> bool:
    cache = owner._fresh_archived_operation_membership_cache()
    if cache is None:
        cache = owner._build_archived_operation_membership_cache()
        owner._archived_operation_membership_cache = cache
    return operation_id in cache.archived_operation_ids


def fresh_archived_operation_membership_cache(owner: Store) -> ArchivedOperationMembershipCacheRecord | None:
    cache = owner._archived_operation_membership_cache
    if cache is None:
        return None
    return cache


def build_archived_operation_membership_cache(owner: Store) -> ArchivedOperationMembershipCacheRecord:
    frontier = archived_operation_projection_frontier(owner._repo)
    archived_operation_ids: set[str] = set()
    for ref in owner.list_operation_archive_refs():
        archived_operation_ids.add(owner.read_operation_history(ref).summary.operation_id)
    for ref in owner.list_discarded_world_archive_refs():
        archived_operation_ids.update(owner._exact_operation_ids_on_committed_carrier(ref))
    return owner.ArchivedOperationMembershipCache(
        frontier_digest=archived_operation_projection_digest(frontier),
        archived_operation_ids=frozenset(archived_operation_ids),
    )


def record_archived_membership_additions(
    owner: Store,
    previous_cache: ArchivedOperationMembershipCacheRecord | None,
    *,
    added_operation_ids: frozenset[str],
) -> None:
    if previous_cache is None:
        owner._archived_operation_membership_cache = None
        return
    owner._archived_operation_membership_cache = owner.ArchivedOperationMembershipCache(
        frontier_digest=previous_cache.frontier_digest,
        archived_operation_ids=previous_cache.archived_operation_ids | added_operation_ids,
    )


def exact_operation_ids_on_committed_carrier(owner: Store, ref: str) -> frozenset[str]:
    operation_ids: set[str] = set()
    for completion_commit in owner._completed_operation_commits_on_ref(ref):
        metadata = read_effect_json(owner._repo, completion_commit)
        require_pointer_epoch_metadata(
            metadata,
            context=f"exact operation-id enumeration on ref {ref!r}",
        )
        operation_id = owner._operation_id_from_metadata(metadata)
        if not isinstance(operation_id, str) or not operation_id:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: committed operation on ref {ref!r} is missing mg.operation.id."
            )
        if operation_id in operation_ids:
            owner._raise_duplicate_visible_operation_id(ref, operation_id)
        operation_ids.add(operation_id)
    return frozenset(operation_ids)


def fresh_archived_operation_projection(owner: Store) -> ArchivedOperationsByIdSnapshot | None:
    cached = owner._archived_operation_projection_cache
    if cached is not None:
        current_head = archived_operation_projection_current_head(owner._repo)
        if cached.head_oid == current_head:
            return cached
        owner._archived_operation_projection_cache = None

    snapshot = load_archived_operations_by_id_snapshot(owner._repo)
    if snapshot is None:
        owner._archived_operation_projection_cache = None
        return None
    if not archived_operation_projection_is_fresh(owner._repo, snapshot):
        owner._archived_operation_projection_cache = None
        return None
    owner._archived_operation_projection_cache = snapshot
    return snapshot


def validated_projected_archived_operation_summary(
    owner: Store,
    *,
    operation_id: str,
    world_id: str | None,
) -> OperationSummary | None:
    projection = owner._fresh_archived_operation_projection()
    if projection is None:
        return None
    candidate = projection.entries_by_id.get(operation_id)
    if candidate is None:
        return None
    manifest_carrier = projection.carriers_by_ref.get(candidate.carrier_ref)
    if manifest_carrier is None:
        return None
    if manifest_carrier.carrier_kind != candidate.carrier_kind:
        return None
    if manifest_carrier.tip_oid != candidate.carrier_tip_oid:
        return None
    if candidate.carrier_ref not in owner._repo.references:
        return None
    current_tip = str(owner._repo.references[candidate.carrier_ref].peel(pygit2.Commit).id)
    if current_tip != candidate.carrier_tip_oid:
        return None
    try:
        if candidate.carrier_kind == "archived_operation_ref":
            summary = owner.read_operation_history(candidate.carrier_ref).summary
        else:
            summary = owner.read_discarded_world_operation_history(
                candidate.carrier_ref,
                operation_id=candidate.operation_id,
            ).summary
    except StaleScopeError:
        return None
    if summary.operation_id != operation_id:
        return None
    if world_id is not None and summary.world_id != world_id:
        return None
    return summary


def build_archived_operation_projection_entries(owner: Store) -> tuple[ArchivedOperationCandidate, ...]:
    entries_by_id: dict[str, ArchivedOperationCandidate] = {}
    for ref in owner.list_operation_archive_refs():
        summary = owner.read_operation_history(ref).summary
        candidate = ArchivedOperationCandidate(
            operation_id=summary.operation_id,
            carrier_ref=summary.carrier_ref,
            carrier_tip_oid=str(owner._repo.references[ref].peel(pygit2.Commit).id),
            carrier_kind="archived_operation_ref",
        )
        existing = entries_by_id.get(candidate.operation_id)
        if existing is not None and existing.carrier_ref != candidate.carrier_ref:
            msg = (
                "Invalid repository state: multiple archived carriers share durable "
                f"operation_id {candidate.operation_id!r}."
            )
            raise InvalidRepositoryStateError(msg)
        entries_by_id[candidate.operation_id] = candidate

    for ref in owner.list_discarded_world_archive_refs():
        tip_oid = str(owner._repo.references[ref].peel(pygit2.Commit).id)
        for summary in owner._summaries_from_committed_carrier(
            ref,
            visibility="archived",
            max_count=1_000_000,
            archived_via="discarded_world_ref",
        ):
            candidate = ArchivedOperationCandidate(
                operation_id=summary.operation_id,
                carrier_ref=summary.carrier_ref,
                carrier_tip_oid=tip_oid,
                carrier_kind="discarded_world_ref",
            )
            existing = entries_by_id.get(candidate.operation_id)
            if existing is not None and existing.carrier_ref != candidate.carrier_ref:
                msg = (
                    "Invalid repository state: multiple archived carriers share durable "
                    f"operation_id {candidate.operation_id!r}."
                )
                raise InvalidRepositoryStateError(msg)
            entries_by_id[candidate.operation_id] = candidate
    return tuple(sorted(entries_by_id.values(), key=lambda item: item.operation_id))


def publish_archived_operation_projection(owner: Store) -> bool:
    expected_head = archived_operation_projection_current_head(owner._repo)
    frontier = archived_operation_projection_frontier(owner._repo)
    expected_source_digest = archived_operation_projection_digest(frontier)
    entries = owner._build_archived_operation_projection_entries()
    published = publish_archived_operations_by_id_snapshot(
        owner._repo,
        expected_head_oid=expected_head,
        expected_source_digest=expected_source_digest,
        carriers=frontier,
        entries=entries,
    )
    if published:
        owner._archived_operation_projection_cache = load_archived_operations_by_id_snapshot(owner._repo)
    else:
        owner._archived_operation_projection_cache = None
    return published


def record_archived_operation_projection_additions(
    owner: Store,
    previous_projection: ArchivedOperationsByIdSnapshot | None,
    *,
    carrier_ref: str,
    carrier_kind: ProjectionCarrierKind,
    operation_ids: frozenset[str],
) -> bool:
    if previous_projection is None:
        return owner._refresh_archived_operation_projection()
    if carrier_ref not in owner._repo.references:
        return owner._refresh_archived_operation_projection()

    carrier_tip_oid = str(owner._repo.references[carrier_ref].peel(pygit2.Commit).id)
    carrier = ProjectionCarrier(
        ref=carrier_ref,
        tip_oid=carrier_tip_oid,
        carrier_kind=carrier_kind,
    )
    if carrier.ref in previous_projection.carriers_by_ref:
        return owner._refresh_archived_operation_projection()

    entries: list[ArchivedOperationCandidate] = []
    for operation_id in operation_ids:
        if operation_id in previous_projection.entries_by_id:
            return owner._refresh_archived_operation_projection()
        entries.append(
            ArchivedOperationCandidate(
                operation_id=operation_id,
                carrier_ref=carrier_ref,
                carrier_tip_oid=carrier_tip_oid,
                carrier_kind=carrier_kind,
            )
        )

    try:
        projection = publish_archived_operations_by_id_additions(
            owner._repo,
            previous=previous_projection,
            added_carriers=(carrier,),
            added_entries=tuple(sorted(entries, key=lambda item: item.operation_id)),
        )
    except (KeyError, TypeError, ValueError, OSError, pygit2.GitError):
        projection = None
    if projection is None:
        owner._archived_operation_projection_cache = None
        return owner._refresh_archived_operation_projection()

    owner._archived_operation_projection_cache = projection
    return True


def refresh_archived_operation_projection(owner: Store) -> bool:
    try:
        return owner._publish_archived_operation_projection()
    except (InvalidRepositoryStateError, OSError, pygit2.GitError):
        owner._archived_operation_projection_cache = None
        return False
