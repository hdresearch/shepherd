"""Retained workspace-output publication transitions.

This module is intentionally workspace-only. A future multi-output surface should
add an explicit public transition rather than broadening this one by parameter.

Authority contract:

* vcs-core retained-output custody is the state authority;
* trace descriptors are durable evidence/query material for the current launch floor;
* the selected ``shepherd.runs`` ledger is a product/control projection that cites the
  retained output, not the owner of settlement custody.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shepherd2.kernel.facts import TRUSTED_READ_CONTEXT
from shepherd2.trace_store import SQLiteTraceStore

from shepherd_dialect.workspace_control.output_publication import publish_run_output_descriptor
from shepherd_dialect.workspace_control.outputs import (
    RunOutputResolutionError,
    RunOutputResolver,
    TraceDescriptorNotResolvedError,
    run_output_publication_from_retained_row,
)
from shepherd_dialect.workspace_control.queries import get_run
from shepherd_dialect.workspace_control.run_ledger import publish_run_record
from shepherd_dialect.workspace_control.schemas import (
    RunOutputCitationRef,
    RunRecord,
    RunTerminalization,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class RetainedWorkspaceOutputPublicationError(RuntimeError):
    """Raised when retained workspace-output publication cannot be completed safely."""


def publish_retained_workspace_output(
    mg: Any,
    *,
    run_ref: str,
    trace_store_path: str | Path,
    scope: Any = None,
) -> RunRecord:
    """Publish or repair the retained workspace-output citation for one run.

    This is a post-terminal projection transition. It may publish trace descriptor
    evidence and update the run ledger citation, but it preserves the original run
    terminal revision identity.
    """
    record = get_run(mg, run_ref, scope=scope)
    if record is None:
        raise RetainedWorkspaceOutputPublicationError(f"cannot publish workspace output for missing run {run_ref!r}")
    retained = _validated_retained_workspace_row(mg, record)
    terminalization = record.terminalization
    if terminalization.output_publication_status == "published":
        citation = _published_workspace_citation(record)
        _validate_citation_matches_retained(citation, retained, record)
        if _citation_descriptor_resolves(mg, trace_store_path, citation):
            return record
    else:
        if terminalization.output_publication_status not in {"failed", "pending"}:
            raise RetainedWorkspaceOutputPublicationError(
                f"run {record.run_ref!r} output publication is not retryable: "
                f"{terminalization.output_publication_status!r}"
            )
        if record.outputs:
            raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} already carries output citations")

    if record.trace_ref is None:
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} has no trace_ref for output publication")

    draft = run_output_publication_from_retained_row(retained, trace_ref=record.trace_ref, output_name="workspace")
    citation = publish_run_output_descriptor(trace_store_path, draft)
    _validate_citation_matches_retained(citation, retained, record)
    updated = replace(
        record,
        outputs={"workspace": citation},
        terminal_workspace_world_oid=citation.output_world_oid,
        terminalization=RunTerminalization(
            body_status="completed",
            world_disposition="retained",
            output_publication_status="published",
            retained_custody=terminalization.retained_custody,
        ),
        error=None,
    )
    publish_run_record(mg, updated, scope=scope)
    return updated


def _published_workspace_citation(record: RunRecord) -> RunOutputCitationRef:
    if set(record.outputs) != {"workspace"}:
        raise RetainedWorkspaceOutputPublicationError(
            f"run {record.run_ref!r} claims published output without citation"
        )
    return record.outputs["workspace"]


def _validated_retained_workspace_row(mg: Any, record: RunRecord) -> Any:
    terminalization = record.terminalization
    if record.status != "retained" or terminalization.world_disposition != "retained":
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} is not a retained run")
    custody = terminalization.retained_custody
    if custody is None:
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} has no retained custody")
    if custody.binding != "workspace":
        raise RetainedWorkspaceOutputPublicationError(
            f"run {record.run_ref!r} retained custody binding is not workspace"
        )
    if (
        record.terminal_workspace_world_oid is not None
        and record.terminal_workspace_world_oid != custody.output_world_oid
    ):
        raise RetainedWorkspaceOutputPublicationError(
            f"run {record.run_ref!r} terminal workspace world disagrees with retained custody"
        )
    reader = getattr(mg, "list_retained_outputs", None)
    if reader is None:
        raise RetainedWorkspaceOutputPublicationError(
            "retained workspace-output publication requires VcsCore.list_retained_outputs"
        )
    rows = tuple(reader(parent=None, binding="workspace", state=None))
    matches = [row for row in rows if custody.matches_retained_output(row)]
    if not matches:
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} retained custody row is missing")
    if len(matches) > 1:
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} retained custody row is ambiguous")
    retained = matches[0]
    if getattr(retained, "state", None) == "invalid":
        reason = getattr(retained, "invalid_reason", None)
        suffix = f": {reason}" if isinstance(reason, str) and reason else ""
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} retained custody row is invalid{suffix}")
    _validate_retained_row_matches_run(record, retained)
    return retained


def _validate_retained_row_matches_run(record: RunRecord, retained: Any) -> None:
    custody = record.terminalization.retained_custody
    if custody is None:
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} has no retained custody")
    if not custody.matches_retained_output(retained):
        raise RetainedWorkspaceOutputPublicationError(
            f"run {record.run_ref!r} retained custody disagrees with custody row"
        )
    # Do not compare retained.parent_basis_world_oid to record.input_workspace_world_oid here.
    # The run field is a selected workspace-world identity; retained custody carries the
    # workspace binding basis. Real vcs-core runs can legitimately distinguish them.


def _validate_citation_matches_retained(citation: RunOutputCitationRef, retained: Any, record: RunRecord) -> None:
    draft = run_output_publication_from_retained_row(retained, trace_ref=citation.trace_ref, output_name="workspace")
    expected: Mapping[str, object] = {
        "output_name": draft.output_name,
        "output_id": draft.output_id,
        "binding": draft.binding,
        "store_id": draft.store_id,
        "resource_id": draft.resource_id,
        "materialization_kind": draft.materialization_kind,
        "custody_ref": draft.custody_ref,
        "output_world_oid": draft.output_world_oid,
        "parent_basis_world_oid": draft.parent_basis_world_oid,
    }
    for field_name, expected_value in expected.items():
        if getattr(citation, field_name) != expected_value:
            raise RetainedWorkspaceOutputPublicationError(
                f"run {record.run_ref!r} output citation field {field_name!r} disagrees with retained custody"
            )
    if record.trace_ref is not None and citation.trace_ref != record.trace_ref:
        raise RetainedWorkspaceOutputPublicationError(f"run {record.run_ref!r} output citation trace_ref disagrees")


def _citation_descriptor_resolves(
    mg: Any,
    trace_store_path: str | Path,
    citation: RunOutputCitationRef,
) -> bool:
    path = Path(trace_store_path)
    if str(path) != ":memory:" and not path.exists():
        return False
    store = SQLiteTraceStore(path)
    try:
        RunOutputResolver(mg, trace_store=store, read_context=TRUSTED_READ_CONTEXT).resolve((citation,))
    except TraceDescriptorNotResolvedError:
        return False
    except RunOutputResolutionError:
        raise
    finally:
        store.close()
    return True


__all__ = [
    "RetainedWorkspaceOutputPublicationError",
    "publish_retained_workspace_output",
]
