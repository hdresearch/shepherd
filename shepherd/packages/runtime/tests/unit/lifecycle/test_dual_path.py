"""Tests for dual-path lifecycle execution (Spike 2).

Validates that ExecutionLifecycle supports both LLM (execute) and
programmatic (run_executor) paths through a shared phase pipeline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from shepherd_core.effects import TaskCompleted, TaskFailed, TaskStarted
from shepherd_core.errors import TaskExecutionError
from shepherd_core.types import ExecutionResult, ProviderBinding, ProviderCapabilities
from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.source.syntax import Lit, Return
from shepherd_runtime._lifecycle import Attribution, ConfigurePhase, ExecutePhase, PhaseContext
from shepherd_runtime.kernel import KernelV3CanarySpec, kernel_v3_canary_policy
from shepherd_runtime.lifecycle import ExecutionLifecycle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope() -> MagicMock:
    """Create a mock scope with effect tracking."""
    scope = MagicMock()
    scope.emit = MagicMock()
    scope.update_context = MagicMock()
    scope.mark_binding_lifecycle = MagicMock()
    scope.all_bindings = MagicMock(return_value=[])
    scope.current_device = None
    scope.effects = MagicMock()
    return scope


def _make_provider(provider_id: str = "test-provider") -> MagicMock:
    """Create a mock provider."""
    provider = MagicMock()
    provider.provider_id = provider_id
    provider.capabilities = ProviderCapabilities(provider_type="test")
    provider.validate_binding = MagicMock()
    provider.formatter = MagicMock()
    provider.execute_sdk = AsyncMock(return_value=ExecutionResult(output_text="Test output"))
    return provider


# ---------------------------------------------------------------------------
# ExecutePhase Tests
# ---------------------------------------------------------------------------


class TestExecutePhaseExecutorBranch:
    """Validate ExecutePhase branching on executor vs provider."""

    @pytest.mark.asyncio
    async def test_sync_executor(self) -> None:
        """ExecutePhase calls sync executor and sets sentinel result."""
        called = False

        def my_executor() -> None:
            nonlocal called
            called = True

        ctx = PhaseContext(
            scope=MagicMock(),
            task_name="test",
            executor=my_executor,
            attribution=Attribution(task_name="test", provider_id=None, source="programmatic"),
        )

        phase = ExecutePhase()
        result_ctx = await phase.execute(ctx)

        assert called
        assert result_ctx.result is not None
        assert result_ctx.result.success is True
        assert result_ctx.result.output_text == ""
        assert result_ctx.result.tool_calls == ()
        assert result_ctx.result.metadata == {"task_name": "test"}

    @pytest.mark.asyncio
    async def test_async_executor(self) -> None:
        """ExecutePhase calls async executor and sets sentinel result."""
        called = False

        async def my_executor() -> None:
            nonlocal called
            called = True

        ctx = PhaseContext(
            scope=MagicMock(),
            task_name="test",
            executor=my_executor,
            attribution=Attribution(task_name="test", provider_id=None, source="programmatic"),
        )

        phase = ExecutePhase()
        result_ctx = await phase.execute(ctx)

        assert called
        assert result_ctx.result is not None
        assert result_ctx.result.success is True

    @pytest.mark.asyncio
    async def test_executor_raises(self) -> None:
        """ExecutePhase propagates executor exceptions."""

        def bad_executor() -> None:
            raise ValueError("boom")

        ctx = PhaseContext(
            scope=MagicMock(),
            task_name="test",
            executor=bad_executor,
            attribution=Attribution(task_name="test", provider_id=None, source="programmatic"),
        )

        phase = ExecutePhase()
        with pytest.raises(ValueError, match="boom"):
            await phase.execute(ctx)

    @pytest.mark.asyncio
    async def test_no_executor_no_provider_raises(self) -> None:
        """ExecutePhase raises when neither executor nor provider is set."""
        ctx = PhaseContext(
            scope=MagicMock(),
            task_name="test",
            attribution=Attribution(task_name="test", provider_id=None, source="programmatic"),
        )

        phase = ExecutePhase()
        with pytest.raises(Exception, match="requires either an executor or a provider"):
            await phase.execute(ctx)

    @pytest.mark.asyncio
    async def test_provider_path_unchanged(self) -> None:
        """ExecutePhase still works with provider (LLM path regression)."""
        provider = _make_provider()
        ctx = PhaseContext(
            scope=MagicMock(),
            provider=provider,
            task_name="test",
            prompt="hello",
            attribution=Attribution(task_name="test", provider_id="test-provider", source="llm"),
        )

        phase = ExecutePhase()
        result_ctx = await phase.execute(ctx)

        provider.execute_sdk.assert_called_once()
        assert result_ctx.result is not None
        assert result_ctx.result.output_text == "Test output"

    @pytest.mark.asyncio
    async def test_canary_sentinel_carries_report_metadata(self) -> None:
        """ExecutePhase records compact canary metadata on its sentinel result."""
        called_existing = False

        def my_executor() -> None:
            nonlocal called_existing
            called_existing = True

        target = MagicMock()
        target.result = None
        output_field = MagicMock()
        output_field.inner_type = str
        ctx = PhaseContext(
            scope=MagicMock(),
            task_name="test",
            executor=my_executor,
            attribution=Attribution(task_name="test", provider_id=None, source="programmatic"),
            kernel_v3_canary_target=target,
            kernel_v3_canary_spec=KernelV3CanarySpec(
                program_factory=lambda _task_instance: elaborate(Return(Lit("v3-output"))),
            ),
            task_meta=MagicMock(outputs={"result": output_field}),
        )

        phase = ExecutePhase()
        with kernel_v3_canary_policy("canary"):
            result_ctx = await phase.execute(ctx)

        assert called_existing is False
        assert result_ctx.result is not None
        assert result_ctx.result.metadata["kernel_v3_canary_mode"] == "canary"
        assert result_ctx.result.metadata["kernel_v3_canary_authoritative"] == "v3"
        assert result_ctx.result.metadata["kernel_v3_canary"]["mode"] == "canary"
        assert result_ctx.result.metadata["kernel_v3_canary"]["authoritative"] == "v3"


# ---------------------------------------------------------------------------
# ConfigurePhase Tests
# ---------------------------------------------------------------------------


class TestConfigurePhaseNoProvider:
    """Validate ConfigurePhase works without a provider."""

    @pytest.mark.asyncio
    async def test_configure_no_provider(self) -> None:
        """ConfigurePhase passes capabilities=None when no provider."""
        mock_context = MagicMock()
        mock_context.configure = MagicMock(return_value=ProviderBinding(context_ids=["test:ctx"]))

        mock_binding = MagicMock()
        mock_binding.name = "workspace"
        mock_binding.context = mock_context

        ctx = PhaseContext(
            scope=MagicMock(),
            provider=None,
            task_name="test",
            bindings=(mock_binding,),
            attribution=Attribution(task_name="test", provider_id=None, source="programmatic"),
        )

        phase = ConfigurePhase()
        result_ctx = await phase.execute(ctx)

        # configure should be called with None capabilities
        mock_context.configure.assert_called_once_with(None)
        # validate_binding should NOT be called (no provider)
        assert result_ctx.composed_binding is not None

    @pytest.mark.asyncio
    async def test_configure_with_provider(self) -> None:
        """ConfigurePhase still validates with provider (regression)."""
        provider = _make_provider()
        mock_context = MagicMock()
        mock_context.configure = MagicMock(return_value=ProviderBinding(context_ids=["test:ctx"]))

        mock_binding = MagicMock()
        mock_binding.name = "workspace"
        mock_binding.context = mock_context

        ctx = PhaseContext(
            scope=MagicMock(),
            provider=provider,
            task_name="test",
            bindings=(mock_binding,),
            attribution=Attribution(task_name="test", provider_id="test-provider", source="llm"),
        )

        phase = ConfigurePhase()
        await phase.execute(ctx)

        # configure should be called with provider capabilities
        mock_context.configure.assert_called_once_with(provider.capabilities)
        # validate_binding should be called
        provider.validate_binding.assert_called_once()


# ---------------------------------------------------------------------------
# Full Lifecycle Tests
# ---------------------------------------------------------------------------


class TestRunExecutor:
    """Validate the run_executor() facade method."""

    @pytest.mark.asyncio
    async def test_run_executor_emits_effects(self) -> None:
        """run_executor emits TaskStarted and TaskCompleted with provider_id=None."""
        scope = _make_scope()
        emitted: list[Any] = []
        scope.emit = lambda effect: emitted.append(effect)

        called = False

        def my_executor() -> None:
            nonlocal called
            called = True

        async with ExecutionLifecycle(
            scope=scope,
            provider=None,
            executor=my_executor,
            task_name="TestTask",
        ) as lifecycle:
            await lifecycle.run_executor()

        assert called

        # Find TaskStarted and TaskCompleted in emitted effects
        started = [e for e in emitted if isinstance(e, TaskStarted)]
        completed = [e for e in emitted if isinstance(e, TaskCompleted)]

        assert len(started) == 1
        assert started[0].provider_id is None
        assert started[0].task_name == "TestTask"
        assert "executor" in started[0].inputs

        assert len(completed) == 1
        assert completed[0].provider_id is None

    @pytest.mark.asyncio
    async def test_run_executor_async(self) -> None:
        """run_executor works with async executor."""
        scope = _make_scope()
        emitted: list[Any] = []
        scope.emit = lambda effect: emitted.append(effect)

        called = False

        async def my_executor() -> None:
            nonlocal called
            called = True

        async with ExecutionLifecycle(
            scope=scope,
            provider=None,
            executor=my_executor,
            task_name="TestTask",
        ) as lifecycle:
            await lifecycle.run_executor()

        assert called
        completed = [e for e in emitted if isinstance(e, TaskCompleted)]
        assert len(completed) == 1

    @pytest.mark.asyncio
    async def test_run_executor_failure_emits_task_failed(self) -> None:
        """run_executor emits TaskFailed and raises TaskExecutionError on failure."""
        scope = _make_scope()
        emitted: list[Any] = []
        scope.emit = lambda effect: emitted.append(effect)

        def bad_executor() -> None:
            raise ValueError("executor failed")

        async with ExecutionLifecycle(
            scope=scope,
            provider=None,
            executor=bad_executor,
            task_name="TestTask",
        ) as lifecycle:
            with pytest.raises(TaskExecutionError) as exc_info:
                await lifecycle.run_executor()

        assert "executor failed" in str(exc_info.value)

        failed = [e for e in emitted if isinstance(e, TaskFailed)]
        assert len(failed) == 1
        assert failed[0].provider_id is None
        assert failed[0].task_name == "TestTask"
        assert "executor failed" in failed[0].error

    @pytest.mark.asyncio
    async def test_execute_llm_unchanged(self) -> None:
        """execute(prompt) still works with a provider (regression)."""
        scope = _make_scope()
        provider = _make_provider()
        emitted: list[Any] = []
        scope.emit = lambda effect: emitted.append(effect)

        async with ExecutionLifecycle(
            scope=scope,
            provider=provider,
            task_name="LLMTask",
        ) as lifecycle:
            result = await lifecycle.execute("Hello world")

        assert result.output_text == "Test output"

        started = [e for e in emitted if isinstance(e, TaskStarted)]
        assert len(started) == 1
        assert started[0].provider_id == "test-provider"
        assert "prompt" in started[0].inputs

        completed = [e for e in emitted if isinstance(e, TaskCompleted)]
        assert len(completed) == 1
        assert completed[0].provider_id == "test-provider"
