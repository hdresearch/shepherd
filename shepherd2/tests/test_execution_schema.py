from __future__ import annotations

import pytest
from shepherd2.schema_library import ProjectionModeError
from shepherd2.schemas.execution import (
    EXECUTION_COMPLETED_SCHEMA,
    EXECUTION_CREATED_SCHEMA,
    EXECUTION_FAILED_SCHEMA,
    EXECUTION_PROJECTION,
    EXECUTION_SCHEMA_LIBRARY,
    EXECUTION_STARTED_SCHEMA,
)

from shepherd2 import (
    AppendContext,
    Fact,
    FactDraft,
    ReadContext,
    SQLiteTraceStore,
    complete_execution_batch,
    create_execution_batch,
    execution_completed,
    execution_created,
    execution_id_for,
    project_execution,
    publish_execution_frontier,
)

TRUSTED = AppendContext(
    actor_ref="runtime:test",
    presented_witness_refs=("trusted:internal",),
    schema_version_set="shepherd2-slice-a",
    trust_mode="internal",
)
READER = ReadContext(actor_ref="reader")


def test_execution_schema_helpers_emit_fact_drafts() -> None:
    execution_id = execution_id_for("intent:execution:create")

    created = execution_created(execution_id=execution_id, task_ref="Task", inputs={"x": 1})
    completed = execution_completed(execution_id=execution_id, outputs={"y": 2})

    assert isinstance(created, FactDraft)
    assert created.fact_kind == "execution_created"
    assert created.schema_ref == "shepherd2.execution.created.v1"
    assert created.payload["execution_id"] == execution_id
    assert isinstance(completed, FactDraft)
    assert completed.fact_kind == "execution_completed"


def test_execution_is_declared_as_schema_library() -> None:
    assert EXECUTION_SCHEMA_LIBRARY.name == "shepherd2.execution"
    assert EXECUTION_SCHEMA_LIBRARY.schema_refs == frozenset(
        {
            EXECUTION_CREATED_SCHEMA,
            EXECUTION_STARTED_SCHEMA,
            EXECUTION_COMPLETED_SCHEMA,
            EXECUTION_FAILED_SCHEMA,
        }
    )
    assert EXECUTION_SCHEMA_LIBRARY.projection_specs == frozenset({EXECUTION_PROJECTION})
    assert EXECUTION_PROJECTION.mode_requirement == "both"


def test_project_execution_consumes_trace_slice() -> None:
    store = SQLiteTraceStore()
    execution_id = execution_id_for("intent:execution-slice:create")
    store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:execution-slice:create",
            execution_id=execution_id,
            task_ref="Task",
            inputs={"x": 10},
        ),
    )
    terminal = store.append(
        TRUSTED,
        complete_execution_batch(
            append_intent_id="intent:execution-slice:complete",
            execution_id=execution_id,
            outputs={"y": 11},
        ),
    )
    cutoff = publish_execution_frontier(
        store,
        TRUSTED,
        frontier_id="frontier:execution-slice",
        target_execution_id=execution_id,
        through_fact_id=terminal.fact_ids[-1],
    )
    trace_slice = store.resolve_frontier(READER, cutoff.frontier_id)

    projected = project_execution(trace_slice, execution_id, cutoff=cutoff)

    assert projected.execution_id == execution_id
    assert projected.task_ref == "Task"
    assert projected.inputs == {"x": 10}
    assert projected.outputs == {"y": 11}
    assert projected.status == "succeeded"


def test_project_execution_declares_mode_requirement() -> None:
    store = SQLiteTraceStore()
    execution_id = execution_id_for("intent:execution-mode:create")
    receipt = store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:execution-mode:create",
            execution_id=execution_id,
            task_ref="Task",
            inputs={},
        ),
    )
    trace_slice = store.read_owner_prefix(READER, execution_id, len(receipt.fact_ids), mode_filter="captures_only")

    with pytest.raises(ProjectionModeError, match="requires mode_filter='both'"):
        project_execution(trace_slice, execution_id)


def test_execution_frontier_validation_is_schema_level() -> None:
    store = SQLiteTraceStore()
    execution_id = execution_id_for("intent:execution-frontier:create")
    receipt = store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:execution-frontier:create",
            execution_id=execution_id,
            task_ref="Task",
            inputs={},
        ),
    )
    through = store.read_fact(READER, receipt.fact_ids[-1])

    assert isinstance(through, Fact)
    assert through.fact_kind == "execution_started"
    with pytest.raises(ValueError, match="terminal lifecycle"):
        publish_execution_frontier(
            store,
            TRUSTED,
            frontier_id="frontier:not-terminal",
            target_execution_id=execution_id,
            through_fact_id=through.envelope.fact_id,
        )
