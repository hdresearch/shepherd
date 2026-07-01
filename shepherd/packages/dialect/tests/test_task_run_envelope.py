from __future__ import annotations

import pytest

from shepherd_dialect.runtime_options import RuntimeOptionsError
from shepherd_dialect.task_run_envelope import (
    TASK_RUN_ENVELOPE_SCHEMA,
    TaskRunEnvelopeError,
    normalize_task_run_params,
)


def _envelope() -> dict[str, object]:
    return {
        "schema": TASK_RUN_ENVELOPE_SCHEMA,
        "task_id": "pkg.tasks:demo",
        "args": {"marker": "ok"},
        "may": "ReadOnly",
    }


@pytest.mark.parametrize("field", ["task_id", "args", "may", "runtime"])
def test_envelope_cannot_be_combined_with_authority_or_run_fields(field: str) -> None:
    params = {"envelope": _envelope(), field: "conflict"}
    if field in {"args", "runtime"}:
        params[field] = {}

    with pytest.raises(TaskRunEnvelopeError, match=f"top-level run field\\(s\\): {field}"):
        normalize_task_run_params(params)


def test_envelope_runtime_rejects_authority_shaped_may_field() -> None:
    envelope = _envelope()
    envelope["runtime"] = {"may": "Permissive"}

    with pytest.raises(RuntimeOptionsError, match=r"unknown runtime field\(s\): may"):
        normalize_task_run_params({"envelope": envelope})


def test_legacy_params_normalize_runtime_to_authority_free_envelope_payload() -> None:
    normalized = normalize_task_run_params(
        {
            "task_id": "pkg.tasks:demo",
            "may": "ReadOnly",
            "runtime": {"provider": "static-mock"},
        }
    )

    assert normalized.envelope.to_payload()["runtime"] == {"provider": {"id": "static-mock"}}
    assert normalized.envelope.may == "ReadOnly"
