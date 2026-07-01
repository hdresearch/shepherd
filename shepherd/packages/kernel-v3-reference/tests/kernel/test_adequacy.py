import pytest

from shepherd_kernel_v3_reference.kernel import elaborate, run_kernel
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.eval_direct import run
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed, Suspended
from shepherd_kernel_v3_reference.source.syntax import (
    Abort,
    Handle,
    Let,
    Lit,
    Perform,
    Resume,
    Return,
    Var,
)
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.validate import validate_core_trace, validate_core_trace_prefix


def install(effect_kind: str, body, handler_id: str = "h.v1") -> StaticHandlerInstall:
    return StaticHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        payload_name="_payload",
        body=body,
    )


@pytest.mark.parametrize(
    "term",
    [
        Let("x", Return(Lit(1)), Return(Var("x"))),
        Perform("eff.unhandled", Lit({"kind": "NeedInput"})),
        Handle(
            Perform("eff.a", Lit(None)),
            HandlerEnv((install("eff.a", Return(Lit("replaced"))),)),
        ),
        Handle(
            Let("x", Perform("eff.a", Lit(None)), Return(Var("x"))),
            HandlerEnv(
                (
                    install(
                        "eff.a",
                        Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        ),
        Handle(
            Let(
                "x",
                Perform("eff.a", Lit("first")),
                Let("y", Perform("eff.a", Lit("second")), Return(Var("y"))),
            ),
            HandlerEnv(
                (
                    install(
                        "eff.a",
                        Let("r", Resume(Lit("R")), Return(Var("r"))),
                    ),
                )
            ),
        ),
        Handle(
            Handle(
                Perform("eff.inner", Lit(None)),
                HandlerEnv(
                    (
                        install(
                            "eff.inner",
                            Let(
                                "approval",
                                Perform("eff.outer", Lit("ask")),
                                Let("r", Resume(Var("approval")), Return(Var("r"))),
                            ),
                            "h.inner",
                        ),
                    )
                ),
            ),
            HandlerEnv(
                (
                    install(
                        "eff.outer",
                        Let("r", Resume(Lit("approved")), Return(Var("r"))),
                        "h.outer",
                    ),
                )
            ),
        ),
        Handle(
            Perform("eff.a", Lit(None)),
            HandlerEnv((install("eff.a", Abort(Lit("blocked"))),)),
        ),
    ],
)
def test_static_fragment_direct_kernel_and_trace_agree(term) -> None:
    kernel = elaborate(term)
    direct = run(term)
    machine = run_kernel(kernel)
    traced = run_trace(kernel)

    _assert_outcomes_agree(direct, machine)
    _assert_outcomes_agree(direct, traced.outcome)
    if isinstance(direct, Completed):
        validate_core_trace(traced.trace)
    else:
        validate_core_trace_prefix(traced.trace)


def _assert_outcomes_agree(left, right) -> None:
    if isinstance(left, Completed):
        assert right == left
        return

    assert isinstance(left, Suspended)
    assert isinstance(right, Suspended)
    assert right.effect_kind == left.effect_kind
    assert right.payload == left.payload
