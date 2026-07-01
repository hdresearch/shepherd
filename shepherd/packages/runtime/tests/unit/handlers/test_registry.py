"""Tests for HandlerRegistry: registration, lookup, and dispatch."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from shepherd_core.effects import Effect, FileCreate, FilePatch
from shepherd_runtime.handlers import (
    HandlerNotFoundError,
    HandlerRegistry,
    SimpleHandlerContext,
    get_default_registry,
    get_handler,
    register_handler,
    reset_default_registry,
)
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Fixtures: Mock Handlers
# =============================================================================


class MockHandler:
    """Mock handler for testing."""

    def __init__(self, effect_cls: type[Effect], result: Any = "handled"):
        self._effect_cls = effect_cls
        self._result = result
        self.handled_effects: list[Effect] = []

    @property
    def effect_type(self) -> type[Effect]:
        return self._effect_cls

    async def handle(
        self,
        effect: Effect,
        context: Any,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        self.handled_effects.append(effect)
        return await resume(self._result)

    def can_handle(self, effect: Effect) -> bool:
        return isinstance(effect, self._effect_cls)


# =============================================================================
# Tests for HandlerRegistry
# =============================================================================


class TestHandlerRegistry:
    """Tests for HandlerRegistry."""

    def test_register_handler(self):
        """register() adds handler to registry."""
        registry = HandlerRegistry()
        handler = MockHandler(Effect)

        registry.register(handler)

        assert Effect in registry
        assert len(registry) == 1

    def test_get_handler_exact_match(self):
        """get() returns handler for exact type match."""
        registry = HandlerRegistry()
        handler = MockHandler(FileCreate)
        registry.register(handler)

        effect = FileCreate(path="/test.py", content="")
        result = registry.get(effect)

        assert result is handler

    def test_get_handler_inherited(self):
        """get() walks MRO for inherited handlers."""
        registry = HandlerRegistry()
        handler = MockHandler(Effect)  # Base type
        registry.register(handler)

        # FileCreate inherits from Effect
        effect = FileCreate(path="/test.py", content="")
        result = registry.get(effect)

        assert result is handler

    def test_get_handler_not_found(self):
        """get() returns None when no handler found."""
        registry = HandlerRegistry()

        effect = Effect(effect_type="test")
        result = registry.get(effect)

        assert result is None

    def test_has_handler(self):
        """has_handler() checks if handler exists."""
        registry = HandlerRegistry()
        handler = MockHandler(FileCreate)
        registry.register(handler)

        assert registry.has_handler(FileCreate(path="/a.py", content=""))
        assert not registry.has_handler(FilePatch(path="/b.py", patch=""))

    @pytest.mark.asyncio
    async def test_handle_calls_handler(self):
        """handle() dispatches to registered handler."""
        registry = HandlerRegistry()
        handler = MockHandler(Effect, result="test_result")
        registry.register(handler)

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test")

            result = await registry.handle(effect, context)

            assert result == "test_result"
            assert effect in handler.handled_effects

    @pytest.mark.asyncio
    async def test_handle_with_custom_resume(self):
        """handle() uses custom resume function."""
        registry = HandlerRegistry()
        handler = MockHandler(Effect, result="original")
        registry.register(handler)

        transformed_results = []

        async def custom_resume(result: Any) -> Any:
            transformed = f"transformed: {result}"
            transformed_results.append(transformed)
            return transformed

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test")

            result = await registry.handle(effect, context, resume=custom_resume)

            assert result == "transformed: original"
            assert transformed_results == ["transformed: original"]

    @pytest.mark.asyncio
    async def test_handle_not_found_raises(self):
        """handle() raises HandlerNotFoundError when no handler."""
        registry = HandlerRegistry()

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test")

            with pytest.raises(HandlerNotFoundError) as exc_info:
                await registry.handle(effect, context)

            assert exc_info.value.effect is effect

    def test_unregister_handler(self):
        """unregister() removes handler."""
        registry = HandlerRegistry()
        handler = MockHandler(Effect)
        registry.register(handler)

        removed = registry.unregister(Effect)

        assert removed is handler
        assert Effect not in registry

    def test_unregister_not_found(self):
        """unregister() returns None if not found."""
        registry = HandlerRegistry()

        removed = registry.unregister(Effect)

        assert removed is None

    def test_registered_types(self):
        """registered_types() returns list of types."""
        registry = HandlerRegistry()
        registry.register(MockHandler(Effect))
        registry.register(MockHandler(FileCreate))

        types = registry.registered_types()

        assert set(types) == {Effect, FileCreate}

    def test_replace_handler_warning(self, caplog):
        """Replacing a handler logs a warning."""
        import logging

        registry = HandlerRegistry()
        handler1 = MockHandler(Effect)
        handler2 = MockHandler(Effect)

        with caplog.at_level(logging.WARNING):
            registry.register(handler1)
            registry.register(handler2)

        assert "Replacing existing handler" in caplog.text


# =============================================================================
# Tests for Global Registry
# =============================================================================


class TestGlobalRegistry:
    """Tests for global registry functions."""

    def setup_method(self):
        """Reset global registry before each test."""
        reset_default_registry()

    def teardown_method(self):
        """Reset global registry after each test."""
        reset_default_registry()

    def test_get_default_registry_creates_once(self):
        """get_default_registry() creates registry on first call."""
        registry1 = get_default_registry()
        registry2 = get_default_registry()

        assert registry1 is registry2

    def test_reset_default_registry(self):
        """reset_default_registry() clears the global registry."""
        registry1 = get_default_registry()
        reset_default_registry()
        registry2 = get_default_registry()

        assert registry1 is not registry2

    def test_register_handler_global(self):
        """register_handler() adds to global registry."""
        handler = MockHandler(Effect)

        register_handler(handler)

        assert Effect in get_default_registry()

    def test_get_handler_global(self):
        """get_handler() looks up in global registry."""
        handler = MockHandler(Effect)
        register_handler(handler)

        effect = Effect(effect_type="test")
        result = get_handler(effect)

        assert result is handler
