"""W1 re-pins — Check markers (authoring re-pin plan, S1-ratified).

Same-name re-pins of the legacy contract: `runtime/tests/unit/task/
test_check_builtins.py` (builtins + message templates + Annotated),
`test_check_extraction.py` (function-form per tranche D1 — extraction reads the
task function's signature, not an Input model), and `test_check_execution.py`
(nucleus path: preconditions refuse BEFORE the fork — S1 seam 1 — with a
`refused` durable trace; postconditions discard via the wrap). Legacy
class-form rows (`test_postcondition_checks_run_after_custom_execute`,
`test_custom_execute_passes_with_valid_output`) retire under tranche D1 —
function-form only.
"""

# NOTE: no `from __future__ import annotations` — extraction tests bind Check
# instances as closure locals, which PEP 563 stringification would make
# unresolvable (the documented v1 limitation in checks.extract_checks).
import asyncio
import dataclasses
from pathlib import Path
from typing import Annotated

import pytest

from shepherd_dialect import (
    Check,
    FileExists,
    InRange,
    Matches,
    MaxLength,
    NonEmpty,
    extract_checks,
    handle,
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
class ChecksModel:
    name: str


@pytest.fixture
def ws(tmp_path: Path):
    return workspace(model=ChecksModel("checks"), root=str(tmp_path))


# --- test_check_builtins re-pins -------------------------------------------------


class TestCheckCore:
    def test_call_returns_predicate_result(self):
        assert Check(predicate=lambda v: v > 0)(1) is True
        assert Check(predicate=lambda v: v > 0)(-1) is False

    def test_format_message_with_interpolation(self):
        chk = Check(predicate=bool, message="bad {field}: {value}")
        assert chk.format_message(3, "count") == "bad count: 3"

    def test_format_message_repr_format(self):
        chk = Check(predicate=bool, message="got {value!r}")
        assert chk.format_message("x") == "got 'x'"

    def test_format_message_default_when_no_message(self):
        assert Check(predicate=bool).format_message("v", "f") == "Check failed for f: 'v'"

    def test_format_message_default_no_field_name(self):
        assert Check(predicate=bool).format_message("v") == "Check failed for field: 'v'"

    def test_format_message_fallback_on_bad_template(self):
        chk = Check(predicate=bool, message="bad {nope}")
        assert chk.format_message("v") == "bad {nope}"

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            Check(predicate=bool).message = "x"  # type: ignore[misc]

    def test_isinstance(self):
        assert isinstance(NonEmpty(), Check)


class TestFileExists:
    def test_existing_file_passes(self, tmp_path: Path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert FileExists()(str(f))

    def test_missing_file_fails(self, tmp_path: Path):
        assert not FileExists()(str(tmp_path / "missing"))

    def test_directory_passes(self, tmp_path: Path):
        assert FileExists()(str(tmp_path))

    def test_returns_check_instance(self):
        assert isinstance(FileExists(), Check)

    def test_default_message_interpolation(self, tmp_path: Path):
        target = str(tmp_path / "gone")
        assert FileExists().format_message(target) == f"File does not exist: {target}"

    def test_custom_message(self):
        assert FileExists("nope")(__file__) is True
        assert FileExists("nope").message == "nope"


class TestNonEmpty:
    def test_non_empty_string_passes(self):
        assert NonEmpty()("hi")

    def test_empty_string_fails(self):
        assert not NonEmpty()("")

    def test_whitespace_only_fails(self):
        assert not NonEmpty()("   ")

    def test_none_fails(self):
        assert not NonEmpty()(None)

    def test_non_empty_list_passes(self):
        assert NonEmpty()([1])

    def test_empty_list_fails(self):
        assert not NonEmpty()([])

    def test_empty_dict_fails(self):
        assert not NonEmpty()({})

    def test_non_empty_dict_passes(self):
        assert NonEmpty()({"a": 1})

    def test_zero_is_non_empty(self):
        assert NonEmpty()(0)

    def test_false_is_non_empty(self):
        assert NonEmpty()(False)

    def test_returns_check_instance(self):
        assert isinstance(NonEmpty(), Check)

    def test_custom_message(self):
        assert NonEmpty("need it").message == "need it"


class TestInRange:
    def test_within_range_passes(self):
        assert InRange(0, 10)(5)

    def test_at_min_boundary_passes(self):
        assert InRange(0, 10)(0)

    def test_at_max_boundary_passes(self):
        assert InRange(0, 10)(10)

    def test_below_min_fails(self):
        assert not InRange(0, 10)(-1)

    def test_above_max_fails(self):
        assert not InRange(0, 10)(11)

    def test_min_only(self):
        assert InRange(min_val=3)(4)
        assert not InRange(min_val=3)(2)

    def test_max_only(self):
        assert InRange(max_val=3)(2)
        assert not InRange(max_val=3)(4)

    def test_integer_range(self):
        assert InRange(1, 3)(2)

    def test_returns_check_instance(self):
        assert isinstance(InRange(0, 1), Check)

    def test_message_both_bounds(self):
        assert InRange(0, 9).format_message(11) == "Value 11 not in range [0, 9]"

    def test_message_min_only(self):
        assert InRange(min_val=2).format_message(1) == "Value 1 must be >= 2"

    def test_message_max_only(self):
        assert InRange(max_val=2).format_message(3) == "Value 3 must be <= 2"

    def test_custom_message(self):
        assert InRange(0, 1, message="m").message == "m"


class TestMatches:
    def test_matching_pattern_passes(self):
        assert Matches(r"^\d+$")("123")

    def test_non_matching_fails(self):
        assert not Matches(r"^\d+$")("abc")

    def test_partial_match(self):
        assert Matches(r"\d+")("abc123")

    def test_full_string_match_with_anchors(self):
        assert not Matches(r"^\d+$")("abc123")

    def test_pattern_with_braces(self):
        chk = Matches(r"^\d{3}$")
        assert chk("123")
        assert "does not match pattern" in chk.format_message("x")

    def test_returns_check_instance(self):
        assert isinstance(Matches(r"."), Check)

    def test_custom_message(self):
        assert Matches(r".", message="m").message == "m"


class TestMaxLength:
    def test_within_limit_passes(self):
        assert MaxLength(5)("abc")

    def test_at_limit_passes(self):
        assert MaxLength(3)("abc")

    def test_over_limit_fails(self):
        assert not MaxLength(2)("abc")

    def test_empty_passes(self):
        assert MaxLength(2)("")

    def test_list_length(self):
        assert MaxLength(2)([1, 2])
        assert not MaxLength(2)([1, 2, 3])

    def test_returns_check_instance(self):
        assert isinstance(MaxLength(1), Check)

    def test_default_message(self):
        assert MaxLength(2).format_message("abc") == "Length exceeds maximum of 2"

    def test_custom_message(self):
        assert MaxLength(1, message="m").message == "m"


class TestAnnotated:
    def test_check_in_annotated_metadata(self):
        def fn(x: Annotated[str, NonEmpty()]) -> str:
            return x

        input_checks, _ = extract_checks(fn)
        assert len(input_checks["x"]) == 1

    def test_multiple_checks_on_one_field(self):
        def fn(x: Annotated[str, NonEmpty(), MaxLength(5)]) -> str:
            return x

        input_checks, _ = extract_checks(fn)
        assert len(input_checks["x"]) == 2


# --- test_check_extraction re-pins (function-form per tranche D1) ----------------


class TestCheckExtraction:
    def test_returns_empty_for_no_metadata(self):
        def fn(x: str) -> str:
            return x

        assert extract_checks(fn) == ({}, ())

    def test_returns_empty_when_no_checks(self):
        def fn(x: Annotated[str, "doc"]) -> str:
            return x

        assert extract_checks(fn) == ({}, ())

    def test_extracts_single_check(self):
        chk = NonEmpty()

        def fn(x: Annotated[str, chk]) -> str:
            return x

        input_checks, _ = extract_checks(fn)
        assert input_checks["x"][0] is chk  # identity preserved

    def test_extracts_multiple_checks(self):
        a, b = NonEmpty(), MaxLength(3)

        def fn(x: Annotated[str, a, b]) -> str:
            return x

        assert extract_checks(fn)[0]["x"] == (a, b)

    def test_input_checks_extracted(self):
        def fn(x: Annotated[str, NonEmpty()], y: Annotated[int, InRange(0, 5)]) -> str:
            return x

        input_checks, _ = extract_checks(fn)
        assert set(input_checks) == {"x", "y"}

    def test_output_checks_extracted(self):
        chk = NonEmpty()

        def fn(x: str) -> Annotated[str, chk]:
            return x

        _, output_checks = extract_checks(fn)
        assert output_checks == (chk,)

    def test_no_checks_means_no_entry(self):
        def fn(x: Annotated[str, NonEmpty()], y: str) -> str:
            return x

        assert "y" not in extract_checks(fn)[0]

    def test_multiple_checks_per_field(self):
        def fn(x: Annotated[str, NonEmpty(), MaxLength(2), Matches(r"^a")]) -> str:
            return x

        assert len(extract_checks(fn)[0]["x"]) == 3

    def test_checks_dont_interfere_with_primary_markers(self):
        def fn(x: Annotated[str, "other-metadata", NonEmpty()]) -> str:
            return x

        assert len(extract_checks(fn)[0]["x"]) == 1

    def test_mixed_fields_with_and_without_checks(self):
        def fn(a: Annotated[str, NonEmpty()], b: str, c: Annotated[int, InRange(0)]) -> str:
            return a

        assert set(extract_checks(fn)[0]) == {"a", "c"}


# --- test_check_execution re-pins (nucleus path) ---------------------------------


@task
def guarded(text: Annotated[str, NonEmpty()]) -> str:
    return text.upper()


@task
def post_guarded(text: str) -> Annotated[str, NonEmpty()]:
    return text  # empty in -> empty out -> postcondition fails


@task
def multi_guarded(text: Annotated[str, NonEmpty(), MaxLength(3)]) -> str:
    return text


class TestCheckExecution:
    def test_precondition_failure_raises_check_failed_error(self, ws):
        run = guarded.detailed("")
        assert isinstance(run.outcome, Failed)
        assert run.outcome.error_type == "CheckFailed"
        assert "text" in run.outcome.message
        assert "precondition" in run.outcome.message

    def test_precondition_failure_does_not_call_provider(self, ws):
        calls: list[object] = []
        with handle("model.call", lambda req: calls.append(req) or ModelResponse(structured_output={"result": {}})):
            guarded.detailed("")
        assert calls == []

    def test_precondition_failure_discards_fork(self, ws):
        """Re-pinned (S1 seams 1+3): the refusal happens BEFORE the fork — no
        workspace transition, durable trace terminal `refused`. (The trace
        append itself IS a world transition by design — durable evidence,
        invariant 3 — so world-OID equality is not the observable here.)"""
        run = guarded.detailed("")
        transition = run.trace.filter("substrate.transition")[0]
        assert transition["head_to"] is None, "no output workspace world — the fork never happened"
        lifecycle = run.trace.filter("run.lifecycle")
        assert lifecycle[0]["terminal_status"] == "refused"
        violations = run.trace.filter("check.violation")
        assert violations
        assert violations[0]["phase"] == "precondition"

    def test_precondition_passes_with_valid_input(self, ws):
        assert guarded("hello") == "HELLO"

    def test_postcondition_failure_raises_check_failed_error(self, ws):
        run = post_guarded.detailed("")
        assert isinstance(run.outcome, Failed)
        assert run.outcome.error_type == "CheckFailed"
        assert "postcondition" in run.outcome.message

    def test_postcondition_failure_discards_fork(self, ws):
        run = post_guarded.detailed("")
        lifecycle = run.trace.filter("run.lifecycle")
        assert lifecycle[0]["terminal_status"] == "discarded"
        violations = run.trace.filter("check.violation")
        assert violations
        assert violations[0]["phase"] == "postcondition"

    def test_postcondition_passes_with_valid_output(self, ws):
        assert post_guarded("ok") == "ok"

    def test_first_failing_check_raises(self, ws):
        run = multi_guarded.detailed("toolong")  # NonEmpty passes; MaxLength(3) fails first failure
        assert isinstance(run.outcome, Failed)
        assert "maximum" in run.outcome.message

    def test_all_checks_pass(self, ws):
        assert multi_guarded("ab") == "ab"

    def test_task_without_checks_still_works(self, ws):
        @task
        def plain(x: str) -> str:
            return x + "!"

        assert plain("y") == "y!"

    def test_effects_merged_on_success(self, ws):
        run = guarded.detailed("ok")
        assert isinstance(run.outcome, Finished)
        assert run.trace.filter("run.lifecycle")[0]["terminal_status"] == "merged"

    def test_precondition_failure_in_arun(self, ws):
        """D2 thin wrapper: async task, sync core — same refusal observable."""

        @task
        async def aguarded(text: Annotated[str, NonEmpty()]) -> str:
            return text

        run = asyncio.run(aguarded.detailed(""))
        assert isinstance(run.outcome, Failed)
        assert run.outcome.error_type == "CheckFailed"

    def test_postcondition_failure_in_arun(self, ws):
        @task
        async def apost(text: str) -> Annotated[str, NonEmpty()]:
            return text

        run = asyncio.run(apost.detailed(""))
        assert isinstance(run.outcome, Failed)

    def test_arun_success_merges_effects(self, ws):
        @task
        async def aok(text: Annotated[str, NonEmpty()]) -> str:
            return text

        run = asyncio.run(aok.detailed("fine"))
        assert isinstance(run.outcome, Finished)
        assert run.trace.filter("run.lifecycle")[0]["terminal_status"] == "merged"
