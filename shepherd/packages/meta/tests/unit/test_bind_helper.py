"""Tests for binding after the top-level facade hard cut."""

from shepherd_core.effects import Effect
from shepherd_core.scope import ContextRef
from shepherd_runtime.scope import Scope
from shepherd_tests.contexts import CounterContext


def test_legacy_top_level_bind_helper_is_removed() -> None:
    import shepherd

    for name in ("bind", "Scope", "ScopeNotConfiguredError"):
        assert not hasattr(shepherd, name)
        assert name not in shepherd.__all__


def test_owner_path_scope_bind_returns_context_ref() -> None:
    ctx = CounterContext(count=42)

    with Scope() as scope:
        ref = scope.bind("test", ctx)

        assert isinstance(ref, ContextRef)
        assert ref.count == 42


def test_owner_path_scope_bind_context_ref_auto_updates() -> None:
    ctx = CounterContext(count=0)

    with Scope() as scope:
        ref = scope.bind("test", ctx)

        assert ref.count == 0
        scope.emit(Effect(effect_type="increment", binding_name="test"))
        assert ref.count == 1
