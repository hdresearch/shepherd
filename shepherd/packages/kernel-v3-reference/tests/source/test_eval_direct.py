import pytest

from shepherd_kernel_v3_reference.schemas import AnySchema, TypeSchema, ValidationError
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature
from shepherd_kernel_v3_reference.source.eval_direct import run
from shepherd_kernel_v3_reference.source.handlers import DynamicHandlerInstall, HandlerEnv
from shepherd_kernel_v3_reference.source.outcomes import Completed, ResumptionUsed, Suspended
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Return, Var
from shepherd_kernel_v3_reference.source.values import Env

# --- Pure computation -----------------------------------------------------


def test_return_lit_completes_with_literal_value() -> None:
    assert run(Return(Lit(42))) == Completed(42)


def test_return_var_resolves_in_env() -> None:
    env = Env().extend("x", "hello")
    assert run(Return(Var("x")), env=env) == Completed("hello")


def test_var_lookup_failure_propagates() -> None:
    with pytest.raises(KeyError):
        run(Return(Var("never-bound")))


# --- Let -------------------------------------------------------------------


def test_let_binds_bound_value_into_body() -> None:
    program = Let("x", Return(Lit(7)), Return(Var("x")))
    assert run(program) == Completed(7)


def test_let_sequences_nested_lets() -> None:
    program = Let(
        "x",
        Return(Lit(1)),
        Let("y", Return(Lit(2)), Return(Var("y"))),
    )
    assert run(program) == Completed(2)


def test_let_inner_binding_does_not_pollute_outer_scope() -> None:
    program = Let(
        "x",
        Return(Lit(1)),
        Let("y", Return(Lit(2)), Return(Var("x"))),
    )
    assert run(program) == Completed(1)


# --- Unhandled Perform ----------------------------------------------------


def test_unhandled_perform_suspends_with_kind_and_payload() -> None:
    outcome = run(Perform("eff.a", Lit({"k": "v"})))
    assert isinstance(outcome, Suspended)
    assert outcome.effect_kind == "eff.a"
    assert outcome.payload == {"k": "v"}


def test_unhandled_perform_payload_is_evaluated_under_env() -> None:
    env = Env().extend("p", "loaded-payload")
    outcome = run(Perform("eff.a", Var("p")), env=env)
    assert isinstance(outcome, Suspended)
    assert outcome.payload == "loaded-payload"


def test_suspension_continuation_carries_full_outer_program() -> None:
    # The Suspended.continuation is the rest of the program after the
    # perform: `Let("x", ., Return(Var("x")))`. Applying a synthetic
    # operation-result value should run that tail.
    program = Let("x", Perform("eff.a", Lit({})), Return(Var("x")))
    outcome = run(program)
    assert isinstance(outcome, Suspended)
    resumed = outcome.continuation.apply("from_handler")
    assert resumed == Completed("from_handler")


def test_suspension_continuation_is_one_shot() -> None:
    # Single-root-branch fragment: the underlying generator is mutated by
    # the first apply. A second apply must raise rather than silently read
    # exhausted state.
    program = Let("x", Perform("eff.a", Lit({})), Return(Var("x")))
    outcome = run(program)
    assert isinstance(outcome, Suspended)
    outcome.continuation.apply("first")
    with pytest.raises(ResumptionUsed, match="already applied"):
        outcome.continuation.apply("second")


def test_suspension_continuation_checks_operation_result_schema() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    program = Let("x", Perform("eff.a", Lit(None)), Return(Var("x")))
    outcome = run(program, registry=registry)
    assert isinstance(outcome, Suspended)

    with pytest.raises(ValidationError, match="resume.*eff.a"):
        outcome.continuation.apply("not-an-int")


def test_suspension_continuation_accepts_matching_operation_result_schema() -> None:
    registry = EffectRegistry()
    registry.register(EffectSignature("eff.a", AnySchema(), TypeSchema(int)))
    program = Let("x", Perform("eff.a", Lit(None)), Return(Var("x")))
    outcome = run(program, registry=registry)
    assert isinstance(outcome, Suspended)

    assert outcome.continuation.apply(12) == Completed(12)


# --- Handle frame -----------------------------------------------------


def test_handle_with_no_matching_install_falls_through_to_suspension() -> None:
    install = DynamicHandlerInstall(
        effect_kind="eff.b",
        handler_id="h.v1",
        handled_result_schema=AnySchema(),
        body=lambda payload: Return(Lit(None)),
    )
    program = Handle(
        Perform("eff.a", Lit({})),
        HandlerEnv((install,)),
    )
    outcome = run(program)
    assert isinstance(outcome, Suspended)
    assert outcome.effect_kind == "eff.a"


def test_nested_handles_search_innermost_then_outward_for_unhandled_effect() -> None:
    inner_env = HandlerEnv(())
    outer_env = HandlerEnv(
        (
            DynamicHandlerInstall(
                effect_kind="eff.b",
                handler_id="hb.v1",
                handled_result_schema=AnySchema(),
                body=lambda p: Return(Lit(None)),
            ),
        ),
    )
    program = Handle(
        Handle(Perform("eff.a", Lit({})), inner_env),
        outer_env,
    )
    outcome = run(program)
    assert isinstance(outcome, Suspended)
    assert outcome.effect_kind == "eff.a"


def test_handle_with_matching_install_runs_handler_body_as_replacement() -> None:
    # No Resume in handler body: handler replaces the worker continuation
    # entirely (a degenerate "answer-without-resume" pattern). The handler
    # body's Return becomes the Handle's value (§02 deep-handler equation
    # with no resume call).
    install = DynamicHandlerInstall(
        effect_kind="eff.a",
        handler_id="ha.v1",
        handled_result_schema=AnySchema(),
        body=lambda p: Return(Lit("replaced")),
    )
    program = Handle(
        Perform("eff.a", Lit({})),
        HandlerEnv((install,)),
    )
    assert run(program) == Completed("replaced")
