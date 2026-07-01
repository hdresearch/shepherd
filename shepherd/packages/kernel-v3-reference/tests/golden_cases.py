from __future__ import annotations

from dataclasses import dataclass

from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import Abort, Handle, Let, Lit, Perform, Resume, Return, Var


@dataclass(frozen=True)
class GoldenCase:
    name: str
    boundary: str
    completed: bool
    term: object


def install(effect_kind: str, body, handler_id: str = "h.v1") -> StaticHandlerInstall:
    return StaticHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        payload_name="_payload",
        body=body,
    )


def golden_cases() -> tuple[GoldenCase, ...]:
    return (
        GoldenCase(
            "core0-pure-let",
            "core0",
            True,
            Let("x", Return(Lit(1)), Return(Var("x"))),
        ),
        GoldenCase(
            "core0-handled-return",
            "core0",
            True,
            Handle(
                Perform("eff.a", Lit(None)),
                HandlerEnv((install("eff.a", Return(Lit("handled"))),)),
            ),
        ),
        GoldenCase(
            "core0-one-resume",
            "core0",
            True,
            Handle(
                Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
                HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
            ),
        ),
        GoldenCase(
            "core0-deep-handling",
            "core0",
            True,
            Handle(
                Let(
                    "x",
                    Perform("eff.a", Lit("first")),
                    Let("y", Perform("eff.a", Lit("second")), Return(Var("y"))),
                ),
                HandlerEnv((install("eff.a", Let("r", Resume(Lit("R")), Return(Var("r")))),)),
            ),
        ),
        GoldenCase(
            "core0-handler-side-outward-perform",
            "core0",
            True,
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
        ),
        GoldenCase(
            "core0-unhandled-suspension-prefix",
            "core0_prefix",
            False,
            Let("x", Perform("eff.unhandled", Lit("payload")), Return(Var("x"))),
        ),
        GoldenCase(
            "core-a-answer-abort",
            "core_a",
            True,
            Handle(
                Perform("eff.a", Lit(None)),
                HandlerEnv((install("eff.a", Abort(Lit("blocked"))),)),
            ),
        ),
        GoldenCase(
            "core-a-outer-return-closes-inner",
            "core_a",
            True,
            Handle(
                Handle(
                    Perform("eff.inner", Lit(None)),
                    HandlerEnv(
                        (
                            install(
                                "eff.inner",
                                Let(
                                    "x",
                                    Perform("eff.outer", Lit("ask")),
                                    Return(Var("x")),
                                ),
                                "h.inner",
                            ),
                        )
                    ),
                ),
                HandlerEnv((install("eff.outer", Return(Lit("outer-return")), "h.outer"),)),
            ),
        ),
        GoldenCase(
            "core-a-outer-abort-closes-inner",
            "core_a",
            True,
            Handle(
                Handle(
                    Perform("eff.inner", Lit(None)),
                    HandlerEnv(
                        (
                            install(
                                "eff.inner",
                                Let(
                                    "x",
                                    Perform("eff.outer", Lit("ask")),
                                    Return(Var("x")),
                                ),
                                "h.inner",
                            ),
                        )
                    ),
                ),
                HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
            ),
        ),
    )
