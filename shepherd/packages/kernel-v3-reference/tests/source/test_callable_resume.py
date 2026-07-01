"""Callable resume + deep handling (§02, §03)."""

import pytest

from shepherd_kernel_v3_reference.schemas import AnySchema, TaggedRecordSchema, TypeSchema
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.eval_direct import ResumptionUsed, run
from shepherd_kernel_v3_reference.source.handlers import DynamicHandlerInstall, HandlerEnv
from shepherd_kernel_v3_reference.source.outcomes import Completed
from shepherd_kernel_v3_reference.source.syntax import (
    Handle,
    Let,
    Lit,
    Perform,
    Resume,
    Return,
    Var,
)


def install(
    effect_kind: str,
    body,
    handler_id: str = "h.v1",
) -> DynamicHandlerInstall:
    return DynamicHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        body=body,
    )


# --- callable resume basics -------------------------------------------------


def test_handler_resumes_with_value_and_returns_worker_result() -> None:
    # Worker performs eff.a; handler resumes with "v"; worker returns "v"
    # to handler, handler returns it as the Handle's answer.
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let("r", Resume(Lit("v")), Return(Var("r"))),
                ),
            )
        ),
    )
    assert run(program) == Completed("v")


def test_resume_returns_worker_continuation_R_value_to_handler() -> None:
    # Worker after perform: r = ...; the worker R is the binding chain.
    # Specifically: y = perform; z = "z" string; return (y, z) shape.
    # The handler observes the worker's R and rewraps it.
    program = Handle(
        Let(
            "y",
            Perform("eff.a", Lit(None)),
            Let(
                "z",
                Return(Lit("z-from-worker")),
                Return(Var("y")),
            ),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "r",
                        Resume(Lit("y-from-handler")),
                        Return(Var("r")),
                    ),
                ),
            )
        ),
    )
    # Worker resumes with y="y-from-handler", then unused z, then Return y
    # → worker R = "y-from-handler". Handler returns r=that → Handle value.
    assert run(program) == Completed("y-from-handler")


def test_handler_can_transform_worker_result_before_answering() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "r",
                        Resume(Lit("base")),
                        # Handler observes worker's "base" R, returns
                        # something else as its answer.
                        Return(Lit("transformed")),
                    ),
                ),
            )
        ),
    )
    assert run(program) == Completed("transformed")


def test_callable_resume_is_one_shot_in_the_core() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "x",
                        Resume(Lit("first")),
                        Let("y", Resume(Lit("second")), Return(Var("y"))),
                    ),
                ),
            )
        ),
    )

    with pytest.raises(ResumptionUsed, match="already used"):
        run(program)


# --- deep handling ----------------------------------------------------------


def test_deep_handling_dispatches_a_second_perform_to_the_same_handler_env() -> None:
    # Two performs of eff.a back-to-back; both dispatched to the same handler
    # env. With deep handling each perform creates a fresh selection.
    program = Handle(
        Let(
            "x",
            Perform("eff.a", Lit("first")),
            Let(
                "y",
                Perform("eff.a", Lit("second")),
                Return(Var("y")),
            ),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda payload: Let(
                        "r",
                        Resume(Lit(f"R_for_{payload}")),
                        Return(Var("r")),
                    ),
                ),
            )
        ),
    )
    # Worker:    x = R_for_first  (1st handler resumes)
    #            y = R_for_second (2nd handler resumes; deep dispatch)
    #            return y         → R = "R_for_second"
    # Each handler returns R as its answer; per deep handler equation
    # `resume(v) = Handle(e[v], h)`, the handler's answer becomes the
    # value of the surrounding fresh Handle. The chain unwinds to:
    assert run(program) == Completed("R_for_second")


# --- handler-side effects propagate outward ---------------------------------


def test_handler_side_perform_propagates_to_outer_handler() -> None:
    # Inner handler (eff.inner) performs eff.outer in its body, which is
    # caught by the outer handler. Outer handler resumes.
    program = Handle(  # outer
        Handle(  # inner
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        body=lambda p: Let(
                            "approval",
                            Perform("eff.outer", Lit("ask-outer")),
                            Let(
                                "r",
                                Resume(Var("approval")),
                                Return(Var("r")),
                            ),
                        ),
                        handler_id="h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv(
            (
                install(
                    "eff.outer",
                    body=lambda p: Let(
                        "r",
                        Resume(Lit("approved-value")),
                        Return(Var("r")),
                    ),
                    handler_id="h.outer",
                ),
            )
        ),
    )
    # Inner handler asks outer for approval, gets "approved-value", resumes
    # worker with that, gets it back as worker R, returns as handler answer.
    # Outer's handler answer flows to outer's outer continuation = top.
    assert run(program) == Completed("approved-value")


def test_handler_side_perform_with_no_outer_handler_suspends_at_top() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "x",
                        Perform("eff.unhandled", Lit("ping")),
                        Return(Var("x")),
                    ),
                ),
            )
        ),
    )
    outcome = run(program)
    from shepherd_kernel_v3_reference.source.outcomes import Suspended

    assert isinstance(outcome, Suspended)
    assert outcome.effect_kind == "eff.unhandled"
    assert outcome.payload == "ping"


# --- schema validation ------------------------------------------------------


def make_registry_with_eff_a() -> EffectRegistry:
    r = EffectRegistry()
    r.register(
        EffectSignature(
            effect_kind="eff.a",
            payload_schema=TypeSchema(str),
            operation_result_schema=TaggedRecordSchema("Result"),
        )
    )
    return r


def test_payload_schema_violation_raises_at_perform() -> None:
    program = Handle(
        Perform("eff.a", Lit(123)),  # int, not str
        HandlerEnv((install("eff.a", body=lambda p: Return(Lit(None))),)),
    )
    with pytest.raises(Exception, match="perform.*payload"):
        run(program, registry=make_registry_with_eff_a())


def test_resume_value_schema_violation_raises() -> None:
    program = Handle(
        Perform("eff.a", Lit("ok-payload")),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "r",
                        Resume(Lit("not-tagged-Result")),
                        Return(Var("r")),
                    ),
                ),
            )
        ),
    )
    with pytest.raises(Exception, match="resume.*eff.a"):
        run(program, registry=make_registry_with_eff_a())


def test_payload_and_resume_pass_when_schemas_match() -> None:
    program = Handle(
        Perform("eff.a", Lit("ok-payload")),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "r",
                        Resume(Lit({"kind": "Result", "v": 1})),
                        Return(Var("r")),
                    ),
                ),
            )
        ),
    )
    assert run(program, registry=make_registry_with_eff_a()) == Completed({"kind": "Result", "v": 1})


# --- error cases ------------------------------------------------------------


def test_resume_outside_a_handler_body_is_a_runtime_error() -> None:
    program = Resume(Lit("nope"))
    with pytest.raises(RuntimeError, match="outside.*handler"):
        run(program)
