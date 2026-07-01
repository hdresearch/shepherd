from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from shepherd_dialect import task, workspace
from shepherd_dialect.nucleus import Failed, reset_workspace_for_tests
from shepherd_dialect.workspace_control import read_run_ledger_payload, show_run

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_workspace() -> Iterator[None]:
    reset_workspace_for_tests()
    yield
    reset_workspace_for_tests()


def test_admission_failure_before_body_writes_failed_run_without_trace_or_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace(model=object(), root=str(tmp_path))
    body_entered = False

    def fail_before_body(*args: Any, **kwargs: Any) -> Any:
        assert args == ("runtime", "run")
        assert "task_body" in kwargs
        raise RuntimeError("admission refused")

    monkeypatch.setattr(ws._mg, "execute_recorded", fail_before_body)

    @task
    def should_not_run() -> str:
        nonlocal body_entered
        body_entered = True
        return "unreachable"

    run = should_not_run.detailed()

    assert body_entered is False
    assert isinstance(run.outcome, Failed)
    assert run.outcome.error_type == "RuntimeError"
    assert run._trace_head is None
    assert run.trace is None

    payload = read_run_ledger_payload(ws._mg)
    assert payload is not None
    assert len(payload["runs"]) == 1

    record = show_run(ws._mg, run.ref.id)
    assert record is not None
    assert record.status == "failed"
    assert record.error is not None
    assert record.error["type"] == "RuntimeError"
    assert record.error["message"] == "admission refused"
    assert record.error["stage"] == "setup"
    assert record.error["phase"] == "admission"
    assert record.operation_refs.trace_head is None
    assert record.terminal_workspace_world_oid is None
    assert record.outputs == {}


def test_body_exception_is_not_classified_as_setup(tmp_path) -> None:
    ws = workspace(model=object(), root=str(tmp_path))
    body_entered = False

    @task
    def raises_in_body() -> str:
        nonlocal body_entered
        body_entered = True
        raise RuntimeError("body failed")

    run = raises_in_body.detailed()

    assert body_entered is True
    assert isinstance(run.outcome, Failed)
    assert run._trace_head is not None

    record = show_run(ws._mg, run.ref.id)
    assert record is not None
    assert record.status == "failed"
    assert record.error == {"type": "RuntimeError", "message": "body failed"}
    assert record.operation_refs.trace_head == run._trace_head
    assert record.terminal_workspace_world_oid is None
