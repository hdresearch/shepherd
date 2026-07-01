import pytest

from shepherd_kernel_v3_reference.schemas import TaggedRecordSchema
from shepherd_kernel_v3_reference.source.handlers import (
    AnswerCompletion,
    DynamicHandlerInstall,
    HandlerEnv,
)
from shepherd_kernel_v3_reference.source.syntax import Lit, Return


def make_install(effect_kind: str = "eff.foo", handler_id: str = "h.v1") -> DynamicHandlerInstall:
    return DynamicHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=TaggedRecordSchema("Section"),
        body=lambda payload: Return(Lit(payload)),
    )


def test_handler_env_lookup_returns_matching_install() -> None:
    a = make_install("eff.a", "ha.v1")
    b = make_install("eff.b", "hb.v1")
    env = HandlerEnv((a, b))
    assert env.lookup("eff.a") is a
    assert env.lookup("eff.b") is b
    assert env.lookup("eff.never") is None


def test_handler_env_first_match_wins_when_kinds_collide() -> None:
    a = make_install("eff.x", "ha")
    b = make_install("eff.x", "hb")
    env = HandlerEnv((a, b))
    assert env.lookup("eff.x") is a


def test_answer_completion_kinds() -> None:
    a = AnswerCompletion(kind="ordinary", value="ok")
    b = AnswerCompletion(kind="abort", value="rejected")
    assert a.kind == "ordinary"
    assert b.kind == "abort"


def test_answer_completion_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="ordinary' or 'abort'"):
        AnswerCompletion(kind="bogus", value=None)


def test_dynamic_handler_install_body_is_a_builder() -> None:
    install = make_install()
    term = install.body({"k": "v"})
    assert term == Return(Lit({"k": "v"}))
