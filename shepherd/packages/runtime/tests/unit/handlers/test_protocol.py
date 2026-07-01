"""Tests for handler protocols: HandlerContext, EffectHandler, Materializer."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.handlers import (
    EffectHandler,
    HandlerContext,
    MaterializationError,
    Materializer,
    ReversalError,
    SimpleHandlerContext,
)
from shepherd_runtime.scope import Scope


class TestHandlerContextProtocol:
    """Tests for HandlerContext protocol compliance."""

    def test_simple_handler_context_satisfies_protocol(self):
        """SimpleHandlerContext implements HandlerContext protocol."""
        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            assert isinstance(context, HandlerContext)

    def test_handler_context_scope_access(self):
        """HandlerContext provides scope access."""
        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            assert context.scope is scope
            assert context.scope_id == scope.id

    def test_handler_context_state_access(self):
        """HandlerContext provides state access."""
        with Scope() as scope:
            context = SimpleHandlerContext(scope, state={"ws": {"files": []}})
            assert context.get_state("ws") == {"files": []}
            assert context.get_state("nonexistent") is None

    def test_simple_handler_context_set_state(self):
        """SimpleHandlerContext allows setting state for testing."""
        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            context.set_state("test", {"value": 42})
            assert context.get_state("test") == {"value": 42}


class TestEffectHandlerProtocol:
    """Tests for EffectHandler protocol compliance."""

    def test_class_satisfies_protocol(self):
        """A class with required methods satisfies EffectHandler."""

        class MyHandler:
            @property
            def effect_type(self) -> type[Effect]:
                return Effect

            async def handle(
                self,
                effect: Effect,
                context: HandlerContext,
                resume: Callable[[Any], Awaitable[Any]],
            ) -> Any:
                return await resume(None)

            def can_handle(self, effect: Effect) -> bool:
                return True

        handler = MyHandler()
        assert isinstance(handler, EffectHandler)

    def test_protocol_is_runtime_checkable(self):
        """EffectHandler is runtime checkable."""

        # A non-conforming class should not match
        class NotAHandler:
            pass

        assert not isinstance(NotAHandler(), EffectHandler)


class TestMaterializerProtocol:
    """Tests for Materializer protocol compliance."""

    def test_class_satisfies_protocol(self):
        """A class with required methods satisfies Materializer."""
        from shepherd_runtime.effect_materialization import MaterializationResult

        class MyMaterializer:
            @property
            def effect_type(self) -> type[Effect]:
                return Effect

            def materialize(self, effect: Effect) -> MaterializationResult:
                return MaterializationResult.ok()

            def can_reverse(self, effect: Effect) -> bool:
                return False

            def reverse(self, effect: Effect) -> None:
                raise ReversalError(effect, "Cannot reverse")

        materializer = MyMaterializer()
        assert isinstance(materializer, Materializer)

    def test_protocol_is_runtime_checkable(self):
        """Materializer is runtime checkable."""

        class NotAMaterializer:
            pass

        assert not isinstance(NotAMaterializer(), Materializer)


class TestErrors:
    """Tests for handler-related errors."""

    def test_materialization_error(self):
        """MaterializationError can be raised and caught."""
        from shepherd_core.effects import Effect

        effect = Effect()
        with pytest.raises(MaterializationError) as exc_info:
            raise MaterializationError(effect, "Failed to write file")

        assert exc_info.value.effect is effect
        assert exc_info.value.error == "Failed to write file"
        assert "Failed to write file" in str(exc_info.value)

    def test_reversal_error(self):
        """ReversalError can be raised and caught."""
        from shepherd_core.effects import Effect

        effect = Effect()
        with pytest.raises(ReversalError) as exc_info:
            raise ReversalError(effect, "Cannot reverse this effect")

        assert exc_info.value.effect is effect
        assert exc_info.value.error == "Cannot reverse this effect"
        assert "Cannot reverse this effect" in str(exc_info.value)
