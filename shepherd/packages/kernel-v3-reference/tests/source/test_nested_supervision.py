"""Handler-side effects and nested supervision (§02, §09).

These tests exercise patterns that the §13 worked example instantiates:

- a handler performs a handled effect BEFORE resuming the worker,
- a handler performs a handled effect AFTER resuming the worker (post-resume
  audit),
- both,
- three levels (worker -> supervisor -> parent -> grandparent).

The post-resume case is the one that previously surfaced a resume-return
accounting bug in spike 25 (per §12); at the source level we observe it
indirectly via the final value, since this package has no trace records.
"""

from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.eval_direct import run
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


def passthrough_resume(value_term):
    """Common shape: handler that resumes with a constant and returns the
    worker's R unchanged."""
    return lambda _: Let("r", Resume(value_term), Return(Var("r")))


# --- supervisor performs BEFORE resuming worker ----------------------------


def test_supervisor_performs_then_resumes_worker_with_transformed_value() -> None:
    # Parent answers "ALPHA" to approval.request. Supervisor uses this
    # to compute the resume-value for worker; worker echoes it back.
    parent_env = HandlerEnv((install("approval.request", passthrough_resume(Lit("ALPHA")), "h.parent"),))
    supervisor_env = HandlerEnv(
        (
            install(
                "llm.generate",
                body=lambda _req: Let(
                    "prompt",
                    Perform("approval.request", Lit("proposal")),
                    Let(
                        "section",
                        Resume(Var("prompt")),  # resume worker with the prompt
                        Return(Var("section")),
                    ),
                ),
                handler_id="h.supervisor",
            ),
        )
    )
    program = Handle(
        Handle(
            Let("y", Perform("llm.generate", Lit("req")), Return(Var("y"))),
            supervisor_env,
        ),
        parent_env,
    )
    assert run(program) == Completed("ALPHA")


# --- supervisor performs AFTER resuming worker (post-resume audit) ---------


def test_supervisor_audits_after_resume_and_returns_worker_R() -> None:
    # Supervisor: prompt = perform(approval); section = resume(prompt);
    #             ack    = perform(audit, section); return section.
    # Both approval and audit handled by parent. The worker's R should
    # survive the post-resume handler-side effect.
    parent_env = HandlerEnv(
        (
            install("approval.request", passthrough_resume(Lit("PROMPT")), "h.approval"),
            install("audit.log", passthrough_resume(Lit("acked")), "h.audit"),
        )
    )
    supervisor_env = HandlerEnv(
        (
            install(
                "llm.generate",
                body=lambda _req: Let(
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
                handler_id="h.supervisor",
            ),
        )
    )
    # Worker echoes whatever it's resumed with.
    program = Handle(
        Handle(
            Let("y", Perform("llm.generate", Lit("req")), Return(Var("y"))),
            supervisor_env,
        ),
        parent_env,
    )
    assert run(program) == Completed("PROMPT")


def test_supervisor_post_resume_handler_side_effect_observed_in_payload() -> None:
    # Make the audit's payload depend on the worker's R. The audit handler
    # passes through; the test confirms the section value flows into the
    # audit perform's payload (i.e., we don't lose track of section
    # between resume-return and the next perform).
    captured: list[object] = []
    parent_env = HandlerEnv(
        (
            install(
                "approval.request",
                passthrough_resume(Lit("PROMPT")),
                "h.approval",
            ),
            # Audit handler resolves an arg-capturing identity: returns
            # the section back unchanged via resume, but recording the
            # payload it received.
            install(
                "audit.log",
                body=lambda payload: captured.append(payload) or Let("r", Resume(Lit("ack")), Return(Var("r"))),
                handler_id="h.audit",
            ),
        )
    )
    supervisor_env = HandlerEnv(
        (
            install(
                "llm.generate",
                body=lambda _req: Let(
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
                handler_id="h.supervisor",
            ),
        )
    )
    program = Handle(
        Handle(
            Let("y", Perform("llm.generate", Lit("req")), Return(Var("y"))),
            supervisor_env,
        ),
        parent_env,
    )
    assert run(program) == Completed("PROMPT")
    assert captured == ["PROMPT"]  # audit saw the worker's R


# --- three-level nesting -----------------------------------------------------


def test_three_level_supervision_chain() -> None:
    # worker -> supervisor (handles "work") -> parent (handles "request") ->
    # grandparent (handles "escalate").
    grandparent_env = HandlerEnv((install("escalate", passthrough_resume(Lit("policy-X")), "h.gp"),))
    parent_env = HandlerEnv(
        (
            install(
                "request",
                body=lambda _: Let(
                    "policy",
                    Perform("escalate", Lit("ask-gp")),
                    Let("r", Resume(Var("policy")), Return(Var("r"))),
                ),
                handler_id="h.parent",
            ),
        )
    )
    supervisor_env = HandlerEnv(
        (
            install(
                "work",
                body=lambda _: Let(
                    "policy",
                    Perform("request", Lit("ask-parent")),
                    Let("r", Resume(Var("policy")), Return(Var("r"))),
                ),
                handler_id="h.sup",
            ),
        )
    )
    program = Handle(
        Handle(
            Handle(
                Let("y", Perform("work", Lit("req")), Return(Var("y"))),
                supervisor_env,
            ),
            parent_env,
        ),
        grandparent_env,
    )
    # Expectation: grandparent's "policy-X" travels:
    #   gp.resume("policy-X") -> parent's get-policy resume returns
    #     "policy-X" to parent.body's resume site -> parent.resume("policy-X")
    #     -> supervisor.body resumes worker with "policy-X" -> worker R = "policy-X"
    # All handlers passthrough_resume, so each one's answer is its resume
    # call's eventual return = the worker's R = "policy-X".
    assert run(program) == Completed("policy-X")


# --- pre-AND-post-resume handler effects -----------------------------------


def test_supervisor_performs_before_AND_after_resume() -> None:
    # Confirms that both handler-side perform sites work in one trace and
    # the worker's R survives intact through the second perform.
    parent_env = HandlerEnv(
        (
            install("get-prompt", passthrough_resume(Lit("PROMPT")), "h.gp"),
            install("audit", passthrough_resume(Lit("ack")), "h.audit"),
        )
    )
    supervisor_env = HandlerEnv(
        (
            install(
                "work",
                body=lambda _: Let(
                    "prompt",
                    Perform("get-prompt", Lit(None)),
                    Let(
                        "section",
                        Resume(Var("prompt")),
                        Let(
                            "_",
                            Perform("audit", Var("section")),
                            Return(Var("section")),
                        ),
                    ),
                ),
                handler_id="h.sup",
            ),
        )
    )
    program = Handle(
        Handle(
            Let("y", Perform("work", Lit(None)), Return(Var("y"))),
            supervisor_env,
        ),
        parent_env,
    )
    assert run(program) == Completed("PROMPT")
