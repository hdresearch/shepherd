"""CLI loading for durable trace payloads."""

from __future__ import annotations

import argparse
from pathlib import Path

from shepherd_trace_viewer.cli import _load_view_json

from shepherd2 import AppendBatch, AppendContext, AppendGroup, FactDraft, OwnerCutoffSpec, SQLiteTraceStore

FIXTURE = Path(__file__).parent / "fixtures" / "durable-basic.trace.json"

TRUSTED = AppendContext(
    actor_ref="runtime:test",
    presented_witness_refs=("trusted:internal",),
    schema_version_set="shepherd2-slice-a",
    trust_mode="internal",
)


def test_trace_payload_loads_view_json() -> None:
    out = _load_view_json(
        argparse.Namespace(
            trace_payload=FIXTURE,
            trace_store=None,
            trace_rev=None,
            trace_head=False,
            workspace=Path(),
        )
    )
    assert out["schema_version"] == "shepherd.trace-view.v3"
    assert out["run"]["id"] == "run-basic"
    assert len(out["nodes"]) == 3
    assert out["nodes"][1]["role"] == "pointer"


def test_trace_store_loads_view_json(tmp_path: Path) -> None:
    store_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(store_path) as store:
        receipt = store.append(
            TRUSTED,
            AppendBatch(
                append_intent_id="intent:cli",
                groups=(
                    AppendGroup(
                        trace_owner_id="exec:cli",
                        fact_drafts=(
                            FactDraft(
                                kind_label="step",
                                mode="capture",
                                schema_ref="shepherd2.viewer.step.v1",
                                payload={"value": 1},
                            ),
                        ),
                    ),
                ),
            ),
        )
        cut = store.publish_cut(
            TRUSTED,
            OwnerCutoffSpec(
                frontier_id="frontier:cli",
                target_trace_owner_id="exec:cli",
                through_fact_id=receipt.fact_ids[-1],
            ),
        )

    out = _load_view_json(
        argparse.Namespace(
            trace_payload=None,
            trace_store=store_path,
            cut=cut.frontier_id,
            owner=None,
            causal_root=None,
            through=None,
            visibility="payload",
            mode="both",
            actor="trace-viewer",
            trusted_internal=False,
            trace_rev=None,
            trace_head=False,
            workspace=Path(),
        )
    )

    assert out["schema_version"] == "shepherd.trace-view.v3"
    assert out["source"]["source_kind"] == "trace_store_slice"
    assert out["nodes"][0]["payload"]["record_id"] == receipt.fact_ids[0]
