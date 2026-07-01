from __future__ import annotations

import json

import pytest

from shepherd_dialect.workspace_control.identities import (
    RunRef,
    TaskRef,
    WorkspaceRef,
    coerce_exact_run_ref,
    coerce_optional_run_ref,
    coerce_optional_run_selector,
    coerce_run_ref,
    coerce_run_selector,
    coerce_task_ref,
    coerce_workspace_ref,
)


def test_task_ref_is_pure_value_identity_with_payload_roundtrip() -> None:
    ref = TaskRef("sample_tasks.fix_bug@v2")

    payload = json.loads(json.dumps(ref.to_payload()))

    assert str(ref) == "sample_tasks.fix_bug@v2"
    assert TaskRef.from_payload(payload) == ref
    assert coerce_task_ref(ref) == "sample_tasks.fix_bug@v2"
    assert coerce_task_ref("sample_tasks.fix_bug") == "sample_tasks.fix_bug"


@pytest.mark.parametrize("value", ["", "task@", "@v1", "task ref"])
def test_task_ref_rejects_empty_or_malformed_values(value: str) -> None:
    with pytest.raises(ValueError):
        TaskRef(value)


def test_workspace_ref_is_pure_value_identity_with_payload_roundtrip(tmp_path) -> None:
    ref = WorkspaceRef.from_path(tmp_path)

    payload = json.loads(json.dumps(ref.to_payload()))

    assert str(ref) == str(tmp_path.resolve())
    assert WorkspaceRef.from_payload(payload) == ref
    assert coerce_workspace_ref(ref) == str(tmp_path.resolve())
    assert coerce_workspace_ref("workspace-main") == "workspace-main"


def test_run_ref_reuses_runtime_identity_with_payload_roundtrip() -> None:
    ref = RunRef(id="run-123")

    payload = json.loads(json.dumps(ref.to_payload()))

    assert str(ref) == "run-123"
    assert RunRef.from_payload(payload) == ref
    assert coerce_run_ref(ref) == "run-123"
    assert coerce_run_ref("run-123") == "run-123"
    assert coerce_exact_run_ref(ref) == "run-123"
    assert coerce_optional_run_ref(None) is None
    assert coerce_run_selector(ref) == "run-123"
    assert coerce_run_selector("@latest") == "@latest"
    assert coerce_optional_run_selector(None) is None


def test_identity_payloads_fail_closed_for_wrong_schema_or_empty_id() -> None:
    with pytest.raises(ValueError, match="schema"):
        TaskRef.from_payload({"schema": "wrong", "id": "task"})
    with pytest.raises(ValueError, match="non-empty"):
        WorkspaceRef.from_payload({"schema": "shepherd.workspace_control.workspace_ref.v1", "id": ""})
    with pytest.raises(ValueError, match="non-empty"):
        RunRef(id="")
    with pytest.raises(ValueError, match="exact run id"):
        RunRef(id="@latest")
    with pytest.raises(ValueError, match="exact run id"):
        coerce_run_ref("@latest")
