from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from shepherd_dialect import CHILD_RUN_COMPLETED, CHILD_VALUE_COMPLETED, ask, handle, task, workspace
from shepherd_dialect.nucleus import Finished, reset_workspace_for_tests
from shepherd_dialect.trace_events import log_entry_operation_id
from shepherd_dialect.workspace_control import show_run, trace_run

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_workspace() -> Iterator[None]:
    reset_workspace_for_tests()
    yield
    reset_workspace_for_tests()


@dataclass(frozen=True)
class Review:
    decision: str
    risk: str
    summary: str


@dataclass(frozen=True)
class ReviewResult:
    branch: str
    review: Review
    child_run_ref: str
    predicate_fired: bool
    carrier_path: str | None = None


def test_tier1_cautious_review_value_child_mechanics_not_full_gate(tmp_path) -> None:
    ws = workspace(model=object(), root=str(tmp_path))
    child_started: list[str] = []
    reviewed_children: list[str] = []
    operator_modes: list[str] = []

    @task(may="ReadOnly")
    def review_change(diff: str) -> Review:
        child_started.append(diff)
        risk = "critical" if "DROP TABLE" in diff else "low"
        return Review(decision="needs-operator" if risk == "critical" else "safe", risk=risk, summary=diff)

    @task(may="ReadOnly")
    def cautious_review(child: Any, diff: str, mode: str) -> ReviewResult:
        child_run = child.detailed(diff)
        child_trace = child_run.trace
        child_events = child_trace.filter("run.lifecycle") if child_trace is not None else ()
        predicate_fired = bool(child_events) and child_run.unwrap().risk == "critical"
        reviewed_children.append(child_run.ref.id)
        operator_modes.append(mode)
        branch = ask("operator.decision", {"mode": mode, "child_run_ref": child_run.ref.id})
        return ReviewResult(
            branch=branch,
            review=child_run.unwrap(),
            child_run_ref=child_run.ref.id,
            predicate_fired=predicate_fired,
        )

    def operator(request: dict[str, str]) -> str:
        return request["mode"]

    with handle("operator.decision", operator):
        apply_run = cautious_review.detailed(review_change, "DROP TABLE users", "apply")
        discard_run = cautious_review.detailed(review_change, "DROP TABLE users", "discard")

    assert isinstance(apply_run.outcome, Finished)
    assert isinstance(discard_run.outcome, Finished)
    apply_result = apply_run.outcome.value
    discard_result = discard_run.outcome.value

    # 1. Child task runs.
    assert child_started == ["DROP TABLE users", "DROP TABLE users"]
    assert apply_result.child_run_ref != apply_run.ref.id
    assert discard_result.child_run_ref != discard_run.ref.id

    # 2. Review predicate fires before the parent chooses a branch.
    assert apply_result.predicate_fired is True
    assert discard_result.predicate_fired is True
    assert reviewed_children == [apply_result.child_run_ref, discard_result.child_run_ref]

    # 3. Apply/discard both branch through the same parent program.
    assert apply_result.branch == "apply"
    assert discard_result.branch == "discard"
    assert operator_modes == ["apply", "discard"]

    # 4. Nested value trace survives and carries honest same-process child identity.
    for parent_run, result in ((apply_run, apply_result), (discard_run, discard_result)):
        assert parent_run.trace is not None
        assert parent_run.trace.filter(CHILD_RUN_COMPLETED) == ()
        (child_event,) = parent_run.trace.filter(CHILD_VALUE_COMPLETED)
        assert child_event["child_run_ref"] == result.child_run_ref
        assert child_event["child_trace_token"].startswith("memory-trace:")
        assert child_event["child_lifecycle"] in {"finished", "failed"}
        assert child_event["evidence_level"] == "same_process_value"
        assert child_event["trace_materialized"] is False
        assert child_event["ledger_visible"] is False
        assert trace_run(ws._mg, result.child_run_ref) is None

    # This green mechanics test is explicitly not the full Gate-3 nested-reversibility claim.
    assert apply_run.trace.summary()["child_runs"] == ()
    assert discard_run.trace.summary()["child_runs"] == ()
    assert apply_run.trace.summary()["value_children"][0]["evidence_level"] == "same_process_value"
    assert discard_run.trace.summary()["value_children"][0]["evidence_level"] == "same_process_value"


def test_tier1_cautious_review_nested_reversibility_gate(tmp_path) -> None:
    ws = workspace(model=object(), root=str(tmp_path))
    accepted = tmp_path / "accepted.patch"
    discarded = tmp_path / "discarded.patch"
    child_started: list[str] = []
    reviewed_children: list[str] = []

    @task(may="ReadOnly")
    def review_change(diff: str) -> Review:
        child_started.append(diff)
        return Review(decision="needs-operator", risk="critical", summary=diff)

    @task(may="Permissive")
    def cautious_review(child: Any, diff: str, mode: str, working_path: str) -> ReviewResult:
        child_run = child.detailed(diff)
        review = child_run.unwrap()
        reviewed_children.append(child_run.ref.id)
        branch = ask("operator.decision", {"mode": mode, "child_run_ref": child_run.ref.id})
        carrier = Path(working_path)
        if branch == "apply":
            (carrier / accepted.name).write_text(review.summary, encoding="utf-8")
        elif branch == "discard":
            pass
        else:
            raise ValueError(f"unexpected operator branch: {branch}")
        return ReviewResult(
            branch=branch,
            review=review,
            child_run_ref=child_run.ref.id,
            predicate_fired=review.risk == "critical",
            carrier_path=str(carrier),
        )

    def operator(request: dict[str, str]) -> str:
        return request["mode"]

    with handle("operator.decision", operator):
        apply_run = cautious_review.detailed(review_change, "DROP TABLE users", "apply")
        discard_run = cautious_review.detailed(review_change, "DROP TABLE users", "discard")

    assert isinstance(apply_run.outcome, Finished)
    assert isinstance(discard_run.outcome, Finished)
    apply_result = apply_run.outcome.value
    discard_result = discard_run.outcome.value

    # 1. The concrete child task runs in both branches.
    assert child_started == ["DROP TABLE users", "DROP TABLE users"]
    assert apply_result.child_run_ref != apply_run.ref.id
    assert discard_result.child_run_ref != discard_run.ref.id

    # 2. The parent review predicate fires before choosing the branch.
    assert apply_result.predicate_fired is True
    assert discard_result.predicate_fired is True
    assert reviewed_children == [apply_result.child_run_ref, discard_result.child_run_ref]

    # 3. Apply/discard both branch through the same parent program.
    assert apply_result.branch == "apply"
    assert discard_result.branch == "discard"
    assert apply_result.carrier_path is not None
    assert discard_result.carrier_path is not None
    assert Path(apply_result.carrier_path).resolve() != tmp_path.resolve()
    assert Path(discard_result.carrier_path).resolve() != tmp_path.resolve()

    # 4. Trace evidence remains the honest Path-A value-child contract.
    for parent_run, result in ((apply_run, apply_result), (discard_run, discard_result)):
        assert parent_run.trace is not None
        assert parent_run.trace.filter(CHILD_RUN_COMPLETED) == ()
        (child_event,) = parent_run.trace.filter(CHILD_VALUE_COMPLETED)
        assert child_event["child_run_ref"] == result.child_run_ref
        assert child_event["evidence_level"] == "same_process_value"
        assert child_event["trace_materialized"] is False
        assert child_event["ledger_visible"] is False
        assert trace_run(ws._mg, result.child_run_ref) is None

    # 5. Nested reversibility is observable as parent-applied / parent-discarded state through the carrier.
    apply_record = show_run(ws._mg, apply_run.ref.id)
    discard_record = show_run(ws._mg, discard_run.ref.id)
    assert apply_record is not None
    assert discard_record is not None
    apply_operation = apply_record.operation_refs.runtime_operation
    discard_operation = discard_record.operation_refs.runtime_operation
    assert apply_operation is not None
    assert discard_operation is not None
    apply_scope = _operation_scope(ws._mg, operation_id=apply_operation)
    discard_scope = _operation_scope(ws._mg, operation_id=discard_operation)
    assert apply_scope is not None
    assert discard_scope is not None
    assert apply_scope != discard_scope
    apply_paths = _merged_file_effect_paths(ws._mg, scope=apply_scope)
    discard_paths = _merged_file_effect_paths(ws._mg, scope=discard_scope)
    assert accepted.name in apply_paths
    assert accepted.name not in discard_paths
    assert discarded.name not in apply_paths
    assert discarded.name not in discard_paths
    selected_paths = _selected_workspace_paths(ws._mg)
    assert accepted.name in selected_paths
    assert discarded.name not in selected_paths
    assert not accepted.exists()
    assert not discarded.exists()


@pytest.mark.xfail(strict=True, reason="V1D-017 durable nested child runtime backend is deferred")
def test_tier1_cautious_review_durable_child_runtime_gate(tmp_path) -> None:
    ws = workspace(model=object(), root=str(tmp_path))

    @task(may="ReadOnly")
    def review_change(diff: str) -> Review:
        return Review(decision="needs-operator", risk="critical", summary=diff)

    @task(may="ReadOnly")
    def cautious_review(child: Any, diff: str) -> str:
        child_run = child.detailed(diff)
        return child_run.ref.id

    run = cautious_review.detailed(review_change, "DROP TABLE users")

    assert isinstance(run.outcome, Finished)
    child_ref = run.outcome.value
    assert run.trace is not None
    (child_event,) = run.trace.filter(CHILD_RUN_COMPLETED)
    assert child_event["child_run_ref"] == child_ref
    assert trace_run(ws._mg, child_ref) is not None


def _operation_scope(mg: Any, *, operation_id: str) -> str | None:
    for entry in mg.log(max_count=40):
        if log_entry_operation_id(entry) == operation_id:
            scope = entry.metadata.get("scope")
            return scope if isinstance(scope, str) else None
    return None


def _merged_file_effect_paths(mg: Any, *, scope: str) -> tuple[str, ...]:
    return tuple(
        str(entry.metadata.get("path"))
        for entry in mg.log(max_count=40)
        if entry.metadata.get("scope") == scope
        and entry.metadata.get("type") in {"FileCreate", "FilePatch"}
        and entry.metadata.get("path") is not None
    )


def _selected_workspace_paths(mg: Any) -> frozenset[str]:
    payload = mg.read_selected_binding_revision("workspace")
    assert isinstance(payload, dict)
    manifest = payload["state_manifest"]
    assert isinstance(manifest, dict)
    entries = manifest["entries"]
    assert isinstance(entries, list)
    return frozenset(
        entry["path"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry.get("state") == "present"
    )
