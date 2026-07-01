"""Tests for built-in handlers: PassthroughHandler, LoggingHandler."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime.handlers import (
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


class RecordingHandler:
    """Handler that records calls for testing."""

    def __init__(self, result: Any = "recorded"):
        self._result = result
        self.calls: list[tuple[Effect, Any]] = []

    @property
    def effect_type(self) -> type[Effect]:
        return Effect

    async def handle(
        self,
        effect: Effect,
        context: Any,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        self.calls.append((effect, context))
        return await resume(self._result)

    def can_handle(self, effect: Effect) -> bool:
        return True


class FailingHandler:
    """Handler that raises an exception."""

    def __init__(self, error: Exception):
        self._error = error

    @property
    def effect_type(self) -> type[Effect]:
        return Effect

    async def handle(
        self,
        effect: Effect,
        context: Any,
        resume: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        raise self._error

    def can_handle(self, effect: Effect) -> bool:
        return True


# =============================================================================
# Tests for PassthroughHandler
# =============================================================================


class TestPassthroughHandler:
    """Tests for PassthroughHandler."""

    @pytest.mark.asyncio
    async def test_passthrough_calls_resume_with_none(self):
        """PassthroughHandler calls resume with None by default."""
        handler = PassthroughHandler()
        results = []

        async def resume(result: Any) -> Any:
            results.append(result)
            return result

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test")

            await handler.handle(effect, context, resume)

            assert results == [None]

    @pytest.mark.asyncio
    async def test_passthrough_custom_result(self):
        """PassthroughHandler can return custom result."""
        handler = PassthroughHandler(result="custom_value")
        results = []

        async def resume(result: Any) -> Any:
            results.append(result)
            return result

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test")

            result = await handler.handle(effect, context, resume)

            assert result == "custom_value"
            assert results == ["custom_value"]

    def test_passthrough_effect_type_is_base_effect(self):
        """PassthroughHandler handles all Effect types."""
        handler = PassthroughHandler()
        assert handler.effect_type is Effect

    def test_passthrough_can_handle_any(self):
        """PassthroughHandler can handle any effect."""
        handler = PassthroughHandler()
        assert handler.can_handle(Effect(effect_type="test"))
        assert handler.can_handle(Effect(effect_type="another"))

    def test_passthrough_satisfies_protocol(self):
        """PassthroughHandler satisfies EffectHandler protocol."""
        handler = PassthroughHandler()
        assert isinstance(handler, EffectHandler)


# =============================================================================
# Tests for LoggingHandler
# =============================================================================


class TestLoggingHandler:
    """Tests for LoggingHandler."""

    @pytest.mark.asyncio
    async def test_logging_handler_delegates(self):
        """LoggingHandler delegates to inner handler."""
        inner = RecordingHandler(result="inner_result")
        handler = LoggingHandler(inner)

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test")

            result = await handler.handle(effect, context, identity_resume)

            # Inner handler was called
            assert len(inner.calls) == 1
            assert inner.calls[0][0] is effect

    @pytest.mark.asyncio
    async def test_logging_handler_returns_inner_result(self):
        """LoggingHandler returns result from inner handler."""
        inner = RecordingHandler(result="expected_result")
        handler = LoggingHandler(inner)

        results = []

        async def resume(r: Any) -> Any:
            results.append(r)
            return r

        with Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test")

            result = await handler.handle(effect, context, resume)

            assert result == "expected_result"

    @pytest.mark.asyncio
    async def test_logging_handler_logs_before_and_after(self, caplog):
        """LoggingHandler logs before and after handling."""
        inner = RecordingHandler()
        handler = LoggingHandler(inner, level=logging.INFO)

        with caplog.at_level(logging.INFO), Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="test_effect")

            await handler.handle(effect, context, identity_resume)

        # Check logs contain effect type
        assert "Handling test_effect" in caplog.text
        assert "Handled test_effect" in caplog.text

    @pytest.mark.asyncio
    async def test_logging_handler_logs_errors(self, caplog):
        """LoggingHandler logs errors from inner handler."""
        inner = FailingHandler(ValueError("test error"))
        handler = LoggingHandler(inner)

        with caplog.at_level(logging.ERROR), Scope() as scope:
            context = SimpleHandlerContext(scope)
            effect = Effect(effect_type="failing")

            with pytest.raises(ValueError, match="test error"):
                await handler.handle(effect, context, identity_resume)

        assert "Failed failing" in caplog.text
        assert "test error" in caplog.text

    def test_logging_handler_effect_type_from_inner(self):
        """LoggingHandler effect_type comes from inner handler."""
        inner = RecordingHandler()
        handler = LoggingHandler(inner)

        assert handler.effect_type is inner.effect_type

    def test_logging_handler_can_handle_from_inner(self):
        """LoggingHandler can_handle delegates to inner."""
        inner = RecordingHandler()
        handler = LoggingHandler(inner)

        effect = Effect(effect_type="test")
        assert handler.can_handle(effect) == inner.can_handle(effect)

    def test_logging_handler_custom_logger(self, caplog):
        """LoggingHandler can use custom logger."""
        custom_logger = logging.getLogger("custom.handler")
        inner = RecordingHandler()
        handler = LoggingHandler(inner, logger=custom_logger, level=logging.WARNING)

        # The handler was created with custom logger
        assert handler._logger is custom_logger

    def test_logging_handler_satisfies_protocol(self):
        """LoggingHandler satisfies EffectHandler protocol."""
        inner = RecordingHandler()
        handler = LoggingHandler(inner)
        assert isinstance(handler, EffectHandler)
