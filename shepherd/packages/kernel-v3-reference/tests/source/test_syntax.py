from shepherd_kernel_v3_reference.source.handlers import HandlerEnv
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Return, Var


def test_return_carries_an_expr() -> None:
    t = Return(Lit(7))
    assert t.expr == Lit(7)


def test_let_is_two_subterms_and_a_name() -> None:
    t = Let("x", Return(Lit(1)), Return(Var("x")))
    assert t.name == "x"
    assert t.body == Return(Var("x"))


def test_perform_carries_kind_and_payload_expr() -> None:
    t = Perform("eff.foo", Lit({"k": "v"}))
    assert t.effect_kind == "eff.foo"
    assert t.payload == Lit({"k": "v"})


def test_handle_takes_body_and_env() -> None:
    body = Return(Lit(0))
    henv = HandlerEnv(())
    t = Handle(body, henv)
    assert t.body == body
    assert t.handler_env == henv


def test_terms_are_value_equal_when_structurally_equal() -> None:
    a = Let("x", Return(Lit(1)), Return(Var("x")))
    b = Let("x", Return(Lit(1)), Return(Var("x")))
    assert a == b


def test_terms_are_hashable_so_they_can_appear_in_sets() -> None:
    s = {Return(Lit(1)), Return(Lit(1)), Return(Lit(2))}
    assert len(s) == 2
