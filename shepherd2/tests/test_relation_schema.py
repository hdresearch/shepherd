from __future__ import annotations

import pytest

from shepherd2 import (
    AppendContext,
    Fact,
    FactDraft,
    ReadContext,
    SQLiteTraceStore,
    complete_execution_batch,
    create_execution_batch,
    create_execution_relation_batch,
    execution_id_for,
    execution_relation_created,
    execution_relation_from_fact,
    project_execution_relations,
    publish_execution_frontier,
    relation_id_for,
)

TRUSTED = AppendContext(
    actor_ref="runtime:test",
    presented_witness_refs=("trusted:internal",),
    schema_version_set="shepherd2-slice-a",
    trust_mode="internal",
)
READER = ReadContext(actor_ref="reader")


def test_relation_schema_helper_emits_fact_draft() -> None:
    relation_id = relation_id_for("intent:relation:create")

    draft = execution_relation_created(
        relation_id=relation_id,
        relation_kind="spawned",
        parent_execution_id="exec:parent",
        child_execution_id="exec:child",
        child_frontier_id="frontier:child",
    )

    assert isinstance(draft, FactDraft)
    assert draft.fact_kind == "relation_created"
    assert draft.schema_ref == "shepherd2.execution_relation.created.v1"
    assert draft.payload["relation_id"] == relation_id


def test_project_execution_relations_consumes_trace_slice() -> None:
    store = SQLiteTraceStore()
    parent_id = execution_id_for("intent:parent:create")
    child_id = execution_id_for("intent:child:create")
    relation_id = relation_id_for("intent:parent:spawn-child")
    parent_create = store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:parent:create",
            execution_id=parent_id,
            task_ref="Parent",
            inputs={},
        ),
    )
    relation_receipt = store.append(
        TRUSTED,
        create_execution_relation_batch(
            append_intent_id="intent:parent:spawn-child",
            relation_id=relation_id,
            relation_kind="spawned",
            parent_execution_id=parent_id,
            child_execution_id=child_id,
            child_frontier_id="frontier:child",
            caused_by=(parent_create.fact_ids[-1],),
        ),
    )
    parent_terminal = store.append(
        TRUSTED,
        complete_execution_batch(
            append_intent_id="intent:parent:complete",
            execution_id=parent_id,
            outputs={},
            caused_by=relation_receipt.fact_ids,
        ),
    )
    cutoff = publish_execution_frontier(
        store,
        TRUSTED,
        frontier_id="frontier:parent",
        target_execution_id=parent_id,
        through_fact_id=parent_terminal.fact_ids[-1],
    )
    trace_slice = store.resolve_frontier(READER, cutoff.frontier_id)

    relations = project_execution_relations(trace_slice, parent_id)

    assert len(relations) == 1
    assert relations[0].relation_id == relation_id
    assert relations[0].parent_execution_id == parent_id
    assert relations[0].child_execution_id == child_id


def test_execution_relation_from_fact_rejects_wrong_fact_kind() -> None:
    store = SQLiteTraceStore()
    receipt = store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:not-relation",
            execution_id=execution_id_for("intent:not-relation"),
            task_ref="Task",
            inputs={},
        ),
    )
    fact = store.read_fact(READER, receipt.fact_ids[0])

    assert isinstance(fact, Fact)
    assert fact.fact_kind == "execution_created"
    with pytest.raises(ValueError, match="expected relation_created"):
        execution_relation_from_fact(fact)
