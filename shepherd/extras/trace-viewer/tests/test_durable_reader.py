"""Durable trace payload reader tests."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from shepherd_trace_viewer.durable_reader import (
    DurableTraceReadError,
    read_trace_payload,
    read_trace_payload_file,
    read_trace_revision,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return read_trace_payload_file(FIXTURES / name)


def test_basic_trace_projects_run_and_lanes() -> None:
    view = fixture("durable-basic.trace.json")
    assert view.source.trace_runtime == "shepherd.trace.provider-neutral.v1"
    assert view.run.id == "run-basic"
    assert view.run.terminal_status == "merged"
    assert [lane.id for lane in view.lanes] == ["task:pkg.mod:run:run-basic"]
    assert [node.id for node in view.nodes] == [
        "task-invocation",
        "workspace-transition",
        "run-lifecycle",
    ]


def test_pointer_and_record_nodes_are_distinct() -> None:
    view = fixture("durable-basic.trace.json")
    by_id = {node.id: node for node in view.nodes}
    assert by_id["task-invocation"].role == "record"
    assert by_id["task-invocation"].record_digest
    assert by_id["workspace-transition"].role == "pointer"
    assert by_id["workspace-transition"].record_digest is None


def test_causal_edges_and_owner_edges_are_preserved() -> None:
    view = fixture("durable-supervised.trace.json")
    kinds = [edge.kind for edge in view.edges]
    assert kinds.count("owner_path") == 4
    assert kinds.count("causal") == 4
    causal = {(edge.source, edge.target) for edge in view.edges if edge.kind == "causal"}
    assert ("decision-1", "file-create") in causal


def test_discarded_trace_uses_lifecycle_terminal_status() -> None:
    view = fixture("durable-discarded.trace.json")
    assert view.run.terminal_status == "discarded"
    assert view.run.summary["head_to"] is None


def test_namespaced_and_effect_families() -> None:
    view = fixture("durable-steps-checks.trace.json")
    families = {node.id: node.family for node in view.nodes}
    assert families["step-1-start"] == "step"
    assert families["check-1"] == "check"
    effect_view = fixture("durable-supervised.trace.json")
    assert {node.id: node.family for node in effect_view.nodes}["file-create"] == "effect"


def test_multiple_owner_paths_become_multiple_lanes() -> None:
    view = fixture("durable-stress.trace.json")
    assert {lane.id for lane in view.lanes} == {
        "task:pkg.mod:run:run-stress",
        "concepts",
        "judging",
    }
    assert view.run.summary["events"] == 11
    assert view.run.summary["families"]["model"] == 3


def test_revert_branch_fixture_preserves_logical_time_and_causality() -> None:
    view = fixture("durable-revert-branch.trace.json")
    assert [node.id for node in view.nodes] == [f"event-{index}" for index in range(1, 9)]
    assert {lane.id: lane.node_ids for lane in view.lanes} == {
        "scope:main": ("event-1", "event-2", "event-3", "event-4", "event-5"),
        "scope:replacement": ("event-6", "event-7", "event-8"),
    }
    by_id = {node.id: node for node in view.nodes}
    assert by_id["event-2"].sequence == 1
    assert by_id["event-2"].label == "known good state"
    assert by_id["event-6"].sequence == 5
    causal = {(edge.source, edge.target) for edge in view.edges if edge.kind == "causal"}
    assert ("event-2", "event-6") in causal
    assert ("event-5", "event-6") in causal


def test_three_scope_branch_fixture_preserves_fan_out_and_fan_in() -> None:
    view = fixture("durable-three-scope-branch.trace.json")
    assert [lane.id for lane in view.lanes] == [
        "scope:parent",
        "scope:child-a",
        "scope:child-b",
        "scope:child-c",
    ]
    by_id = {node.id: node for node in view.nodes}
    assert by_id["parent-2"].sequence < by_id["child-a-1"].sequence
    assert by_id["parent-2"].label == "Branch into three subtasks"
    assert by_id["child-c-2"].sequence < by_id["parent-3"].sequence
    causal = {(edge.source, edge.target) for edge in view.edges if edge.kind == "causal"}
    assert {("parent-2", f"child-{name}-1") for name in ("a", "b", "c")} <= causal
    assert {(f"child-{name}-2", "parent-3") for name in ("a", "b", "c")} <= causal


def test_rejects_unknown_causal_edge() -> None:
    payload = json.loads((FIXTURES / "durable-basic.trace.json").read_text())
    payload["causal_edges"].append(["task-invocation", "ghost"])
    with pytest.raises(DurableTraceReadError, match="unknown event"):
        read_trace_payload(payload)


def test_selected_trace_revision_activates_vcscore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".vcscore").mkdir()
    payload = json.loads((FIXTURES / "durable-basic.trace.json").read_text())
    calls: list[str] = []

    class FakeTaskTraceSubstrateDriver:
        pass

    class FakeStore:
        def __init__(self, repo_path: str) -> None:
            assert repo_path == str(tmp_path / ".vcscore")

    class FakeVcsCore:
        def __init__(self, root: str, *, substrates: list[object], store: FakeStore) -> None:
            assert root == str(tmp_path)
            assert len(substrates) == 1
            assert isinstance(store, FakeStore)
            self.activated = False

        def activate(self) -> None:
            calls.append("activate")
            self.activated = True

        def deactivate(self) -> None:
            calls.append("deactivate")

        def read_trace_revision(self, rev: str | None) -> dict:
            assert rev is None
            assert self.activated
            return payload

    monkeypatch.setitem(
        sys.modules,
        "vcs_core.experimental",
        types.SimpleNamespace(TaskTraceSubstrateDriver=FakeTaskTraceSubstrateDriver),
    )
    monkeypatch.setitem(
        sys.modules,
        "vcs_core.runtime_api",
        types.SimpleNamespace(VcsCore=FakeVcsCore, Store=FakeStore),
    )

    view = read_trace_revision(tmp_path)

    assert calls == ["activate", "deactivate"]
    assert view.run.id == "run-basic"
