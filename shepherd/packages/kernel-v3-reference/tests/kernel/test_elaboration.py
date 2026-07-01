import pytest

from shepherd_kernel_v3_reference.kernel import (
    Elaborator,
    elaborate,
    elaborate_publication_experimental,
)
from shepherd_kernel_v3_reference.kernel.ir import KBind, KHandle, KPerform, KPure, KResumeWith, KTerminalFork
from shepherd_kernel_v3_reference.schemas import AnySchema, TaggedRecordSchema
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.experimental import TerminalFork
from shepherd_kernel_v3_reference.source.handlers import (
    DynamicHandlerInstall,
    HandlerEnv,
    StaticHandlerInstall,
)
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.source.wellformed import SourceFormError


def test_source_let_elaborates_to_bind_with_registered_binder() -> None:
    program = Let("x", Return(Lit(1)), Return(Var("x")))

    kernel = elaborate(program)

    assert isinstance(kernel.root, KBind)
    assert isinstance(kernel.root.bound, KPure)
    binder = kernel.binders[kernel.root.binder_id]
    assert binder.param_name == "x"
    assert binder.binder_env_ref == kernel.root.binder_env_ref
    assert isinstance(binder.body, KPure)
    assert binder.body.expr == Var("x")


def test_handler_resume_elaborates_through_ordinary_bind() -> None:
    handler_term = Let("r", Resume(Lit("value")), Return(Var("r")))
    elaborator = Elaborator(registry=EffectRegistry())

    handler_ir = elaborator.elaborate_handler_body(handler_term)

    assert isinstance(handler_ir, KBind)
    assert isinstance(handler_ir.bound, KResumeWith)
    binder = elaborator.binders[handler_ir.binder_id]
    assert binder.param_name == "r"
    assert isinstance(binder.body, KPure)
    assert binder.body.expr == Var("r")


def test_handle_elaborates_to_handler_env_ref() -> None:
    install = StaticHandlerInstall(
        effect_kind="eff.a",
        handler_id="h.v1",
        handled_result_schema=AnySchema(),
        body=Return(Var("payload")),
        payload_name="payload",
    )
    program = Handle(Perform("eff.a", Lit(None)), HandlerEnv((install,)))

    kernel = elaborate(program)

    assert isinstance(kernel.root, KHandle)
    assert isinstance(kernel.root.body, KPerform)
    env_def = kernel.handler_envs[kernel.root.handler_env_ref]
    assert len(env_def.bindings) == 1
    assert env_def.bindings[0].effect_kind == "eff.a"
    assert env_def.bindings[0].handler_id == "h.v1"


def test_kernel_elaboration_rejects_dynamic_handler_builder() -> None:
    install = DynamicHandlerInstall(
        effect_kind="eff.a",
        handler_id="h.v1",
        handled_result_schema=AnySchema(),
        body=lambda payload: Return(Lit(payload)),
    )
    program = Handle(Perform("eff.a", Lit(None)), HandlerEnv((install,)))

    with pytest.raises(SourceFormError, match="static handler bodies"):
        elaborate(program)


def test_effect_signature_schemas_are_referenced_from_perform() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", TaggedRecordSchema("Payload"), TaggedRecordSchema("Result")))
    program = Perform("eff.a", Lit({"kind": "Payload"}))

    kernel = elaborate(program, registry=registry)

    assert isinstance(kernel.root, KPerform)
    assert kernel.root.payload_schema_ref == "schema:payload:eff.a"
    assert kernel.root.operation_result_schema_ref == "schema:operation-result:eff.a"
    assert kernel.root.payload_schema_ref in kernel.schemas
    assert kernel.root.operation_result_schema_ref in kernel.schemas


def test_publication_source_elaboration_accepts_deep_terminal_fork_program() -> None:
    kernel = elaborate_publication_experimental(_sequential_terminal_fork_program(1000))

    assert isinstance(kernel.root, KHandle)
    assert len(kernel.binders) == 1000
    env_def = kernel.handler_envs[kernel.root.handler_env_ref]
    assert len(env_def.bindings) == 1
    assert isinstance(env_def.bindings[0].body, KTerminalFork)


def _sequential_terminal_fork_program(count: int) -> object:
    term: object = Return(Lit("done"))
    for idx in reversed(range(count)):
        term = Let(f"x{idx}", Perform("eff.a", Lit(f"payload-{idx}")), term)
    return Handle(
        term,
        HandlerEnv(
            (
                StaticHandlerInstall(
                    effect_kind="eff.a",
                    handler_id="h.fork",
                    handled_result_schema=AnySchema(),
                    payload_name="_payload",
                    body=TerminalFork((("branch:A", Lit("fork-value")),)),
                ),
            )
        ),
    )
