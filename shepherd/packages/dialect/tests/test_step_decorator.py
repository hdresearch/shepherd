"""W2b re-pins — function-form ``@step`` (authoring re-pin plan).

Adapted same-intent re-pins of `meta/tests/unit/step/test_step_decorator.py` +
the step rows of `test_step_integration.py`, onto the nucleus seams: the model
call rides ``handle("model.call", …)`` (no `shepherd_tests.MockProvider`), and
Step{Started,Completed,Failed} become durable ``step.*`` trace events (triage
D1 — records in the trace, not a parallel stream). Legacy class-form rows
(inline ``self.step[T]`` syntax, custom ``execute()`` integration) retire under
tranche D1.
"""

import dataclasses
from pathlib import Path
from typing import Literal

import pytest

from shepherd_dialect import (
    StepMetadata,
    StepOutputError,
    handle,
    step,
    task,
    workspace,
)
from shepherd_dialect.nucleus import Failed, Finished, reset_workspace_for_tests
from shepherd_dialect.provider_boundary import ModelResponse


@pytest.fixture(autouse=True)
def _reset_workspace():
    reset_workspace_for_tests()
    yield
    reset_workspace_for_tests()


@dataclasses.dataclass(frozen=True)
class StepModel:
    name: str


@pytest.fixture
def ws(tmp_path: Path):
    return workspace(model=StepModel("steps"), root=str(tmp_path))


def _respond(value):
    return handle("model.call", lambda req: ModelResponse(structured_output={"result": value}))


@step
def classify(text: str) -> Literal["good", "bad"]:
    """Classify the text's sentiment."""
    return "good"  # body unused for shepherd steps — the docstring is the prompt


@step(shepherd=False)
def local_double(n: int) -> int:
    """Double a number locally."""
    return n * 2


class TestStepDecorator:
    def test_step_exposes_metadata(self):
        meta = classify.step_metadata
        assert isinstance(meta, StepMetadata)
        assert meta.name == "classify"
        assert meta.docstring.strip() == "Classify the text's sentiment."
        assert meta.parameters == {"text": str}
        assert meta.step_id == "step:classify"

    def test_step_without_docstring_warns(self):
        with pytest.warns(UserWarning, match="no docstring"):

            @step
            def bare(x: int) -> int:
                return x

    def test_shepherd_step_returns_typed_value(self):
        with _respond("bad"):
            assert classify("meh") == "bad"

    def test_literal_coercion_rejects_unknown_value(self):
        with _respond("ugly"), pytest.raises(StepOutputError):
            classify("meh")

    def test_float_return_type_coerced(self):
        @step
        def score(text: str) -> float:
            """Score the text."""

        with _respond("0.75"):
            assert score("x") == 0.75

    def test_list_return_type_coerced(self):
        @step
        def tags(text: str) -> list[str]:
            """Tag the text."""

        with _respond(["a", "b"]):
            assert tags("x") == ["a", "b"]

    def test_non_shepherd_step_runs_body(self):
        assert local_double(4) == 8

    def test_step_outside_run_is_unit_testable(self):
        with _respond("good"):
            assert classify("standalone") == "good"


class TestStepEventsInDurableTrace:
    def test_step_lifecycle_events_recorded(self, ws):
        @task
        def review(text: str) -> str:
            with _respond("good"):
                return classify(text)

        run = review.detailed("nice")
        assert isinstance(run.outcome, Finished)
        started = run.trace.filter("step.started")
        completed = run.trace.filter("step.completed")
        assert [e["step"] for e in started] == ["classify"]
        assert [e["step"] for e in completed] == ["classify"]

    def test_step_failure_event_recorded_and_run_fails(self, ws):
        @task
        def review(text: str) -> str:
            with _respond(12345):  # not a Literal member -> StepOutputError
                return classify(text)

        run = review.detailed("nice")
        assert isinstance(run.outcome, Failed)
        assert run.outcome.error_type == "StepOutputError"
        failed = run.trace.filter("step.failed")
        assert failed
        assert failed[0]["step"] == "classify"
        assert run.trace.filter("run.lifecycle")[0]["terminal_status"] == "discarded"

    def test_chained_steps_record_in_order(self, ws):
        @task
        def pipeline(n: int) -> int:
            first = local_double(n)
            return local_double(first)

        run = pipeline.detailed(3)
        assert run.unwrap() == 12
        assert [e["step"] for e in run.trace.filter("step.completed")] == ["local_double", "local_double"]
