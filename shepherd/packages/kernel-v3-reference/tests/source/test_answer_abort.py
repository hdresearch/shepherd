"""Answer-producing completions and handled-result schema (§03, §10)."""

import pytest

from shepherd_kernel_v3_reference.schemas import AnySchema, TaggedRecordSchema, TypeSchema
from shepherd_kernel_v3_reference.source.eval_direct import AbortAfterResume, run
from shepherd_kernel_v3_reference.source.experimental import Forward, TerminalDelay, TerminalFork
from shepherd_kernel_v3_reference.source.handlers import DynamicHandlerInstall, HandlerEnv
from shepherd_kernel_v3_reference.source.outcomes import Completed
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
from shepherd_kernel_v3_reference.source.wellformed import (
    SourceFormError,
    validate_handler_body,
    validate_program,
    validate_publication_experimental_handler_body,
    validate_publication_experimental_program,
)


def install(
    effect_kind: str,
    body,
    handler_id: str = "h.v1",
    handled_result_schema=None,
) -> DynamicHandlerInstall:
    return DynamicHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=handled_result_schema or AnySchema(),
        body=body,
    )


# --- Abort ----------------------------------------------------------------


def test_abort_delivers_its_value_as_handler_answer() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", body=lambda p: Abort(Lit("rejected"))),)),
    )
    assert run(program) == Completed("rejected")


def test_abort_skips_worker_continuation() -> None:
    # If Abort fails to short-circuit, the post-perform Perform fires and
    # we'd see a Suspended on eff.unhandled. Aborting properly means we
    # complete with "rejected".
    program = Handle(
        Let(
            "x",
            Perform("eff.a", Lit(None)),
            Perform("eff.unhandled", Lit("should-never-fire")),
        ),
        HandlerEnv((install("eff.a", body=lambda p: Abort(Lit("rejected"))),)),
    )
    assert run(program) == Completed("rejected")


def test_abort_with_payload_resolved_from_env() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let("msg", Return(Lit("computed-rejection")), Abort(Var("msg"))),
                ),
            )
        ),
    )
    assert run(program) == Completed("computed-rejection")


def test_abort_under_ordinary_let_bound_position_is_rejected() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "x",
                        Abort(Lit("rejected")),
                        Return(Lit("should-not-run")),
                    ),
                ),
            )
        ),
    )
    with pytest.raises(SourceFormError, match="answer position"):
        run(program)


@pytest.mark.parametrize(
    "term",
    [
        Forward(),
        TerminalDelay(Lit("waiting")),
        TerminalFork((("branch:A", Lit("value")),)),
    ],
)
def test_publication_control_forms_require_experimental_handler_validation(term) -> None:
    validate_publication_experimental_handler_body(term)
    with pytest.raises(SourceFormError, match="publication experimental profile"):
        validate_handler_body(term)
    with pytest.raises(SourceFormError, match="publication experimental profile"):
        validate_program(term)
    with pytest.raises(SourceFormError, match="outside any handler body"):
        validate_publication_experimental_program(term)
    with pytest.raises(SourceFormError, match="answer position"):
        validate_publication_experimental_handler_body(Let("x", term, Return(Lit("never"))))


def test_publication_control_forms_are_not_root_exports() -> None:
    import shepherd_kernel_v3_reference

    assert not hasattr(shepherd_kernel_v3_reference, "Forward")
    assert not hasattr(shepherd_kernel_v3_reference, "PublicationExperimentalComputation")
    assert not hasattr(shepherd_kernel_v3_reference, "TerminalDelay")
    assert not hasattr(shepherd_kernel_v3_reference, "TerminalFork")


def test_abort_outside_handler_body_is_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="outside any handler body"):
        run(Abort(Lit("no-handler")))


def test_abort_after_resume_is_rejected() -> None:
    # §10 lists `resume(...); Abort(...)` among rejected histories. In the
    # core, Abort is the no-prior-worker-resume short-circuit case.
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let(
                        "r",
                        Resume(Lit("worker-input")),
                        Abort(Lit("rejected-after-resume")),
                    ),
                ),
            )
        ),
    )
    with pytest.raises(AbortAfterResume, match="aborted after invoking"):
        run(program)


def test_abort_before_resume_is_still_allowed() -> None:
    # Sanity check: the new guard does not break the ordinary
    # no-prior-resume Abort case.
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", body=lambda p: Abort(Lit("rejected"))),)),
    )
    assert run(program) == Completed("rejected")


# --- handled-result schema validation -------------------------------------


def test_handler_answer_validated_against_handled_result_schema() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Return(Lit({"kind": "Section", "text": "ok"})),
                    handled_result_schema=TaggedRecordSchema("Section"),
                ),
            )
        ),
    )
    assert run(program) == Completed({"kind": "Section", "text": "ok"})


def test_handler_answer_rejected_when_schema_mismatch() -> None:
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Return(Lit({"kind": "Prompt"})),  # wrong tag
                    handled_result_schema=TaggedRecordSchema("Section"),
                ),
            )
        ),
    )
    with pytest.raises(Exception, match="handler.*answer"):
        run(program)


def test_abort_value_validated_against_handled_result_schema() -> None:
    # Same schema law applies whether the handler completes via Return,
    # Abort, or post-resume return. The check is at handler completion.
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Abort(Lit("not-an-int")),
                    handled_result_schema=TypeSchema(int),
                ),
            )
        ),
    )
    with pytest.raises(Exception, match="handler.*answer"):
        run(program)


def test_post_resume_handler_answer_validated() -> None:
    # Handler resumes, observes worker R, returns something. That return
    # value is what gets schema-checked.
    program = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    body=lambda p: Let("r", Resume(Lit("v")), Return(Lit("not-tagged"))),
                    handled_result_schema=TaggedRecordSchema("Section"),
                ),
            )
        ),
    )
    with pytest.raises(Exception, match="handler.*answer"):
        run(program)
