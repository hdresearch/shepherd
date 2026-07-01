"""W3b/W3c re-pins — task metadata, Input/Output serde, prompt, source, mixin.

Function-form re-pins (tranche D1) of: `runtime/unit/task/test_task_marker.py`
(metadata/introspection/prompt/markers — class-form meta-task execution rides
the transform re-home), `test_spike2_serialization_roundtrip.py` (the JSON-
boundary type matrix), `test_mixin_unification.py` (sync/async — the W3c
remainder; the fork-collapse halves are already pinned by quickstart + W1
arun tests; cacheable defaults ride the cache tranche), and the typed
fourth-row args key ratified at S1 (seam 4): same values ⇒ same cross-run
`task.invocation` digest regardless of call spelling.
"""

import dataclasses
import datetime as dt
import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Optional

import pytest
from pydantic import BaseModel

from shepherd_dialect import (
    CompletedTask,
    NonEmpty,
    TaskRef,
    dump_task_args,
    extract_task_metadata,
    extract_task_source,
    load_task_args,
    task,
    task_input_model,
    task_prompt,
    workspace,
)
from shepherd_dialect.nucleus import Finished, reset_workspace_for_tests


@pytest.fixture(autouse=True)
def _reset_workspace():
    reset_workspace_for_tests()
    yield
    reset_workspace_for_tests()


@dataclasses.dataclass(frozen=True)
class MetaModel:
    name: str


@pytest.fixture
def ws(tmp_path: Path):
    return workspace(model=MetaModel("meta"), root=str(tmp_path))


class Color(Enum):
    RED = "red"
    BLUE = "blue"


class Inner(BaseModel):
    label: str
    weight: float


def summarize(text: Annotated[str, NonEmpty()], limit: int = 100) -> str:
    """Summarize the text within the limit."""
    return text[:limit]


class TestTaskMetadata:
    def test_name_and_docstring(self):
        meta = extract_task_metadata(summarize)
        assert meta.name == "summarize"
        assert meta.docstring == "Summarize the text within the limit."

    def test_inputs_with_required_and_default(self):
        meta = extract_task_metadata(summarize)
        assert meta.inputs["text"].required is True
        assert meta.inputs["limit"].required is False
        assert meta.inputs["limit"].default == 100

    def test_annotated_metadata_stripped_to_inner_type(self):
        meta = extract_task_metadata(summarize)
        assert meta.inputs["text"].inner_type is str

    def test_checks_threaded_into_field_info(self):
        meta = extract_task_metadata(summarize)
        assert len(meta.inputs["text"].checks) == 1
        assert meta.input_checks["text"] == meta.inputs["text"].checks

    def test_return_type_extracted(self):
        assert extract_task_metadata(summarize).return_type is str

    def test_accepts_a_task_callable(self):
        wrapped = task(summarize)
        assert extract_task_metadata(wrapped).name == "summarize"


class TestTaskRefMarkers:
    def test_task_ref_is_pydantic_compatible(self):
        class Spec(BaseModel):
            target: TaskRef

        assert Spec(target=object()).target is not None

    def test_completed_task_is_pydantic_compatible(self):
        class Spec(BaseModel):
            done: CompletedTask

        assert Spec(done={"anything": 1}).done == {"anything": 1}


# --- the spike2 JSON-boundary matrix ---------------------------------------------

MATRIX = [
    ("text", str, "hello"),
    ("count", int, 7),
    ("ratio", float, 0.5),
    ("flag", bool, True),
    ("items", list[str], ["a", "b"]),
    ("table", dict[str, int], {"a": 1}),
    ("unique", set[str], {"x", "y"}),
    ("pair", tuple[int, str], (1, "a")),
    ("when", dt.datetime, dt.datetime(2026, 6, 10, 12, 0)),
    ("day", dt.date, dt.date(2026, 6, 10)),
    ("at", dt.time, dt.time(12, 30)),
    ("blob", bytes, b"raw"),
    ("where", Path, Path("/tmp/x")),
    ("color", Color, Color.RED),
    ("mode", Literal["fast", "slow"], "fast"),
    ("inner", Inner, Inner(label="l", weight=1.5)),
    ("maybe", Optional[int], None),  # noqa: UP045 — the legacy matrix spells Optional
]


class TestSerializationRoundtrip:
    @pytest.mark.parametrize(("name", "typ", "value"), MATRIX, ids=[m[0] for m in MATRIX])
    def test_type_survives_the_json_boundary(self, name, typ, value):
        def fn(x):  # annotation injected below — parametrized signature
            return x

        fn.__annotations__ = {"x": typ, "return": typ}
        dumped = dump_task_args(fn, (value,), {})
        wire = json.loads(json.dumps(dumped))  # the real JSON boundary
        restored = load_task_args(fn, wire)
        assert restored["x"] == value

    def test_dump_is_call_spelling_independent(self):
        assert dump_task_args(summarize, ("hi",), {"limit": 5}) == dump_task_args(
            summarize, (), {"limit": 5, "text": "hi"}
        )

    def test_defaults_are_applied_in_the_dump(self):
        assert dump_task_args(summarize, ("hi",), {})["limit"] == 100

    def test_input_model_name(self):
        assert task_input_model(summarize).__name__ == "SummarizeInput"


class TestFourthRowTypedKey:
    """S1 seam 4, ratified: the cross-run key is the typed dump, not call-repr."""

    def test_same_values_same_invocation_digest(self, ws):
        @task
        def echo(text: str, limit: int = 3) -> str:
            return text[:limit]

        run_a = echo.detailed("abc", limit=2)
        run_b = echo.detailed(limit=2, text="abc")  # different spelling, same fact
        dig = lambda r: r.trace.summary()["invocation_digest"]  # noqa: E731
        assert isinstance(run_a.outcome, Finished)
        assert dig(run_a) == dig(run_b)

    def test_different_values_different_digest(self, ws):
        @task
        def echo2(text: str) -> str:
            return text

        assert (
            echo2.detailed("one").trace.summary()["invocation_digest"]
            != echo2.detailed("two").trace.summary()["invocation_digest"]
        )


class TestPromptAndSource:
    def test_prompt_contains_docstring_inputs_and_schema(self):
        prompt = task_prompt(summarize, {"text": "hello", "limit": 5})
        assert "Summarize the text within the limit." in prompt
        assert "'hello'" in prompt
        assert '"result"' in prompt  # the single-output schema key

    def test_extract_task_source_returns_the_def(self):
        src = extract_task_source(summarize)
        assert src.startswith("def summarize(")
        assert "text[:limit]" in src

    def test_extract_task_source_unwraps_task_callable(self):
        assert "def summarize(" in extract_task_source(task(summarize))


class TestMixinUnification:
    """W3c remainder: one sync core; async is the thin wrapper (D2)."""

    def test_sync_and_async_bodies_share_the_outcome_contract(self, ws):
        import asyncio

        @task
        def sync_form(x: int) -> int:
            return x + 1

        @task
        async def async_form(x: int) -> int:
            return x + 1

        sync_run = sync_form.detailed(1)
        async_run = asyncio.run(async_form.detailed(1))
        assert sync_run.unwrap() == async_run.unwrap() == 2
        assert type(sync_run.outcome) is type(async_run.outcome)

    def test_both_forms_share_the_fourth_row_shape(self, ws):
        import asyncio

        @task
        async def aform(x: int) -> int:
            return x

        run = asyncio.run(aform.detailed(5))
        assert run.trace.summary()["invocation_digest"].startswith("sha256:")
