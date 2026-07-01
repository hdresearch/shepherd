"""Tests for CompositeHandler and handler composition patterns."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from shepherd_core.effects import Effect, FileCreate, FilePatch
from shepherd_runtime.handlers import (
    CompositeHandler,
    EffectHandler,
    LoggingHandler,
    PassthroughHandler,
    SimpleHandlerContext,
)
from shepherd_runtime.scope import Scope

# =============================================================================
# Helpers
# =============================================================================


async def identity_resume(result: Any) -> Any:
    """Async identity function for resume."""
    return result


# =============================================================================
# Test Fixtures
# =============================================================================


class TypedHandler:
    """Handler for a specific effect type."""

    def __init__(self, effect_cls: type[Effect], result: Any = None):
        self._effect_cls = effect_cls
        self._result = result or f"handled_{effect_cls.__name__}"
        self.handled: list[Effect] = []

    @property
    def effect_type(self) -> type[Effect]:
        return self._effect_cls

    async def handle(
        self,
        effect: Effect,
        context: Any,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        self.handled.append(effect)
        return await resume(self._result)

    def can_handle(self, effect: Effect) -> bool:
        return isinstance(effect, self._effect_cls)


# =============================================================================
# Tests for CompositeHandler
# =============================================================================


class TestCompositeHandler:
    """Tests for CompositeHandler."""

    @pytest.mark.asyncio
    async def test_composite_dispatches_to_correct_handler(self):
        """CompositeHandler routes to handler matching effect type."""
        file_create_handler = TypedHandler(FileCreate)
        file_patch_handler = TypedHandler(FilePatch)

        composite = CompositeHandler(file_create_handler, file_patch_handler)

        with Scope() as scope:
            context = SimpleHandlerContext(scope)

            # FileCreate goes to file_create_handler
            create_effect = FileCreate(path="/a.py", content="")
            await composite.handle(create_effect, context, identity_resume)
            assert create_effect in file_create_handler.handled
            assert create_effect not in file_patch_handler.handled

            # FilePatch goes to file_patch_handler
            patch_effect = FilePatch(path="/b.py", patch="")
            await composite.handle(patch_effect, context, identity_resume)
            assert patch_effect in file_patch_handler.handled

    @pytest.mark.asyncio
    async def test_composite_mro_lookup(self):
        """CompositeHandler walks MRO for inherited handlers."""
        base_handler = TypedHandler(Effect)  # Handles all effects
        composite = CompositeHandler(base_handler)

        with Scope() as scope:
            context = SimpleHandlerContext(scope)

            # FileCreate inherits from Effect
            effect = FileCreate(path="/test.py", content="")
            await composite.handle(effect, context, identity_resume)

            assert effect in base_handler.handled

    @pytest.mark.asyncio
    async def test_composite_no_handler_passthrough(self):
        """CompositeHandler passes through when no handler (non-strict)."""
        composite = CompositeHandler()  # Empty, non-strict

        results = []

        async def resume(r: Any) -> Any:
            results.append(r)
            return r

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="unknown")

            result = await composite.handle(effect, context, resume)

            assert result is None
            assert results == [None]

    @pytest.mark.asyncio
    async def test_composite_strict_raises(self):
        """CompositeHandler raises in strict mode when no handler."""
        composite = CompositeHandler(strict=True)

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="unknown")

            with pytest.raises(ValueError, match="No handler found"):
                await composite.handle(effect, context, identity_resume)

    def test_composite_add_handler(self):
        """add_handler() adds handler to composite."""
        composite = CompositeHandler()
        handler = TypedHandler(FileCreate)

        composite.add_handler(handler)

        assert len(composite) == 1
        assert handler in composite.handlers

    def test_composite_remove_handler(self):
        """remove_handler() removes handler from composite."""
        handler = TypedHandler(FileCreate)
        composite = CompositeHandler(handler)

        removed = composite.remove_handler(FileCreate)

        assert removed is handler
        assert len(composite) == 0

    def test_composite_remove_handler_not_found(self):
        """remove_handler() returns None if not found."""
        composite = CompositeHandler()

        removed = composite.remove_handler(FileCreate)

        assert removed is None

    def test_composite_get_handler(self):
        """get_handler() returns handler for effect."""
        handler = TypedHandler(FileCreate)
        composite = CompositeHandler(handler)

        effect = FileCreate(path="/test.py", content="")
        result = composite.get_handler(effect)

        assert result is handler

    def test_composite_can_handle_non_strict(self):
        """can_handle() returns True in non-strict mode."""
        composite = CompositeHandler()  # Empty, non-strict

        # Can "handle" anything (passthrough)
        assert composite.can_handle(Effect(effect_type="any"))

    def test_composite_can_handle_strict(self):
        """can_handle() checks child handlers in strict mode."""
        handler = TypedHandler(FileCreate)
        composite = CompositeHandler(handler, strict=True)

        assert composite.can_handle(FileCreate(path="/a.py", content=""))
        assert not composite.can_handle(FilePatch(path="/b.py", patch=""))

    def test_composite_handlers_property(self):
        """Handlers property returns list of handlers."""
        h1 = TypedHandler(FileCreate)
        h2 = TypedHandler(FilePatch)
        composite = CompositeHandler(h1, h2)

        handlers = composite.handlers

        assert h1 in handlers
        assert h2 in handlers
        assert len(handlers) == 2

    def test_composite_effect_type_is_base(self):
        """CompositeHandler effect_type is base Effect."""
        composite = CompositeHandler()
        assert composite.effect_type is Effect

    def test_composite_satisfies_protocol(self):
        """CompositeHandler satisfies EffectHandler protocol."""
        composite = CompositeHandler()
        assert isinstance(composite, EffectHandler)


class TestHandlerChaining:
    """Tests for handler chaining patterns."""

    @pytest.mark.asyncio
    async def test_logging_wrapping_composite(self):
        """LoggingHandler can wrap CompositeHandler."""
        inner_handler = TypedHandler(FileCreate)
        composite = CompositeHandler(inner_handler)
        logged = LoggingHandler(composite)

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = FileCreate(path="/test.py", content="")

            result = await logged.handle(effect, context, identity_resume)

            # Inner handler was called
            assert effect in inner_handler.handled
            assert result == "handled_FileCreate"

    @pytest.mark.asyncio
    async def test_nested_composites(self):
        """Composites can be nested via LoggingHandler wrapping."""
        # Create a composite with specific handlers
        file_create_handler = TypedHandler(FileCreate)
        file_patch_handler = TypedHandler(FilePatch)
        inner_composite = CompositeHandler(file_create_handler, file_patch_handler)

        # Wrap composite with logging
        logged_composite = LoggingHandler(inner_composite)

        with Scope() as scope:
            context = SimpleHandlerContext(scope)

            # FileCreate is routed through logging to composite to handler
            effect = FileCreate(path="/test.py", content="")
            await logged_composite.handle(effect, context, identity_resume)

            assert effect in file_create_handler.handled

            # FilePatch also works
            patch_effect = FilePatch(path="/test.py", patch="")
            await logged_composite.handle(patch_effect, context, identity_resume)

            assert patch_effect in file_patch_handler.handled

    @pytest.mark.asyncio
    async def test_passthrough_in_composite(self):
        """PassthroughHandler can be used in composite."""
        passthrough = PassthroughHandler(result="passed_through")
        composite = CompositeHandler(passthrough)

        results = []

        async def resume(r: Any) -> Any:
            results.append(r)
            return r

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="any")

            result = await composite.handle(effect, context, resume)

            assert result == "passed_through"
            assert results == ["passed_through"]
