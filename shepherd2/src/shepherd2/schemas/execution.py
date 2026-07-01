"""Execution fact helpers and projection folds."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from ..kernel.facts import (
    AppendBatch,
    AppendContext,
    AppendGroup,
    Fact,
    FactDraft,
    FactId,
    OwnerCutoff,
    OwnerCutoffSpec,
    ReadContext,
    TraceOwnerId,
    TraceSlice,
    TraceStore,
)
from .schema_library import ProjectionSpec, StaticSchemaLibrary, ensure_projection_compatible

ExecutionId = TraceOwnerId
ExecutionStatus = Literal["pending", "running", "succeeded", "failed"]

EXECUTION_CREATED_SCHEMA = "shepherd2.execution.created.v1"
EXECUTION_STARTED_SCHEMA = "shepherd2.execution.started.v1"
EXECUTION_COMPLETED_SCHEMA = "shepherd2.execution.completed.v1"
EXECUTION_FAILED_SCHEMA = "shepherd2.execution.failed.v1"
EXECUTION_PROJECTION = ProjectionSpec("shepherd2.execution.project", mode_requirement="both")
EXECUTION_SCHEMA_LIBRARY = StaticSchemaLibrary(
    name="shepherd2.execution",
    schema_refs=frozenset(
        {
            EXECUTION_CREATED_SCHEMA,
            EXECUTION_STARTED_SCHEMA,
            EXECUTION_COMPLETED_SCHEMA,
            EXECUTION_FAILED_SCHEMA,
        }
    ),
    projection_specs=frozenset({EXECUTION_PROJECTION}),
)


@dataclass(frozen=True)
class Execution:
    """Projected execution value derived from retained trace facts."""

    execution_id: ExecutionId
    task_ref: str
    parent_execution_id: ExecutionId | None
    status: ExecutionStatus
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    error: str | None
    started_fact_id: FactId | None
    terminal_fact_id: FactId | None
    cutoff: OwnerCutoff


def execution_id_for(append_intent_id: str, local_ref: str = "execution") -> ExecutionId:
    """Derive a stable execution id for an append-local execution reference."""
    digest = hashlib.sha256(f"{append_intent_id}\0{local_ref}".encode()).hexdigest()
    return f"exec:{digest[:32]}"


def execution_created(
    *,
    execution_id: ExecutionId,
    task_ref: str,
    inputs: dict[str, Any],
    parent_execution_id: ExecutionId | None = None,
) -> FactDraft:
    """Create an execution entity fact body."""
    return FactDraft(
        mode="declaration",
        schema_ref=EXECUTION_CREATED_SCHEMA,
        kind_label="execution_created",
        payload={
            "execution_id": execution_id,
            "task_ref": task_ref,
            "inputs": inputs,
            "parent_execution_id": parent_execution_id,
        },
    )


def execution_started(*, execution_id: ExecutionId) -> FactDraft:
    """Create an execution-started lifecycle fact body."""
    return FactDraft(
        mode="capture",
        schema_ref=EXECUTION_STARTED_SCHEMA,
        kind_label="execution_started",
        payload={"execution_id": execution_id},
    )


def execution_completed(*, execution_id: ExecutionId, outputs: dict[str, Any]) -> FactDraft:
    """Create an execution-completed lifecycle fact body."""
    return FactDraft(
        mode="capture",
        schema_ref=EXECUTION_COMPLETED_SCHEMA,
        kind_label="execution_completed",
        payload={"execution_id": execution_id, "outputs": outputs},
    )


def execution_failed(*, execution_id: ExecutionId, error: str) -> FactDraft:
    """Create an execution-failed lifecycle fact body."""
    return FactDraft(
        mode="capture",
        schema_ref=EXECUTION_FAILED_SCHEMA,
        kind_label="execution_failed",
        payload={"execution_id": execution_id, "error": error},
    )


def create_execution_batch(
    *,
    append_intent_id: str,
    execution_id: ExecutionId,
    task_ref: str,
    inputs: dict[str, Any],
    parent_execution_id: ExecutionId | None = None,
    caused_by: tuple[FactId, ...] = (),
) -> AppendBatch:
    """Build the canonical creation/start append for one execution."""
    return AppendBatch(
        append_intent_id=append_intent_id,
        groups=(
            AppendGroup(
                trace_owner_id=execution_id,
                causal_parents=caused_by,
                fact_drafts=(
                    execution_created(
                        execution_id=execution_id,
                        task_ref=task_ref,
                        inputs=inputs,
                        parent_execution_id=parent_execution_id,
                    ),
                    execution_started(execution_id=execution_id),
                ),
            ),
        ),
    )


def complete_execution_batch(
    *,
    append_intent_id: str,
    execution_id: ExecutionId,
    outputs: dict[str, Any],
    caused_by: tuple[FactId, ...] = (),
) -> AppendBatch:
    """Build the canonical terminal-success append for one execution."""
    return AppendBatch(
        append_intent_id=append_intent_id,
        groups=(
            AppendGroup(
                trace_owner_id=execution_id,
                causal_parents=caused_by,
                fact_drafts=(execution_completed(execution_id=execution_id, outputs=outputs),),
            ),
        ),
    )


def fail_execution_batch(
    *,
    append_intent_id: str,
    execution_id: ExecutionId,
    error: str,
    caused_by: tuple[FactId, ...] = (),
) -> AppendBatch:
    """Build the canonical terminal-failure append for one execution."""
    return AppendBatch(
        append_intent_id=append_intent_id,
        groups=(
            AppendGroup(
                trace_owner_id=execution_id,
                causal_parents=caused_by,
                fact_drafts=(execution_failed(execution_id=execution_id, error=error),),
            ),
        ),
    )


def publish_execution_frontier(
    store: TraceStore,
    append_context: AppendContext,
    *,
    frontier_id: str,
    target_execution_id: ExecutionId,
    through_fact_id: FactId,
    publisher_execution_id: ExecutionId | None = None,
    append_intent_id: str | None = None,
    caused_by: tuple[FactId, ...] = (),
) -> OwnerCutoff:
    """Publish an execution-schema terminal frontier over the Ring 0 frontier ABI."""
    through = store.read_fact(ReadContext(actor_ref="execution:schema"), through_fact_id)
    if not isinstance(through, Fact):
        raise TypeError("publish_execution_frontier requires payload-visible facts")
    if through.envelope.schema_ref not in {EXECUTION_COMPLETED_SCHEMA, EXECUTION_FAILED_SCHEMA}:
        raise ValueError("terminal execution frontier must target a terminal lifecycle fact")
    return store.publish_frontier(
        append_context,
        OwnerCutoffSpec(
            frontier_id=frontier_id,
            target_trace_owner_id=target_execution_id,
            through_fact_id=through_fact_id,
            publisher_trace_owner_id=publisher_execution_id,
            append_intent_id=append_intent_id,
            caused_by=caused_by,
        ),
    )


def project_execution(
    trace_slice: TraceSlice,
    target_trace_owner_id: ExecutionId,
    *,
    cutoff: OwnerCutoff | None = None,
) -> Execution:
    """Project an execution from a payload-visible trace slice."""
    ensure_projection_compatible(trace_slice, EXECUTION_PROJECTION)
    task_ref = ""
    parent_execution_id: ExecutionId | None = None
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    error: str | None = None
    status: ExecutionStatus = "pending"
    started_fact_id: FactId | None = None
    terminal_fact_id: FactId | None = None

    for fact_id in trace_slice.owner_paths.get(target_trace_owner_id, ()):
        fact = trace_slice.visible_facts_by_id.get(fact_id)
        if not isinstance(fact, Fact):
            raise TypeError("project_execution requires payload-visible facts")
        if fact.body.payload.get("execution_id") not in {None, target_trace_owner_id}:
            continue
        if fact.envelope.schema_ref == EXECUTION_CREATED_SCHEMA:
            task_ref = str(fact.body.payload.get("task_ref", ""))
            raw_parent = fact.body.payload.get("parent_execution_id")
            parent_execution_id = str(raw_parent) if raw_parent is not None else None
            raw_inputs = fact.body.payload.get("inputs", {})
            inputs = dict(raw_inputs) if isinstance(raw_inputs, dict) else {}
            status = "pending"
        elif fact.envelope.schema_ref == EXECUTION_STARTED_SCHEMA:
            started_fact_id = fact.envelope.fact_id
            status = "running"
        elif fact.envelope.schema_ref == EXECUTION_COMPLETED_SCHEMA:
            raw_outputs = fact.body.payload.get("outputs", {})
            outputs = dict(raw_outputs) if isinstance(raw_outputs, dict) else {}
            error = None
            terminal_fact_id = fact.envelope.fact_id
            status = "succeeded"
        elif fact.envelope.schema_ref == EXECUTION_FAILED_SCHEMA:
            outputs = {}
            error = str(fact.body.payload.get("error", ""))
            terminal_fact_id = fact.envelope.fact_id
            status = "failed"

    resolved_cutoff = cutoff or _cutoff_from_slice(trace_slice, target_trace_owner_id)
    return Execution(
        execution_id=target_trace_owner_id,
        task_ref=task_ref,
        parent_execution_id=parent_execution_id,
        status=status,
        inputs=inputs,
        outputs=outputs,
        error=error,
        started_fact_id=started_fact_id,
        terminal_fact_id=terminal_fact_id,
        cutoff=resolved_cutoff,
    )


project_execution_slice = project_execution


def project_execution_from_store(store: TraceStore, read_context: ReadContext, cutoff: OwnerCutoff) -> Execution:
    """Resolve a frontier and project an execution."""
    return project_execution(
        store.resolve_frontier(read_context, cutoff.frontier_id),
        cutoff.target_trace_owner_id,
        cutoff=cutoff,
    )


def _cutoff_from_slice(trace_slice: TraceSlice, target_trace_owner_id: ExecutionId) -> OwnerCutoff:
    frontier = trace_slice.frontier
    if frontier is not None:
        return OwnerCutoff(
            frontier_id=frontier.frontier_id,
            target_trace_owner_id=frontier.target_trace_owner_id,
            through_fact_id=frontier.through_fact_id,
            through_owner_ordinal=frontier.through_owner_ordinal,
            publisher_trace_owner_id=frontier.publisher_trace_owner_id,
            created_by_fact_id=frontier.created_by_fact_id,
        )
    fact_ids = trace_slice.owner_paths.get(target_trace_owner_id, ())
    through_fact_id = fact_ids[-1] if fact_ids else ""
    through_ordinal = -1
    if through_fact_id:
        visible = trace_slice.visible_facts_by_id.get(through_fact_id)
        if isinstance(visible, Fact):
            through_ordinal = visible.owner_ordinal
    return OwnerCutoff(
        frontier_id="",
        target_trace_owner_id=target_trace_owner_id,
        through_fact_id=through_fact_id,
        through_owner_ordinal=through_ordinal,
    )
