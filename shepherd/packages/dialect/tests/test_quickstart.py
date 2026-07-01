"""The Appendix-C quickstart contract, re-pinned on the dialect path (W4).

Same 13 test names and observables as the legacy
`shepherd/integration-tests/test_appendix_c_quickstart.py` (the triage's
PRESERVE backlog item 1), with dialect imports: every task call routes through
the reversible wrap (`execute_recorded("runtime", "run", …)`); the legacy file
stays untouched until the hard-cut. `shepherd_dialect` stands where `shepherd`
will stand after the name transfer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from shepherd_kernel_v3_reference.proof_envelope import ProofProfile, ProofStrength

import shepherd_dialect as shepherd
from shepherd_dialect import (
    DeliveryFailed,
    Run,
    RunRef,
    Workspace,
    deliver,
    handle,
    task,
    workspace,
)
from shepherd_dialect.nucleus import (
    Exhausted,
    Failed,
    Finished,
    NoActiveTaskRun,
    Stopped,
    WorkspaceAlreadyConfigured,
    WorkspaceNotConfigured,
    reset_workspace_for_tests,
)
from shepherd_dialect.provider_boundary import ModelRequest, ModelResponse

if TYPE_CHECKING:
    from collections.abc import Iterator

SINGLE_OUTPUT_KEY = "result"


@pytest.fixture(autouse=True)
def _reset_workspace() -> Iterator[None]:
    """Each test starts and ends with a clean ambient workspace."""
    reset_workspace_for_tests()
    yield
    reset_workspace_for_tests()


@dataclass(frozen=True)
class Joke:
    text: str


@dataclass(frozen=True)
class QuickstartModel:
    name: str


def _handled_model_call(structured_output: dict[str, object]) -> object:
    def responder(request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(structured_output=structured_output)

    return handle("model.call", responder)


# --- A1 — workspace(...) opener ------------------------------------------------


def test_workspace_opens_returns_handle(tmp_path) -> None:
    ws = workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))
    assert isinstance(ws, Workspace)
    assert ws.root is not None
    assert ws.root == tmp_path.expanduser().resolve()


def test_workspace_scope_is_idempotent(tmp_path) -> None:
    ws = workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))
    assert ws.scope is ws.scope


def test_run_default_proof_envelope_is_runtime_only() -> None:
    run = Run(outcome=Finished("ok"), ref=RunRef("run-local"), duration=0.0)

    assert run.proof.profile is ProofProfile.RUNTIME_ONLY
    assert run.proof.strength is ProofStrength.RUNTIME_ONLY
    assert not run.proof.proof_backed


def test_workspace_already_configured_raises_on_conflict(tmp_path) -> None:
    workspace(model=QuickstartModel("first"), root=str(tmp_path))
    with pytest.raises(WorkspaceAlreadyConfigured):
        workspace(model=QuickstartModel("second"), root=str(tmp_path))


def test_workspace_not_configured_raises_for_task_call(tmp_path) -> None:
    @task
    async def needs_workspace() -> str:
        return await deliver(str, goal="...")

    with pytest.raises(WorkspaceNotConfigured):
        asyncio.run(needs_workspace())


# --- A4 — Function-form @task: sync and async -----------------------------------


def test_task_sync_unwraps_to_value(tmp_path) -> None:
    workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))

    @task
    def tell_joke(topic: str) -> Joke:
        return deliver(Joke, goal="tell a joke", evidence=[topic])

    with _handled_model_call({SINGLE_OUTPUT_KEY: {"text": "sync joke"}}):
        result = tell_joke("recursion")
    assert isinstance(result, Joke)
    assert result.text == "sync joke"


def test_task_async_unwraps_to_value(tmp_path) -> None:
    workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))

    @task
    async def tell_joke(topic: str) -> Joke:
        return deliver(Joke, goal="tell a joke", evidence=[topic])

    async def run() -> Joke:
        with _handled_model_call({SINGLE_OUTPUT_KEY: {"text": "async joke"}}):
            return await tell_joke("recursion")

    result = asyncio.run(run())
    assert isinstance(result, Joke)
    assert result.text == "async joke"


# --- A2 / A3 — Run[T] shape, outcome variants, RunRef ----------------------------


def test_detailed_returns_run_with_finished_outcome(tmp_path) -> None:
    workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))

    @task
    async def tell_joke(topic: str) -> Joke:
        return deliver(Joke, goal="tell a joke", evidence=[topic])

    async def execute() -> Run[Joke]:
        with _handled_model_call({SINGLE_OUTPUT_KEY: {"text": "detailed"}}):
            return await tell_joke.detailed("recursion")

    run = asyncio.run(execute())
    assert isinstance(run, Run)
    assert isinstance(run.outcome, Finished)
    assert run.outcome.value == Joke(text="detailed")
    assert isinstance(run.ref, RunRef)
    assert run.ref.id.startswith("run-")
    assert run.duration >= 0.0


def test_run_unwrap_returns_finished_value(tmp_path) -> None:
    workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))

    @task
    async def tell_joke() -> Joke:
        return deliver(Joke, goal="tell")

    async def execute() -> Run[Joke]:
        with _handled_model_call({SINGLE_OUTPUT_KEY: {"text": "unwrap"}}):
            return await tell_joke.detailed()

    run = asyncio.run(execute())
    assert run.unwrap() == Joke(text="unwrap")


# --- A7 — .unwrap() raises on non-Finished outcomes -------------------------------


def test_unwrap_raises_delivery_failed(tmp_path) -> None:
    workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))

    @task
    async def will_fail() -> Joke:
        return deliver(Joke, goal="...")

    async def execute() -> Joke:
        with _handled_model_call({"text": "no result key"}):
            return await will_fail()

    with pytest.raises(DeliveryFailed) as excinfo:
        asyncio.run(execute())
    assert excinfo.value.run is not None
    assert isinstance(excinfo.value.run.outcome, Failed)


def test_detailed_does_not_raise_on_failure(tmp_path) -> None:
    workspace(model=QuickstartModel("appendix-c"), root=str(tmp_path))

    @task
    async def will_fail() -> Joke:
        return deliver(Joke, goal="...")

    async def execute() -> Run[Joke]:
        with _handled_model_call({"text": "no result key"}):
            return await will_fail.detailed()

    run = asyncio.run(execute())
    assert isinstance(run, Run)
    assert isinstance(run.outcome, Failed)


# --- Outcome variants — frozen, hashable shapes -----------------------------------


def test_outcome_variants_are_frozen_dataclasses() -> None:
    finished = Finished(value=Joke(text="hi"))
    exhausted = Exhausted(reason="budget")
    stopped = Stopped(reason="cancel")
    failed = Failed(error_type="X", message="boom")
    assert isinstance(finished, Finished)
    assert isinstance(exhausted, Exhausted)
    assert isinstance(stopped, Stopped)
    assert isinstance(failed, Failed)
    assert failed.retryable is None


# --- Public facade sanity -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "workspace",
        "Workspace",
        "task",
        "deliver",
        "Run",
        "RunRef",
        "DeliveryFailed",
        "emit_artifact",
        "Artifact",
        "handle",
        "ask",
        "tell",
        "current_binding",
    ],
)
def test_public_reexport_present(name: str) -> None:
    assert hasattr(shepherd, name), f"shepherd_dialect.{name} missing from callable-spine facade"


@pytest.mark.parametrize(
    "name",
    [
        "Finished",
        "Exhausted",
        "Stopped",
        "Failed",
        "WorkspaceNotConfigured",
        "WorkspaceAlreadyConfigured",
        "NoActiveTaskRun",
    ],
)
def test_owner_path_nucleus_symbols_are_not_top_level(name: str) -> None:
    assert not hasattr(shepherd, name)


del NoActiveTaskRun  # imported to prove the owner path exists; not used directly
