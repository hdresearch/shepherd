"""Parent-owned execution relation facts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from ..kernel.facts import (
    AppendBatch,
    AppendGroup,
    Fact,
    FactDraft,
    FactId,
    OwnerCutoff,
    ReadContext,
    TraceOwnerId,
    TraceSlice,
    TraceStore,
)
from .schema_library import ProjectionSpec, StaticSchemaLibrary, ensure_projection_compatible

ExecutionId = TraceOwnerId

RelationId = str
RelationKind = Literal["spawned", "adopted", "abandoned"]
EXECUTION_RELATION_SCHEMA = "shepherd2.execution_relation.created.v1"
EXECUTION_RELATION_PROJECTION = ProjectionSpec("shepherd2.execution_relation.project", mode_requirement="both")
EXECUTION_RELATION_SCHEMA_LIBRARY = StaticSchemaLibrary(
    name="shepherd2.execution_relation",
    schema_refs=frozenset({EXECUTION_RELATION_SCHEMA}),
    projection_specs=frozenset({EXECUTION_RELATION_PROJECTION}),
)


@dataclass(frozen=True)
class ExecutionRelation:
    """Projected parent-owned relation between two executions."""

    relation_id: RelationId
    relation_kind: RelationKind
    parent_execution_id: ExecutionId
    child_execution_id: ExecutionId
    child_frontier_id: str | None
    created_fact_id: FactId


def relation_id_for(append_intent_id: str, local_ref: str = "relation") -> RelationId:
    """Derive a stable relation id for an append-local relation reference."""
    digest = hashlib.sha256(f"{append_intent_id}\0{local_ref}".encode()).hexdigest()
    return f"rel:{digest[:32]}"


def execution_relation_created(
    *,
    relation_id: RelationId,
    relation_kind: RelationKind,
    parent_execution_id: ExecutionId,
    child_execution_id: ExecutionId,
    child_frontier_id: str | None = None,
) -> FactDraft:
    """Create a parent-owned execution-relation fact body."""
    return FactDraft(
        mode="capture",
        schema_ref=EXECUTION_RELATION_SCHEMA,
        kind_label="relation_created",
        payload={
            "relation_id": relation_id,
            "relation_kind": relation_kind,
            "parent_execution_id": parent_execution_id,
            "child_execution_id": child_execution_id,
            "child_frontier_id": child_frontier_id,
        },
    )


def create_execution_relation_batch(
    *,
    append_intent_id: str,
    relation_id: RelationId,
    relation_kind: RelationKind,
    parent_execution_id: ExecutionId,
    child_execution_id: ExecutionId,
    child_frontier_id: str | None = None,
    caused_by: tuple[FactId, ...] = (),
) -> AppendBatch:
    """Build the canonical parent-owned relation append."""
    return AppendBatch(
        append_intent_id=append_intent_id,
        groups=(
            AppendGroup(
                trace_owner_id=parent_execution_id,
                causal_parents=caused_by,
                fact_drafts=(
                    execution_relation_created(
                        relation_id=relation_id,
                        relation_kind=relation_kind,
                        parent_execution_id=parent_execution_id,
                        child_execution_id=child_execution_id,
                        child_frontier_id=child_frontier_id,
                    ),
                ),
            ),
        ),
    )


def project_execution_relations(
    trace_slice: TraceSlice,
    parent_trace_owner_id: TraceOwnerId,
) -> tuple[ExecutionRelation, ...]:
    """Project parent-owned execution relations from a payload-visible trace slice."""
    ensure_projection_compatible(trace_slice, EXECUTION_RELATION_PROJECTION)
    relations: list[ExecutionRelation] = []
    for fact_id in trace_slice.owner_paths.get(parent_trace_owner_id, ()):
        fact = trace_slice.visible_facts_by_id.get(fact_id)
        if not isinstance(fact, Fact):
            raise TypeError("project_execution_relations requires payload-visible facts")
        if fact.envelope.schema_ref != EXECUTION_RELATION_SCHEMA:
            continue
        relation = _relation_from_payload(fact.envelope.fact_id, fact.body.payload)
        if relation.parent_execution_id != parent_trace_owner_id:
            continue
        relations.append(relation)
    return tuple(relations)


project_execution_relations_slice = project_execution_relations


def project_execution_relations_from_store(
    store: TraceStore,
    read_context: ReadContext,
    cutoff: OwnerCutoff,
) -> tuple[ExecutionRelation, ...]:
    """Resolve a frontier and project parent-owned execution relations."""
    return project_execution_relations(
        store.resolve_frontier(read_context, cutoff.frontier_id),
        cutoff.target_trace_owner_id,
    )


def execution_relation_from_fact(fact: Fact) -> ExecutionRelation:
    """Project one relation-created fact."""
    if fact.envelope.schema_ref != EXECUTION_RELATION_SCHEMA:
        raise ValueError(f"expected relation_created fact, got {fact.envelope.schema_ref!r}")
    return _relation_from_payload(fact.envelope.fact_id, fact.body.payload)


def _relation_from_payload(fact_id: FactId, payload: dict[str, object]) -> ExecutionRelation:
    raw_kind = str(payload.get("relation_kind", ""))
    if raw_kind not in {"spawned", "adopted", "abandoned"}:
        raise ValueError(f"unknown execution relation kind: {raw_kind}")
    raw_frontier_id = payload.get("child_frontier_id")
    return ExecutionRelation(
        relation_id=str(payload.get("relation_id", "")),
        relation_kind=raw_kind,  # type: ignore[arg-type]
        parent_execution_id=str(payload.get("parent_execution_id", "")),
        child_execution_id=str(payload.get("child_execution_id", "")),
        child_frontier_id=str(raw_frontier_id) if raw_frontier_id is not None else None,
        created_fact_id=fact_id,
    )
