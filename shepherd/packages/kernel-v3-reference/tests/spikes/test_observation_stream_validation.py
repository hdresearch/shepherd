"""CI-tracked regression for the observation-stream-validation spike
(promoted from 2026-05-24 capability spike per
`260524-observation-stream-spike.md`)."""

from __future__ import annotations

import pytest

from shepherd_kernel_v3_reference.spikes.observation_stream_validation import (
    CASES,
    run_cases,
)


def test_observation_stream_all_cases_behave_as_expected() -> None:
    """All 7 cases behave as expected: valid streams complete or suspend
    cleanly, invalid streams fail-fast with per-failure metadata. Mirrors
    the 2026-05-24 spike's 7/7 result that pinned the driver shape."""

    results = run_cases()
    assert {r.name for r in results} == set(CASES)
    failures = [r for r in results if not r.ok]
    if failures:
        msg = "\n".join(
            f"  - {r.name}: expect_pass={r.expect_pass} but "
            f"last_outcome={r.last_outcome!r}, rejection_index={r.rejection_index!r}, "
            f"rejection_class={r.rejection_class!r}, msg={r.rejection_message!r}"
            for r in failures
        )
        pytest.fail(f"observation-stream cases regressed:\n{msg}")


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_observation_stream_per_case(case_name: str) -> None:
    """Per-case parametrized assertion — fast feedback when a single case
    regresses."""

    expect_pass, expected_outcome, _builder = CASES[case_name]
    results = {r.name: r for r in run_cases()}
    result = results[case_name]
    assert result.last_outcome == expected_outcome, (
        f"{case_name}: expected outcome {expected_outcome!r}, got {result.last_outcome!r}"
    )
    if expect_pass:
        assert result.rejection_index is None, (
            f"{case_name}: expected pass but got rejection at index {result.rejection_index} "
            f"({result.rejection_class}: {result.rejection_message})"
        )
    else:
        assert result.rejection_index is not None, (
            f"{case_name}: expected rejection but got pass"
        )


def test_observation_stream_invalid_cases_carry_rejection_metadata() -> None:
    """Every invalid case must surface rejection_index, rejection_class, and
    a non-empty diagnostic so #76b's wrapper can populate KernelRejection."""

    for r in run_cases():
        if not r.expect_pass:
            assert r.rejection_index is not None, f"{r.name}: missing rejection_index"
            assert r.rejection_class, f"{r.name}: missing rejection_class"
            assert r.rejection_message, f"{r.name}: missing rejection_message"
