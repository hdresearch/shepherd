from __future__ import annotations

from typing import cast

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.recursive_machine import RecursiveKernelEvaluator
from shepherd_kernel_v3_reference.kernel.step_machine import StepKernelEvaluator
from shepherd_kernel_v3_reference.source.outcomes import Completed, SourceOutcome
from shepherd_kernel_v3_reference.source.syntax import Lit, Return
from shepherd_kernel_v3_reference.trace.machine import TraceEvaluatorEngine, run_trace
from shepherd_kernel_v3_reference.trace.validate import validate_generated_trace_against_program


def test_run_trace_auto_routes_to_step_evaluator(monkeypatch: pytest.MonkeyPatch) -> None:
    program = elaborate(Return(Lit("done")))

    def blocked_recursive_run(_self: RecursiveKernelEvaluator, _env: object | None = None) -> SourceOutcome:
        raise AssertionError("auto trace routing used the recursive evaluator")

    monkeypatch.setattr(RecursiveKernelEvaluator, "run", blocked_recursive_run)

    result = run_trace(program)

    assert result.outcome == Completed("done")


def test_run_trace_recursive_engine_keeps_recursive_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    program = elaborate(Return(Lit("done")))

    def blocked_step_run(_self: StepKernelEvaluator, _env: object | None = None) -> SourceOutcome:
        raise AssertionError("recursive trace routing used the step evaluator")

    monkeypatch.setattr(StepKernelEvaluator, "run", blocked_step_run)

    result = run_trace(program, engine="recursive")

    assert result.outcome == Completed("done")


def test_validate_generated_trace_accepts_explicit_recursive_engine() -> None:
    program = elaborate(Return(Lit("done")))
    result = run_trace(program, engine="recursive")

    validate_generated_trace_against_program(program, result.trace, engine="recursive")


def test_run_trace_rejects_unknown_engine() -> None:
    program = elaborate(Return(Lit("done")))

    with pytest.raises(ValueError, match="unknown trace evaluator engine"):
        run_trace(program, engine=cast("TraceEvaluatorEngine", "bogus"))
