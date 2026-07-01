from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.kernel import PreparedKernelProgram, elaborate, prepare_kernel_program, run_kernel
from shepherd_kernel_v3_reference.kernel.program_identity import project_program_identity
from shepherd_kernel_v3_reference.kernel.step_machine import StepKernelEvaluator
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace.machine import TraceSession, run_trace


def test_run_kernel_accepts_prepared_program() -> None:
    prepared = prepare_kernel_program(elaborate(Return(Lit("done"))))

    assert run_kernel(prepared) == Completed("done")


def test_prepared_program_cannot_be_directly_constructed() -> None:
    prepared = prepare_kernel_program(elaborate(Return(Lit("done"))))

    with pytest.raises(TypeError, match="prepare_kernel_program"):
        PreparedKernelProgram(program=prepared.program, index=prepared.index)


def test_prepared_program_cannot_be_dataclass_replaced() -> None:
    old = prepare_kernel_program(elaborate(Return(Lit("old"))))
    new_program = elaborate(Return(Lit("new")))

    with pytest.raises(TypeError, match="dataclass"):
        replace(old, program=new_program)


def test_runtime_preserves_prepared_program_identity() -> None:
    prepared = prepare_kernel_program(elaborate(Return(Lit("done"))))

    evaluator = StepKernelEvaluator(prepared, evidence_mode="none")

    assert evaluator._prepared_program is prepared


def test_prepared_program_caches_projected_identity() -> None:
    prepared = prepare_kernel_program(_handled_program())

    first = project_program_identity(prepared)
    second = project_program_identity(prepared)

    assert second is first


def test_run_kernel_prepared_program_does_not_compute_identity() -> None:
    prepared = prepare_kernel_program(_handled_program())
    evaluator = StepKernelEvaluator(prepared, evidence_mode="none")

    assert evaluator.run() == Completed("value")
    assert evaluator._identity_stats.program_ref_computes == 0


def test_prepared_debug_trace_reuses_cached_identity_across_evaluators() -> None:
    prepared = prepare_kernel_program(_handled_program())

    first = TraceSession(prepared, include_debug_evidence=True)
    first_result = first.run()
    second = TraceSession(prepared, include_debug_evidence=True)
    second_result = second.run()

    assert first_result.require_debug_evidence().program_ref == second_result.require_debug_evidence().program_ref
    assert first._evaluator._identity_stats.program_ref_computes == 1
    assert second._evaluator._identity_stats.program_ref_computes == 0


def test_prepared_program_skips_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = prepare_kernel_program(elaborate(Return(Lit("done"))))

    def blocked_prepare(*args: object, **kwargs: object) -> object:
        raise AssertionError("prepared program was admitted again")

    monkeypatch.setattr(
        "shepherd_kernel_v3_reference.kernel.program_admission.prepare_kernel_program",
        blocked_prepare,
    )

    assert run_kernel(prepared) == Completed("done")


def test_run_trace_prepared_program_matches_raw_program() -> None:
    program = _handled_program()
    prepared = prepare_kernel_program(program)

    raw_result = run_trace(program)
    prepared_result = run_trace(prepared)

    assert prepared_result == raw_result


def test_trace_session_accepts_prepared_program() -> None:
    prepared = prepare_kernel_program(_handled_program())

    session = TraceSession(prepared)
    result = session.run()

    assert result.outcome == Completed("value")
    assert result.trace == session.trace


def test_recursive_trace_engine_accepts_prepared_program() -> None:
    prepared = prepare_kernel_program(_handled_program())

    result = run_trace(prepared, engine="recursive")

    assert result.outcome == Completed("value")


def _handled_program():
    return elaborate(
        Handle(
            Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="h.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )
