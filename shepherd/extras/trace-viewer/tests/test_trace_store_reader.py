"""TraceStore projection tests for the viewer source adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from shepherd2.schemas.execution import complete_execution_batch, create_execution_batch
from shepherd2.schemas.relations import ExecutionRelation
from shepherd_trace_viewer.serde import to_json
from shepherd_trace_viewer.trace_store_reader import read_trace_store_view

from shepherd2 import (
    AppendBatch,
    AppendContext,
    AppendGroup,
    FactDraft,
    OwnerCutoffSpec,
    SQLiteTraceStore,
    TaskControl,
    task,
)

if TYPE_CHECKING:
    from pathlib import Path

# Replay-relation projection (replay_control / replay_basis edges) requires the
# kernel to record replay fact-id fields on execution relations. This branch
# (block-reversibility line) does not emit them, so the dedicated replay-edge
# test is skipped until that capability lands. The projection code itself stays
# inert and harmless when those fields are absent.
_KERNEL_RECORDS_REPLAY_RELATIONS = "replay_basis_fact_id" in ExecutionRelation.__dataclass_fields__

TRUSTED = AppendContext(
    actor_ref="runtime:test",
    presented_witness_refs=("trusted:internal",),
    schema_version_set="shepherd2-slice-a",
    trust_mode="internal",
)


def _draft(kind: str, value: int) -> FactDraft:
    return FactDraft(
        kind_label=kind,
        mode="capture",
        schema_ref=f"shepherd2.viewer.{kind}.v1",
        payload={"value": value},
    )


def _store_with_cut(path: Path) -> tuple[str, tuple[str, ...]]:
    with SQLiteTraceStore(path) as store:
        receipt = store.append(
            TRUSTED,
            AppendBatch(
                append_intent_id="intent:viewer",
                groups=(
                    AppendGroup(
                        trace_owner_id="exec:viewer",
                        fact_drafts=(_draft("first", 1), _draft("second", 2)),
                    ),
                ),
            ),
        )
        cut = store.publish_cut(
            TRUSTED,
            OwnerCutoffSpec(
                frontier_id="frontier:viewer",
                target_trace_owner_id="exec:viewer",
                through_fact_id=receipt.fact_ids[-1],
            ),
        )
    return cut.frontier_id, receipt.fact_ids


def test_trace_store_cut_projects_to_v3_view(tmp_path: Path) -> None:
    store_path = tmp_path / "trace.sqlite"
    cut_id, fact_ids = _store_with_cut(store_path)

    view = read_trace_store_view(store_path, selector="cut", selector_value=cut_id)
    data = to_json(view)

    assert data["schema_version"] == "shepherd.trace-view.v3"
    assert data["source"]["source_kind"] == "trace_store_slice"
    assert data["source"]["visibility_profile"] == "payload"
    assert data["source"]["mode_filter"] == "both"
    assert [node["payload"]["record_id"] for node in data["nodes"] if node["role"] == "record"] == list(fact_ids)
    assert data["lanes"][0]["node_ids"][0].startswith("occ:exec:viewer:0:")
    assert any(edge["kind"] == "owner_path" for edge in data["edges"])


def test_trace_store_labels_provider_and_workspace_events(tmp_path: Path) -> None:
    store_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(store_path) as store:
        receipt = store.append(
            TRUSTED,
            AppendBatch(
                append_intent_id="intent:effects",
                groups=(
                    AppendGroup(
                        trace_owner_id="exec:effects",
                        fact_drafts=(
                            FactDraft(
                                kind_label="provider.invocation.started",
                                mode="capture",
                                schema_ref="shepherd.provider.invocation.started.v1",
                                payload={
                                    "kind": "provider.invocation.started",
                                    "provider_id": "claude-headless",
                                    "invocation_id": "inv-1",
                                    "event_id": "inv-1:started",
                                    "sequence": 0,
                                },
                            ),
                            FactDraft(
                                kind_label="tool.result",
                                mode="capture",
                                schema_ref="shepherd.tool.result.v1",
                                payload={
                                    "kind": "tool.call.completed",
                                    "status": "ok",
                                    "provider_id": "claude-headless",
                                    "invocation_id": "inv-1",
                                    "event_id": "inv-1:tool-complete",
                                    "sequence": 1,
                                    "payload": {"tool_name": "Write"},
                                },
                            ),
                            FactDraft(
                                kind_label="workspace.write",
                                mode="capture",
                                schema_ref="shepherd.workspace.write.v1",
                                payload={
                                    "operation": "write",
                                    "path": "index.html",
                                    "scope": "contour-map",
                                    "source": "provider_write",
                                    "byte_length": 123,
                                    "content_digest": "sha256:abc",
                                },
                            ),
                        ),
                    ),
                ),
            ),
        )
        cut = store.publish_cut(
            TRUSTED,
            OwnerCutoffSpec(
                frontier_id="frontier:effects",
                target_trace_owner_id="exec:effects",
                through_fact_id=receipt.fact_ids[-1],
            ),
        )

    view = read_trace_store_view(store_path, selector="cut", selector_value=cut.frontier_id)
    labels = [node["label"] for node in to_json(view)["nodes"]]

    assert "provider started · claude-headless" in labels
    assert "tool ok · Write" in labels
    assert "write index.html" in labels


def test_trace_store_hides_execution_created_bookkeeping(tmp_path: Path) -> None:
    store_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(store_path) as store:
        created = store.append(
            TRUSTED,
            create_execution_batch(
                append_intent_id="exec:create",
                execution_id="exec:compact",
                task_ref="tests:body",
                inputs={},
            ),
        )
        completed = store.append(
            TRUSTED,
            complete_execution_batch(
                append_intent_id="exec:complete",
                execution_id="exec:compact",
                outputs={},
                caused_by=(created.fact_ids[-1],),
            ),
        )
        cut = store.publish_cut(
            TRUSTED,
            OwnerCutoffSpec(
                frontier_id="frontier:compact",
                target_trace_owner_id="exec:compact",
                through_fact_id=completed.fact_ids[-1],
            ),
        )

    view = read_trace_store_view(store_path, selector="cut", selector_value=cut.frontier_id)
    data = to_json(view)
    kinds_by_id = {node["id"]: node["kind"] for node in data["nodes"]}

    assert "execution_created" not in kinds_by_id.values()
    assert [kinds_by_id[node_id] for node_id in data["lanes"][0]["node_ids"]] == [
        "execution_started",
        "execution_completed",
    ]
    assert all(edge["source"] in kinds_by_id and edge["target"] in kinds_by_id for edge in data["edges"])


def test_trace_store_shape_only_projects_record_shapes(tmp_path: Path) -> None:
    store_path = tmp_path / "trace.sqlite"
    cut_id, _fact_ids = _store_with_cut(store_path)

    view = read_trace_store_view(store_path, selector="cut", selector_value=cut_id, visibility="shape_only")

    assert {node.role for node in view.nodes} == {"record_shape"}
    assert {resource.kind for resource in view.resources} >= {"context_anchor", "witness_anchor"}
    assert all(not node.body for node in view.nodes if node.role == "record_shape")


@pytest.mark.skipif(
    not _KERNEL_RECORDS_REPLAY_RELATIONS,
    reason="kernel does not record replay relation fact-ids on this branch (reversibility blocked)",
)
def test_replay_task_projects_control_and_basis_edges(tmp_path: Path) -> None:
    @task
    class ReplacementAttempt:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self) -> dict[str, int]:
            return {"y": self.x + 10}

    @task
    class ReverseAndReplay:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self, control: TaskControl) -> dict[str, int]:
            checkpoint = control.publish("checkpoint", {"name": "known good"})
            control.publish("attempt_started", {"strategy": "first"})
            control.publish("attempt_failed", {"strategy": "first"})
            control.publish("revert_requested", {"basis_fact_id": checkpoint.envelope.fact_id})
            replay = control.replay(ReplacementAttempt, basis=checkpoint, x=self.x)
            replayed = replay.wait()
            return {"y": replayed.outputs["y"]}

    store_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(store_path) as store:
        run = ReverseAndReplay.start(store=store, run_id="run:viewer-replay", x=5)  # type: ignore[attr-defined]
        execution = run.wait()

    view = read_trace_store_view(store_path, selector="causal_root", selector_value=execution.terminal_fact_id or "")

    assert {edge.kind for edge in view.edges} >= {"replay_control", "replay_basis"}
    assert any(node.label == "revert" for node in view.nodes)
    assert any(node.label == "replay" for node in view.nodes)
    assert len(view.lanes) == 2
