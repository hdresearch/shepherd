from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.kernel import (
    KernelProgram,
    elaborate,
    elaborate_publication_experimental,
    run_kernel,
)
from shepherd_kernel_v3_reference.kernel.events import (
    ContinuationDelay,
    ContinuationPending,
    ContinuationResumed,
    EffectDeclared,
    ForkBranch,
    ForkSummary,
    HandlerCaptured,
    HandlerForward,
    HandlerSelected,
    ResumptionCreated,
    SelectionClosed,
    TerminalResumeResult,
    WorkerReturned,
)
from shepherd_kernel_v3_reference.kernel.ir import HandlerEnvDef, HandlerInstallDef, KHandle, KPerform, KPure
from shepherd_kernel_v3_reference.kernel.recursive_machine import RecursiveKernelEvaluator
from shepherd_kernel_v3_reference.schemas import AnySchema, TypeSchema, ValidationError
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.eval_direct import run
from shepherd_kernel_v3_reference.source.experimental import Forward
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Completed, Delayed, Forked, ResumptionUsed, Suspended
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace.machine import run_trace


def install(effect_kind: str, body, handler_id: str = "h.v1") -> StaticHandlerInstall:
    return StaticHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        body=body,
        payload_name="_payload",
    )


def assert_kernel_matches_direct(term) -> None:
    kernel = elaborate_publication_experimental(term)
    assert run_kernel(kernel) == run(term)


def test_machine_evaluates_pure_let() -> None:
    assert_kernel_matches_direct(Let("x", Return(Lit(1)), Return(Var("x"))))


def test_machine_callable_resume_returns_worker_result_to_handler() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )

    assert_kernel_matches_direct(term)


def test_machine_emits_kernel_events_without_trace_runner() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    events = []

    outcome = RecursiveKernelEvaluator(elaborate(term), event_sink=events.append).run()

    assert outcome == Completed("value")
    assert [type(event) for event in events] == [
        EffectDeclared,
        HandlerSelected,
        ResumptionCreated,
        ContinuationResumed,
        WorkerReturned,
        HandlerCaptured,
    ]


def test_machine_records_context_restoration_points() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("value")), Return(Var("r"))),
                ),
            )
        ),
    )
    events = []

    assert RecursiveKernelEvaluator(elaborate(term), event_sink=events.append).run() == Completed("value")

    declaration = next(event for event in events if isinstance(event, EffectDeclared))
    selection = next(event for event in events if isinstance(event, HandlerSelected))
    resume = next(event for event in events if isinstance(event, ContinuationResumed))
    returned = next(event for event in events if isinstance(event, WorkerReturned))
    capture = next(event for event in events if isinstance(event, HandlerCaptured))

    assert declaration.execution_context_ref is not None
    assert selection.worker_context_ref == declaration.execution_context_ref
    assert selection.handler_context_ref is not None
    assert selection.outer_context_ref is not None
    assert resume.worker_context_ref == selection.worker_context_ref
    assert resume.handler_context_ref == selection.handler_context_ref
    assert returned.handler_context_ref == selection.handler_context_ref
    assert capture.outer_context_ref == selection.outer_context_ref


def test_machine_uses_deep_handling_for_resumed_worker_effects() -> None:
    term = Handle(
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
    )

    assert_kernel_matches_direct(term)


def test_machine_handler_side_effect_is_handled_outward() -> None:
    term = Handle(
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
    )

    assert_kernel_matches_direct(term)


def test_machine_post_resume_handler_side_effect_matches_direct() -> None:
    parent_env = HandlerEnv(
        (
            install(
                "approval.request",
                Let("r", Resume(Lit("PROMPT")), Return(Var("r"))),
                "h.approval",
            ),
            install(
                "audit.log",
                Let("r", Resume(Lit("acked")), Return(Var("r"))),
                "h.audit",
            ),
        )
    )
    supervisor_env = HandlerEnv(
        (
            install(
                "llm.generate",
                Let(
                    "prompt",
                    Perform("approval.request", Lit("proposal")),
                    Let(
                        "section",
                        Resume(Var("prompt")),
                        Let(
                            "_ack",
                            Perform("audit.log", Var("section")),
                            Return(Var("section")),
                        ),
                    ),
                ),
                "h.supervisor",
            ),
        )
    )
    term = Handle(
        Handle(
            Let("y", Perform("llm.generate", Lit("req")), Return(Var("y"))),
            supervisor_env,
        ),
        parent_env,
    )

    assert run(term) == Completed("PROMPT")
    assert_kernel_matches_direct(term)


def test_machine_forward_selects_outer_handler_for_same_declaration() -> None:
    term = Handle(
        Handle(
            Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
            HandlerEnv((install("eff.a", Forward(), "h.inner"),)),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let("r", Resume(Lit("outer-value")), Return(Var("r"))),
                    "h.outer",
                ),
            )
        ),
    )
    kernel = elaborate_publication_experimental(term)
    events = []
    outcome = RecursiveKernelEvaluator(kernel, event_sink=events.append).run()

    assert outcome == Completed("outer-value")
    assert [type(event) for event in events] == [
        EffectDeclared,
        HandlerSelected,
        ResumptionCreated,
        HandlerForward,
        SelectionClosed,
        HandlerSelected,
        ResumptionCreated,
        ContinuationResumed,
        WorkerReturned,
        HandlerCaptured,
    ]
    declarations = [event for event in events if isinstance(event, EffectDeclared)]
    selections = [event for event in events if isinstance(event, HandlerSelected)]
    assert len(declarations) == 1
    assert [selection.declaration_ref for selection in selections] == [
        declarations[0].ref,
        declarations[0].ref,
    ]


def test_machine_terminal_delay_exports_pending_resume_source() -> None:
    from shepherd_kernel_v3_reference.source.experimental import TerminalDelay

    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    kernel = elaborate_publication_experimental(term)
    events = []
    outcome = RecursiveKernelEvaluator(kernel, event_sink=events.append).run()

    assert isinstance(outcome, Delayed)
    assert outcome.reason == "waiting"
    assert [type(event) for event in events] == [
        EffectDeclared,
        HandlerSelected,
        ResumptionCreated,
        ContinuationPending,
        ContinuationDelay,
    ]

    resumed = outcome.pending.apply("resumed-value")
    assert resumed == Completed("resumed-value")
    assert [type(event) for event in events] == [
        EffectDeclared,
        HandlerSelected,
        ResumptionCreated,
        ContinuationPending,
        ContinuationDelay,
        ContinuationResumed,
        TerminalResumeResult,
    ]
    resume = next(event for event in events if isinstance(event, ContinuationResumed))
    result = next(event for event in events if isinstance(event, TerminalResumeResult))
    assert resume.source_record_type == "ContinuationPending"
    assert resume.returns_to_handler is False
    assert result.resume_ref == resume.ref


def test_machine_terminal_fork_runs_branch_scoped_terminal_resumes() -> None:
    from shepherd_kernel_v3_reference.source.experimental import TerminalFork

    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork(
                        (
                            ("branch:A", Lit("value-A")),
                            ("branch:B", Lit("value-B")),
                        )
                    ),
                    "h.fork",
                ),
            )
        ),
    )
    kernel = elaborate_publication_experimental(term)
    events = []
    outcome = RecursiveKernelEvaluator(kernel, event_sink=events.append).run()

    assert isinstance(outcome, Forked)
    assert outcome.branches == {
        "branch:A": Completed("value-A"),
        "branch:B": Completed("value-B"),
    }
    assert [type(event) for event in events] == [
        EffectDeclared,
        HandlerSelected,
        ResumptionCreated,
        ForkSummary,
        ForkBranch,
        ContinuationResumed,
        TerminalResumeResult,
        ForkBranch,
        ContinuationResumed,
        TerminalResumeResult,
    ]
    resumes = [event for event in events if isinstance(event, ContinuationResumed)]
    assert [resume.branch_ref for resume in resumes] == ["branch:A", "branch:B"]
    assert all(resume.source_record_type == "ForkBranch" for resume in resumes)
    assert all(resume.returns_to_handler is False for resume in resumes)


def test_machine_terminal_fork_runs_downstream_work_under_branch_ref() -> None:
    from shepherd_kernel_v3_reference.source.experimental import TerminalFork

    term = Handle(
        Let(
            "y",
            Perform("eff.a", Lit("payload")),
            Perform("eff.downstream", Var("y")),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork(
                        (
                            ("branch:A", Lit("value-A")),
                            ("branch:B", Lit("value-B")),
                        )
                    ),
                    "h.fork",
                ),
            )
        ),
    )
    events = []
    outcome = RecursiveKernelEvaluator(
        elaborate_publication_experimental(term),
        event_sink=events.append,
    ).run()

    assert isinstance(outcome, Forked)
    assert isinstance(outcome.branches["branch:A"], Suspended)
    assert isinstance(outcome.branches["branch:B"], Suspended)
    declarations = [event for event in events if isinstance(event, EffectDeclared)]
    assert [event.effect_kind for event in declarations] == [
        "eff.a",
        "eff.downstream",
        "eff.downstream",
    ]
    assert [event.branch_ref for event in declarations] == [
        "branch:root",
        "branch:A",
        "branch:B",
    ]
    assert [event.payload for event in declarations] == [
        "payload",
        "value-A",
        "value-B",
    ]


def test_machine_enforces_one_shot_handler_resumption() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    Let(
                        "x",
                        Resume(Lit("first")),
                        Let("y", Resume(Lit("second")), Return(Var("y"))),
                    ),
                ),
            )
        ),
    )

    with pytest.raises(ResumptionUsed):
        run_kernel(elaborate(term))


def test_machine_unhandled_suspension_can_resume_top_level_continuation() -> None:
    term = Let("x", Perform("eff.a", Lit(None)), Perform("eff.b", Var("x")))
    events = []

    outcome = RecursiveKernelEvaluator(elaborate(term), event_sink=events.append).run()

    assert isinstance(outcome, Suspended)
    resumed = outcome.continuation.apply("value")
    assert isinstance(resumed, Suspended)
    declarations = [event for event in events if isinstance(event, EffectDeclared)]
    assert [declaration.effect_kind for declaration in declarations] == ["eff.a", "eff.b"]
    assert not any(isinstance(event, TerminalResumeResult) for event in events)


def test_machine_suspended_continuation_checks_operation_result_schema() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    term = Let("x", Perform("eff.a", Lit(None)), Return(Var("x")))
    kernel = elaborate(term, registry=registry)

    outcome = RecursiveKernelEvaluator(kernel, registry=registry).run()

    assert isinstance(outcome, Suspended)
    with pytest.raises(ValidationError, match="resume.*eff.a"):
        outcome.continuation.apply("not-an-int")


def test_machine_uses_recorded_resume_schema_without_runtime_registry() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                StaticHandlerInstall(
                    effect_kind="eff.a",
                    handler_id="h.v1",
                    handled_result_schema=AnySchema(),
                    body=Let("r", Resume(Lit("not-an-int")), Return(Var("r"))),
                    payload_name="_payload",
                ),
            )
        ),
    )
    kernel = elaborate(term, registry=registry)

    with pytest.raises(ValidationError, match="resume.*eff.a"):
        run_kernel(kernel)


def test_machine_rejects_missing_payload_schema_ref() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", TypeSchema(int), AnySchema()))
    kernel = elaborate(Perform("eff.a", Lit(1)), registry=registry)
    assert isinstance(kernel.root, KPerform)
    malformed = KernelProgram(
        root=replace(kernel.root, payload_schema_ref="schema:missing"),
        binders=kernel.binders,
        handler_envs=kernel.handler_envs,
        schemas=kernel.schemas,
    )

    with pytest.raises(RuntimeError, match="schema:missing"):
        run_kernel(malformed)


def test_machine_rejects_missing_operation_result_schema_ref() -> None:
    malformed = KernelProgram(
        root=KPerform(
            effect_kind="eff.a",
            payload=Lit(None),
            operation_result_schema_ref="schema:missing",
        ),
        binders={},
        handler_envs={},
        schemas={},
    )

    with pytest.raises(RuntimeError, match="schema:missing"):
        run_kernel(malformed)


def test_machine_rejects_missing_handled_result_schema_ref() -> None:
    install = HandlerInstallDef(
        install_ref="install:0",
        effect_kind="eff.a",
        handler_id="h.v1",
        handled_result_schema_ref="schema:missing",
        payload_name="_payload",
        body=KPure(Lit("answer")),
    )
    malformed = KernelProgram(
        root=KHandle(KPerform("eff.a", Lit(None)), "handler-env:0"),
        binders={},
        handler_envs={"handler-env:0": HandlerEnvDef("handler-env:0", (install,))},
        schemas={},
    )

    with pytest.raises(RuntimeError, match="schema:missing"):
        run_kernel(malformed)


def test_trace_evidence_requires_content_addressable_values_in_captured_continuations() -> None:
    class Opaque:
        pass

    opaque = Opaque()
    term = Let(
        "opaque",
        Return(Lit(opaque)),
        Handle(
            Let("x", Perform("eff.a", Lit(None)), Return(Var("opaque"))),
            HandlerEnv(
                (
                    install(
                        "eff.a",
                        Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        ),
    )

    assert run(term) == Completed(opaque)
    assert run_kernel(elaborate(term)) == Completed(opaque)
    with pytest.raises(TypeError, match="not content-addressable"):
        run_trace(elaborate(term))
