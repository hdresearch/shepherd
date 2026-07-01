"""Read/query seam for workspace-control ledgers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from shepherd_dialect.trace import RunTrace
from shepherd_dialect.workspace_control.ledger_contracts import (
    FLOW_RUNS,
    FLOWS,
    RUN_ARGS,
    RUN_LEDGER_BINDING,
    RUN_LEDGER_SCHEMA,
    RUN_LEDGER_STORAGE_SHAPE,
    RUN_RECORDS,
    TASK_ARTIFACT_BINDING,  # noqa: F401
    TASK_ARTIFACT_SCHEMA,  # noqa: F401
    TASK_LEDGER_BINDING,
    TASK_LEDGER_SCHEMA,
)
from shepherd_dialect.workspace_control.outputs import DescriptorResolver, RunOutputResolver
from shepherd_dialect.workspace_control.run_ledger import RunLedgerStore
from shepherd_dialect.workspace_control.schemas import (
    ResolvedTask,
    RunOutputCitationRef,
    RunRecord,
    RunSummary,
    TaskDefinitionVersion,
    TaskSummary,
    run_workspace_output_world_oid,
)

if TYPE_CHECKING:
    from shepherd2.schemas.run_outputs import RunOutputRef
    from vcs_core.keyed_json_tree import KeyedJsonTreeStore

_RUN_RECORDS = RUN_RECORDS
_RUN_ARGS = RUN_ARGS
_FLOWS = FLOWS
_FLOW_RUNS = FLOW_RUNS


class TraceNotMaterializedError(RuntimeError):
    """Raised when a run has only provider-neutral trace identity."""


def read_task_ledger_payload(mg: Any, *, scope: Any = None) -> Mapping[str, object] | None:
    """Read the selected task-ledger payload through vcs-core's public API."""
    return _read_selected_payload(mg, TASK_LEDGER_BINDING, scope=scope)


def read_run_ledger_payload(mg: Any, *, scope: Any = None) -> Mapping[str, object] | None:
    """Read or synthesize the selected run-ledger payload for compatibility queries.

    For keyed-tree run ledgers, ``revision.json`` is only the manifest. This
    helper reconstructs the old aggregate shape for broad list/CLI/test
    callers; product mutations must use keyed run-ledger helpers instead.
    """
    payload = _read_selected_payload(mg, RUN_LEDGER_BINDING, scope=scope)
    if payload is None or payload.get("storage_shape") != RUN_LEDGER_STORAGE_SHAPE:
        return payload
    synthesized = dict(payload)
    synthesized["runs"] = [dict(row) for row in _list_selected_json_records(mg, _RUN_RECORDS, scope=scope)]
    synthesized["args"] = _json_record_map(_list_selected_json_records(mg, _RUN_ARGS, scope=scope), "args_ref")
    synthesized["flows"] = _json_record_map(_list_selected_json_records(mg, _FLOWS, scope=scope), "flow_id")
    synthesized["flow_runs"] = _json_record_map(_list_selected_json_records(mg, _FLOW_RUNS, scope=scope), "run_ref")
    return synthesized


def list_tasks(
    mg: Any,
    *,
    status: str | None = None,
    prefix: str | None = None,
    scope: Any = None,
) -> tuple[TaskSummary, ...]:
    """Return task summaries from the selected task ledger."""
    versions = _task_versions(read_task_ledger_payload(mg, scope=scope))
    summaries = []
    for version in versions:
        if status is not None and version.status != status:
            continue
        if prefix is not None and not version.task_id.startswith(prefix):
            continue
        summaries.append(version.summary())
    return tuple(summaries)


def get_task(mg: Any, task_ref: str, *, scope: Any = None) -> TaskDefinitionVersion | None:
    """Resolve one task ref to a task version."""
    task_id, version = _split_task_ref(task_ref)
    candidates = [item for item in _task_versions(read_task_ledger_payload(mg, scope=scope)) if item.task_id == task_id]
    if version is not None:
        return next((item for item in candidates if item.version == version), None)
    active = [item for item in candidates if item.status == "active"]
    if len(active) > 1:
        raise ValueError(f"task {task_id!r} has multiple active versions")
    return active[0] if active else None


def resolve_task(mg: Any, task_ref: str, *, scope: Any = None) -> ResolvedTask | None:
    """Return a transportable task snapshot for execution."""
    task = get_task(mg, task_ref, scope=scope)
    return None if task is None else task.resolved()


def list_runs(
    mg: Any,
    *,
    status: str | None = None,
    task_id: str | None = None,
    max_count: int | None = None,
    scope: Any = None,
) -> tuple[RunSummary, ...]:
    """Return run summaries from the selected run ledger."""
    return RunLedgerStore(mg, scope=scope).list_runs(status=status, task_id=task_id, max_count=max_count)


def get_run(mg: Any, run_ref: str, *, scope: Any = None) -> RunRecord | None:
    """Return one run by exact run identity."""
    if not isinstance(run_ref, str) or not run_ref:
        raise ValueError("run_ref must be a non-empty string")
    return RunLedgerStore(mg, scope=scope).get(run_ref)


def get_run_args(mg: Any, args_ref: str, *, scope: Any = None) -> Mapping[str, object] | None:
    """Return one persisted run-argument payload by exact args ref."""
    if not isinstance(args_ref, str) or not args_ref:
        raise ValueError("args_ref must be a non-empty string")
    return RunLedgerStore(mg, scope=scope).get_args(args_ref)


def resolve_run_selector(mg: Any, run_ref: str, *, scope: Any = None) -> RunRecord | None:
    """Resolve a run selector, including ``@latest`` and unambiguous short prefixes."""
    if not isinstance(run_ref, str) or not run_ref:
        raise ValueError("run_ref must be a non-empty string")
    if run_ref == "@latest":
        latest = _latest_run_ref(mg, scope=scope)
        if latest is not None:
            return get_run(mg, latest, scope=scope)
        records = _selected_run_records(mg, scope=scope)
        return records[-1] if records else None
    exact_record = get_run(mg, run_ref, scope=scope)
    if exact_record is not None:
        return exact_record
    records = _selected_run_records(mg, scope=scope)
    matches = [record for record in records if record.run_ref.startswith(run_ref)]
    if len(matches) > 1:
        raise ValueError(f"run ref {run_ref!r} is ambiguous")
    return matches[0] if matches else None


def show_run(mg: Any, run_ref: str, *, scope: Any = None) -> RunRecord | None:
    """Resolve a run selector, including ``@latest`` and unambiguous short prefixes."""
    return resolve_run_selector(mg, run_ref, scope=scope)


def trace_exact_run(
    mg: Any,
    run_ref: str,
    *,
    events: bool = False,
    scope: Any = None,
) -> RunTrace | Mapping[str, object] | None:
    """Read the materialized trace associated with an exact run identity."""
    return _trace_record(mg, get_run(mg, run_ref, scope=scope), events=events)


def trace_run(
    mg: Any, run_ref: str, *, events: bool = False, scope: Any = None
) -> RunTrace | Mapping[str, object] | None:
    """Read the materialized trace associated with a run selector."""
    return _trace_record(mg, resolve_run_selector(mg, run_ref, scope=scope), events=events)


def _trace_record(
    mg: Any,
    record: RunRecord | None,
    *,
    events: bool = False,
) -> RunTrace | Mapping[str, object] | None:
    del events
    if record is None:
        return None
    if record.operation_refs.trace_head is None:
        if record.trace_ref is not None:
            raise TraceNotMaterializedError(
                f"run {record.run_ref!r} has provider-neutral trace_ref but no materialized trace_head"
            )
        return None
    payload = mg.read_trace_revision(record.operation_refs.trace_head)
    return None if payload is None else RunTrace(payload)


def run_vcscore_projection_for_exact_run(mg: Any, run_ref: str, *, scope: Any = None) -> Mapping[str, object] | None:
    """Return read-only vcs-core citations carried by one exact run identity."""
    return _run_vcscore_projection(get_run(mg, run_ref, scope=scope))


def run_vcscore_projection(mg: Any, run_ref: str, *, scope: Any = None) -> Mapping[str, object] | None:
    """Return read-only vcs-core citations carried by one run selector."""
    return _run_vcscore_projection(resolve_run_selector(mg, run_ref, scope=scope))


def _run_vcscore_projection(record: RunRecord | None) -> Mapping[str, object] | None:
    if record is None:
        return None
    refs = record.operation_refs
    operation_show = None
    if refs.runtime_operation is not None:
        operation_show = ("vcs-core", "operation", "show", refs.runtime_operation)
    return {
        "schema": "shepherd.workspace_control.run_vcscore_projection.v2",
        "run_ref": record.run_ref,
        "task_id": record.task_id,
        "status": record.status,
        "provider": record.provider,
        "enforcement": record.enforcement,
        "execution_evidence": record.execution_evidence.to_json(),
        "runtime_operation": refs.runtime_operation,
        "authority_operation": refs.authority_operation,
        "authority_settlement_operation": refs.authority_settlement_operation,
        "operation_show": operation_show,
        "trace_head": refs.trace_head,
        "trace_show": ("shepherd", "run", "trace", record.run_ref) if refs.trace_head is not None else None,
        "run_start_revision": refs.run_start_revision,
        "input_workspace_world_oid": record.input_workspace_world_oid,
        "terminal_workspace_world_oid": record.terminal_workspace_world_oid,
        "published_workspace_output_world_oid": run_workspace_output_world_oid(record),
    }


def run_output_citations(
    mg: Any,
    *,
    run_ref: str | None = None,
    binding: str | None = None,
    scope: Any = None,
) -> tuple[RunOutputCitationRef, ...]:
    """Return run-ledger output citations, without claiming custody state."""
    return _run_output_citations_for_records(
        _records_for_run_selector(mg, run_ref, scope=scope),
        binding=binding,
    )


def run_output_citations_for_exact_run(
    mg: Any,
    *,
    run_ref: str,
    binding: str | None = None,
    scope: Any = None,
) -> tuple[RunOutputCitationRef, ...]:
    """Return run-ledger output citations for one exact run identity."""
    return _run_output_citations_for_records(
        _records_for_exact_run(mg, run_ref, scope=scope),
        binding=binding,
    )


def _run_output_citations_for_records(
    records: tuple[RunRecord, ...],
    *,
    binding: str | None,
) -> tuple[RunOutputCitationRef, ...]:
    citations: list[RunOutputCitationRef] = []
    for record in records:
        for citation in record.outputs.values():
            if binding is None or citation.binding == binding:
                citations.append(citation)
    return tuple(citations)


def outputs_for_run(
    mg: Any,
    *,
    run_ref: str | None = None,
    parent: Any = None,
    binding: str | None = None,
    state: str | None = None,
    scope: Any = None,
    trace_store: Any = None,
    descriptor_resolver: DescriptorResolver | None = None,
    read_context: Any = None,
) -> tuple[RunOutputRef, ...]:
    """Return product-visible run outputs after retained-custody validation."""
    citations = run_output_citations(
        mg,
        run_ref=run_ref,
        binding=binding,
        scope=scope,
    )
    return _resolve_run_outputs(
        mg,
        citations=citations,
        parent=parent,
        binding=binding,
        state=state,
        trace_store=trace_store,
        descriptor_resolver=descriptor_resolver,
        read_context=read_context,
    )


def outputs_for_exact_run(
    mg: Any,
    *,
    run_ref: str,
    parent: Any = None,
    binding: str | None = None,
    state: str | None = None,
    scope: Any = None,
    trace_store: Any = None,
    descriptor_resolver: DescriptorResolver | None = None,
    read_context: Any = None,
) -> tuple[RunOutputRef, ...]:
    """Return product-visible run outputs for one exact run identity."""
    citations = run_output_citations_for_exact_run(
        mg,
        run_ref=run_ref,
        binding=binding,
        scope=scope,
    )
    return _resolve_run_outputs(
        mg,
        citations=citations,
        parent=parent,
        binding=binding,
        state=state,
        trace_store=trace_store,
        descriptor_resolver=descriptor_resolver,
        read_context=read_context,
    )


def _resolve_run_outputs(
    mg: Any,
    *,
    citations: tuple[RunOutputCitationRef, ...],
    parent: Any,
    binding: str | None,
    state: str | None,
    trace_store: Any,
    descriptor_resolver: DescriptorResolver | None,
    read_context: Any,
) -> tuple[RunOutputRef, ...]:
    return RunOutputResolver(
        mg,
        parent=parent,
        binding=binding,
        state=state,
        trace_store=trace_store,
        descriptor_resolver=descriptor_resolver,
        read_context=read_context,
    ).resolve(citations)


def _read_selected_payload(mg: Any, binding: str, *, scope: Any) -> Mapping[str, object] | None:
    reader = getattr(mg, "read_selected_binding_revision", None)
    if reader is None:
        raise TypeError("workspace-control queries require VcsCore.read_selected_binding_revision")
    payload = reader(binding, scope=scope)
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise TypeError(f"selected {binding!r} ledger payload must be an object")
    return payload


def _list_selected_json_records(
    mg: Any,
    store: KeyedJsonTreeStore,
    *,
    scope: Any,
) -> tuple[dict[str, object], ...]:
    reader = getattr(mg, "read_selected_binding_json_entries", None)
    if not callable(reader):
        return ()
    return tuple(value for _path, value in reader(RUN_LEDGER_BINDING, store.prefix_path(), scope=scope))


def _selected_run_records(mg: Any, *, scope: Any) -> tuple[RunRecord, ...]:
    return RunLedgerStore(mg, scope=scope).list_run_records()


def _latest_run_ref(mg: Any, *, scope: Any) -> str | None:
    payload = _read_selected_payload(mg, RUN_LEDGER_BINDING, scope=scope)
    if payload is None:
        return None
    if payload.get("storage_shape") == RUN_LEDGER_STORAGE_SHAPE:
        latest = payload.get("latest_run_ref")
        return latest if isinstance(latest, str) and latest else None
    records = _run_records(payload)
    return records[-1].run_ref if records else None


def _json_record_map(rows: tuple[dict[str, object], ...], key_field: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for row in rows:
        key = row.get(key_field)
        if isinstance(key, str) and key:
            result[key] = dict(row)
    return result


def _records_for_run_selector(
    mg: Any,
    run_ref: str | None,
    *,
    scope: Any,
) -> tuple[RunRecord, ...]:
    if run_ref is None:
        return RunLedgerStore(mg, scope=scope).list_run_records()
    record = resolve_run_selector(mg, run_ref, scope=scope)
    return () if record is None else (record,)


def _records_for_exact_run(
    mg: Any,
    run_ref: str,
    *,
    scope: Any,
) -> tuple[RunRecord, ...]:
    record = get_run(mg, run_ref, scope=scope)
    return () if record is None else (record,)


def _task_versions(payload: Mapping[str, object] | None) -> tuple[TaskDefinitionVersion, ...]:
    if payload is None:
        return ()
    _require_schema(payload, TASK_LEDGER_SCHEMA)
    raw_tasks = payload.get("tasks", {})
    if not isinstance(raw_tasks, Mapping):
        raise TypeError("task ledger payload field 'tasks' must be an object")
    versions: list[TaskDefinitionVersion] = []
    for task_id, raw_versions in raw_tasks.items():
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task ledger keys must be non-empty task ids")
        if not isinstance(raw_versions, list | tuple):
            raise TypeError(f"task ledger versions for {task_id!r} must be a list")
        for raw in raw_versions:
            if not isinstance(raw, Mapping):
                raise TypeError(f"task ledger version for {task_id!r} must be an object")
            version = TaskDefinitionVersion.from_json(raw)
            if version.task_id != task_id:
                raise ValueError(f"task ledger key {task_id!r} disagrees with version task_id {version.task_id!r}")
            versions.append(version)
    return tuple(versions)


def _run_records(payload: Mapping[str, object] | None) -> tuple[RunRecord, ...]:
    if payload is None:
        return ()
    _require_schema(payload, RUN_LEDGER_SCHEMA)
    raw_runs = payload.get("runs", ())
    if not isinstance(raw_runs, list | tuple):
        raise TypeError("run ledger payload field 'runs' must be a list")
    records: list[RunRecord] = []
    seen: set[str] = set()
    for raw in raw_runs:
        if not isinstance(raw, Mapping):
            raise TypeError("run ledger entries must be objects")
        record = RunRecord.from_json(raw)
        if record.run_ref in seen:
            raise ValueError(f"duplicate run_ref in run ledger: {record.run_ref!r}")
        seen.add(record.run_ref)
        records.append(record)
    return tuple(records)


def _split_task_ref(task_ref: str) -> tuple[str, str | None]:
    if not isinstance(task_ref, str) or not task_ref:
        raise ValueError("task_ref must be a non-empty string")
    if "@" not in task_ref:
        return task_ref, None
    task_id, version = task_ref.rsplit("@", 1)
    if not task_id or not version:
        raise ValueError("task_ref must be shaped as task_id@version")
    return task_id, version


def _require_schema(payload: Mapping[str, object], expected: str) -> None:
    actual = payload.get("schema")
    if actual != expected:
        raise ValueError(f"unsupported ledger schema: expected {expected!r}, got {actual!r}")
