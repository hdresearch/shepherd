"""§13 worked example, callable supervision case.

Three levels:

    worker     performs llm.generate
    supervisor handles  llm.generate
    parent     handles  approval.request, model.call, audit.log

The bundle's §13 leaves `model_call` as a "provider/foreign step"; we model
it as another handled effect for self-containment, with the parent acting
as the provider. The bundle explicitly notes this is acceptable
("represented as its own handled effect in a fuller example").

Source signatures (§13 / §29):

    approval.request : Proposal        -> Prompt
    llm.generate     : GenerateRequest -> Draft
    audit.log        : AuditEntry      -> Unit
    model.call       : Prompt          -> Draft

The supervisor body is::

    approved_prompt = perform(approval.request, Proposal("draft section"))
    draft = perform(model.call, approved_prompt)
    section = resume(draft)
    _ = perform(audit.log, AuditEntry(section))
    return section

The §29 type discipline: the worker resumption is typed by the
*operation-result* type of `llm.generate`, which is `Draft`, not the
Prompt that approval.request produced. Substituting the prompt for the
draft at the resume site is caught by the runtime schema check.
"""

from __future__ import annotations

from shepherd_kernel_v3_reference.schemas import AnySchema, TaggedRecordSchema
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import (
    Computation,
    Handle,
    Let,
    Lit,
    Perform,
    RecordExpr,
    Resume,
    Return,
    Var,
)

# --- schemas ----------------------------------------------------------------

PROPOSAL = TaggedRecordSchema("Proposal")
PROMPT = TaggedRecordSchema("Prompt")
DRAFT = TaggedRecordSchema("Draft")
GENERATE_REQUEST = TaggedRecordSchema("GenerateRequest")
AUDIT_ENTRY = TaggedRecordSchema("AuditEntry")
UNIT = AnySchema()


def build_registry() -> EffectRegistry:
    r = EffectRegistry()
    r.register(
        EffectSignature("approval.request", PROPOSAL, PROMPT),
    )
    r.register(
        EffectSignature("llm.generate", GENERATE_REQUEST, DRAFT),
    )
    r.register(
        EffectSignature("audit.log", AUDIT_ENTRY, UNIT),
    )
    r.register(
        EffectSignature("model.call", PROMPT, DRAFT),
    )
    return r


# --- handlers ---------------------------------------------------------------


def supervisor_install(
    *,
    resume_with: str = "draft",
) -> StaticHandlerInstall:
    """Build the supervisor's llm.generate handler.

    `resume_with` selects which bound name the supervisor passes to its
    worker resume call. The §29 type-correct path is `"draft"`. Setting
    `"approved_prompt"` is the misuse the schema check is meant to catch
    (resuming an llm.generate worker with a Prompt instead of a Draft).
    """

    return StaticHandlerInstall(
        effect_kind="llm.generate",
        handler_id="supervisor.v1",
        # The handler's answer is what the supervisor returns to the parent's
        # body, which is itself a Handle whose value flows to the program's
        # outcome. In this worked example the eventual R is a Draft.
        handled_result_schema=DRAFT,
        payload_name="req",
        body=Let(
            "approved_prompt",
            Perform(
                "approval.request",
                Lit({"kind": "Proposal", "text": "draft section"}),
            ),
            Let(
                "draft",
                Perform("model.call", Var("approved_prompt")),
                Let(
                    "section",
                    Resume(Var(resume_with)),
                    Let(
                        "_ack",
                        Perform(
                            "audit.log",
                            RecordExpr(
                                (
                                    ("kind", Lit("AuditEntry")),
                                    ("section", Var("section")),
                                )
                            ),
                        ),
                        Return(Var("section")),
                    ),
                ),
            ),
        ),
    )


def parent_env() -> HandlerEnv:
    # All three parent handlers have the same handled-result type (Draft),
    # because their selections share the same outer continuation: parent's
    # body, whose value is the supervisor's answer (a Draft).
    return HandlerEnv(
        (
            StaticHandlerInstall(
                effect_kind="approval.request",
                handler_id="parent.approval.v1",
                handled_result_schema=DRAFT,
                payload_name="proposal",
                body=Let(
                    "r",
                    Resume(
                        Lit(
                            {
                                "kind": "Prompt",
                                "text": "approved: draft section",
                            }
                        )
                    ),
                    Return(Var("r")),
                ),
            ),
            StaticHandlerInstall(
                effect_kind="model.call",
                handler_id="parent.model.v1",
                handled_result_schema=DRAFT,
                payload_name="prompt",
                body=Let(
                    "r",
                    Resume(
                        Lit(
                            {
                                "kind": "Draft",
                                "text": "draft-of: approved: draft section",
                            }
                        )
                    ),
                    Return(Var("r")),
                ),
            ),
            StaticHandlerInstall(
                effect_kind="audit.log",
                handler_id="parent.audit.v1",
                handled_result_schema=DRAFT,
                payload_name="entry",
                body=Let(
                    "r",
                    Resume(Lit({"kind": "Unit"})),
                    Return(Var("r")),
                ),
            ),
        )
    )


# --- worker -----------------------------------------------------------------


def worker_term() -> Computation:
    return Let(
        "y",
        Perform(
            "llm.generate",
            Lit({"kind": "GenerateRequest", "prompt_seed": "draft section"}),
        ),
        Return(Var("y")),
    )


def build_program(*, supervisor_resume_with: str = "draft") -> Computation:
    """Assemble the §13 program. `supervisor_resume_with` is exposed so the
    type-discipline test can flip it to `"approved_prompt"` and verify
    that the operation-result schema rejects the swap."""

    return Handle(
        Handle(
            worker_term(),
            HandlerEnv((supervisor_install(resume_with=supervisor_resume_with),)),
        ),
        parent_env(),
    )
