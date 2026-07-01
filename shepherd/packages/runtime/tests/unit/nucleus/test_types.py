from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Sequence
from typing import get_args, get_origin, get_type_hints

import pytest
from shepherd_core.errors import ShepherdError
from shepherd_kernel_v3_reference.proof_envelope import ProofProfile, ProofStrength
from shepherd_runtime.nucleus import (
    DeliveryExhausted,
    DeliveryFailed,
    DeliveryLimits,
    DeliveryStopped,
    Exhausted,
    Failed,
    Finished,
    NoActiveTaskRun,
    Run,
    RunRef,
    Stopped,
    WorkspaceAlreadyConfigured,
    WorkspaceNotConfigured,
    deliver,
)
from shepherd_runtime.trace import Trace


def _run(outcome: object) -> Run[object]:
    return Run(
        outcome=outcome,  # type: ignore[arg-type]
        effects=(),
        artifacts=(),
        usage=None,
        duration=0.0,
        trace=None,
        ref=RunRef(id="run-local"),
    )


def test_run_field_names_are_pinned() -> None:
    assert [field.name for field in dataclasses.fields(Run)] == [
        "outcome",
        "effects",
        "artifacts",
        "usage",
        "duration",
        "trace",
        "ref",
        "proof",
    ]


def test_run_defaults_to_runtime_only_proof_envelope() -> None:
    run = _run(Finished("ok"))

    assert run.proof.profile is ProofProfile.RUNTIME_ONLY
    assert run.proof.strength is ProofStrength.RUNTIME_ONLY
    assert not run.proof.proof_backed


def test_public_nucleus_annotations_resolve_at_runtime() -> None:
    run_hints = get_type_hints(Run)
    deliver_hints = get_type_hints(deliver)

    assert run_hints["trace"] == Trace | None
    assert deliver_hints["evidence"] == Sequence[object]
    assert deliver_hints["constraints"] == Sequence[str]
    assert any(get_origin(arg) is Awaitable for arg in get_args(deliver_hints["return"]))


def test_nucleus_exceptions_inherit_shepherd_error_and_runtime_error() -> None:
    for exc_type in (
        DeliveryFailed,
        DeliveryExhausted,
        DeliveryStopped,
        WorkspaceNotConfigured,
        WorkspaceAlreadyConfigured,
        NoActiveTaskRun,
    ):
        assert issubclass(exc_type, ShepherdError)
        assert issubclass(exc_type, RuntimeError)


def test_finished_none_unwraps_to_none() -> None:
    assert _run(Finished(None)).unwrap() is None


def test_failed_unwrap_raises_with_run() -> None:
    run = _run(Failed(error_type="ProviderError", message="provider failed"))

    with pytest.raises(DeliveryFailed) as exc_info:
        run.unwrap()

    assert exc_info.value.run is run
    assert str(exc_info.value) == "provider failed"


def test_exhausted_and_stopped_unwrap_policy() -> None:
    exhausted = _run(Exhausted(reason="turn cap"))
    stopped = _run(Stopped(reason="cancelled"))

    with pytest.raises(DeliveryExhausted) as exhausted_error:
        exhausted.unwrap()
    with pytest.raises(DeliveryStopped) as stopped_error:
        stopped.unwrap()

    assert exhausted_error.value.run is exhausted
    assert stopped_error.value.run is stopped


def test_failed_outcome_is_json_serializable() -> None:
    payload = dataclasses.asdict(Failed(error_type="ProviderError", message="bad", retryable=False))
    assert json.loads(json.dumps(payload)) == {
        "error_type": "ProviderError",
        "message": "bad",
        "retryable": False,
    }


def test_delivery_limits_accept_none_and_positive_int() -> None:
    assert DeliveryLimits().max_turns is None
    assert DeliveryLimits(max_turns=3).max_turns == 3


@pytest.mark.parametrize("value", [True, False, 0, -1])
def test_delivery_limits_reject_invalid_max_turns(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        DeliveryLimits(max_turns=value)  # type: ignore[arg-type]
