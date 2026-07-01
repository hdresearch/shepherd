"""Private publication helpers for trace-owned run outputs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shepherd2 import TRUSTED_APPEND_CONTEXT, AppendBatch, AppendGroup, OwnerCutoffSpec
from shepherd2.trace_store import SQLiteTraceStore

if TYPE_CHECKING:
    from shepherd_dialect.workspace_control.outputs import RunOutputPublicationDraft
    from shepherd_dialect.workspace_control.schemas import RunOutputCitationRef


def publish_run_output_descriptor(
    trace_store_path: str | Path,
    draft: RunOutputPublicationDraft,
) -> RunOutputCitationRef:
    """Publish or reuse one trace-owned RunOutput descriptor and return its run citation."""
    path = Path(trace_store_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteTraceStore(path)
    try:
        receipt = store.append(
            TRUSTED_APPEND_CONTEXT,
            AppendBatch(
                append_intent_id=f"intent:run-output-descriptor:{draft.trace_ref.run_id}:{draft.output_name}",
                groups=(
                    AppendGroup(
                        trace_owner_id=draft.trace_ref.execution_id,
                        fact_drafts=(draft.descriptor_fact(),),
                    ),
                ),
            ),
        )
        store.publish_frontier(
            TRUSTED_APPEND_CONTEXT,
            OwnerCutoffSpec(
                frontier_id=draft.trace_ref.frontier_id,
                target_trace_owner_id=draft.trace_ref.execution_id,
                through_fact_id=receipt.fact_ids[0],
            ),
        )
    finally:
        store.close()
    return draft.citation_ref(descriptor_fact_id=receipt.fact_ids[0])
