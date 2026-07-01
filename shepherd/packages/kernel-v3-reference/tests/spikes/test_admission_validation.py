"""CI-tracked regression for the admission-validation spike (promoted from
2026-05-24 capability spike per `260524-post-72-design-pass.md`
§"Item F: Admission Validation Spike")."""

from __future__ import annotations

import pytest

from shepherd_kernel_v3_reference.spikes.admission_validation import (
    CASES,
    run_cases,
)


def test_admission_validation_all_cases_behave_as_expected() -> None:
    """All 9 invariant cases behave as expected: valid cases admit, invalid
    cases reject with stable diagnostics. Mirrors the 2026-05-24 spike's
    9/9 result that pinned the validator's check order and bundle shape."""

    results = run_cases()
    assert {r.name for r in results} == set(CASES)
    failures = [r for r in results if not r.ok]
    if failures:
        msg = "\n".join(
            f"  - {r.name}: expect_pass={r.expect_pass} but "
            f"actually_passed={r.actually_passed} "
            f"({r.error_class}: {r.error_message})"
            for r in failures
        )
        pytest.fail(f"admission validation cases regressed:\n{msg}")


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_admission_validation_per_case(case_name: str) -> None:
    """Per-case parametrized assertion — fast feedback when a single case
    regresses."""

    expect_pass, _builder = CASES[case_name]
    results = {r.name: r for r in run_cases()}
    result = results[case_name]
    assert result.expect_pass == expect_pass, "test wiring drift"
    assert result.actually_passed == expect_pass, (
        f"{case_name}: expect_pass={expect_pass}, actually_passed={result.actually_passed} "
        f"({result.error_class}: {result.error_message})"
    )


def test_admission_validation_invalid_cases_carry_diagnostic_messages() -> None:
    """Every invalid case must surface a non-empty diagnostic message so #73's
    production validator can preserve diagnostic precision."""

    for r in run_cases():
        if not r.expect_pass:
            assert r.error_class == "AdmittedObservationError", (
                f"{r.name}: expected AdmittedObservationError, got {r.error_class}"
            )
            assert r.error_message, f"{r.name}: empty diagnostic"
