"""Effective-history projection over execution and relation facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..kernel.facts import Fact
from .execution import project_execution
from .relations import project_execution_relations

if TYPE_CHECKING:
    from ..kernel.facts import FactId, OwnerCutoff, ReadContext, TraceSlice, TraceStore
    from .execution import Execution
    from .relations import ExecutionRelation, RelationId


@dataclass(frozen=True)
class PublishedFact:
    """Projected parent-published fact visible in effective history."""

    fact_id: FactId
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class EffectiveChild:
    """One active child in a parent's effective history."""

    relation: ExecutionRelation
    execution: Execution


@dataclass(frozen=True)
class EffectiveHistory:
    """Projected effective history for one execution owner prefix."""

    root: Execution
    relations: tuple[ExecutionRelation, ...]
    children: tuple[EffectiveChild, ...]
    published_facts: tuple[PublishedFact, ...]


def project_effective_history(
    root_slice: TraceSlice,
    root_trace_owner_id: str,
    *,
    child_slices: tuple[TraceSlice, ...] = (),
) -> EffectiveHistory:
    """Project root execution, active children, and parent-published facts from trace slices."""
    root = project_execution(root_slice, root_trace_owner_id)
    relations = project_execution_relations(root_slice, root_trace_owner_id)
    active_relations = _active_relations(relations)
    children: list[EffectiveChild] = []
    child_slices_by_frontier = {
        trace_slice.frontier.frontier_id: trace_slice
        for trace_slice in child_slices
        if trace_slice.frontier is not None
    }
    for relation in active_relations:
        if relation.child_frontier_id is None:
            continue
        child_slice = child_slices_by_frontier.get(relation.child_frontier_id)
        if child_slice is None:
            continue
        children.append(
            EffectiveChild(
                relation=relation,
                execution=project_execution(child_slice, relation.child_execution_id),
            )
        )

    return EffectiveHistory(
        root=root,
        relations=relations,
        children=tuple(children),
        published_facts=_project_published_facts(root_slice, root_trace_owner_id),
    )


def project_effective_history_from_store(
    store: TraceStore,
    read_context: ReadContext,
    cutoff: OwnerCutoff,
) -> EffectiveHistory:
    """Resolve needed frontiers and project effective history."""
    root_slice = store.resolve_frontier(read_context, cutoff.frontier_id)
    relations = project_execution_relations(root_slice, cutoff.target_trace_owner_id)
    child_slices = tuple(
        store.resolve_frontier(read_context, relation.child_frontier_id)
        for relation in _active_relations(relations)
        if relation.child_frontier_id is not None
    )
    return project_effective_history(root_slice, cutoff.target_trace_owner_id, child_slices=child_slices)


def _active_relations(relations: tuple[ExecutionRelation, ...]) -> tuple[ExecutionRelation, ...]:
    latest_by_relation: dict[RelationId, ExecutionRelation] = {}
    for relation in relations:
        latest_by_relation[relation.relation_id] = relation
    return tuple(
        relation for relation in latest_by_relation.values() if relation.relation_kind in {"spawned", "adopted"}
    )


def _project_published_facts(trace_slice: TraceSlice, trace_owner_id: str) -> tuple[PublishedFact, ...]:
    facts: list[PublishedFact] = []
    owner_paths = trace_slice.owner_paths
    visible_facts_by_id = trace_slice.visible_facts_by_id
    for fact_id in owner_paths.get(trace_owner_id, ()):
        fact = visible_facts_by_id.get(fact_id)
        if not isinstance(fact, Fact):
            raise TypeError("project_effective_history requires payload-visible facts")
        if fact.envelope.schema_ref != "shepherd2.runtime.published_fact.v1":
            continue
        raw_data = fact.body.payload.get("data", {})
        facts.append(
            PublishedFact(
                fact_id=fact.envelope.fact_id,
                kind=str(fact.body.payload.get("kind", "")),
                data=dict(raw_data) if isinstance(raw_data, dict) else {},
            )
        )
    return tuple(facts)
