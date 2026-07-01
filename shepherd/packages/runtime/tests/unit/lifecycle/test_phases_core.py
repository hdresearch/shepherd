"""Tests for core lifecycle phases: Configure, Prepare, Execute.

These phases form the execution path that sets up and runs provider execution:
- ConfigurePhase: Binding composition and validation
- PreparePhase: Context preparation, sandbox creation, rollback
- ExecutePhase: Provider delegation and result validation
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from shepherd_core.errors import ExecutionError
from shepherd_core.types import (
    PreparationError,
    ProviderBinding,
)
from shepherd_runtime._lifecycle import ConfigurePhase, ExecutePhase, PhaseContext, PreparePhase
from shepherd_runtime.sandbox_registry import SandboxRegistry

from .conftest import MockSandbox

# =============================================================================
# Tests: ConfigurePhase
# =============================================================================


class TestConfigurePhase:
    """Tests for ConfigurePhase."""

    @pytest.mark.asyncio
    async def test_configure_composes_bindings(self, basic_context: PhaseContext) -> None:
        """ConfigurePhase should compose bindings from all contexts."""
        phase = ConfigurePhase()

        result = await phase.execute(basic_context)

        assert result.composed_binding is not None
        assert basic_context.composed_binding is None  # Original unchanged

    @pytest.mark.asyncio
    async def test_configure_validates_against_provider(self, basic_context: PhaseContext) -> None:
        """ConfigurePhase should validate composed binding against provider."""
        phase = ConfigurePhase()

        await phase.execute(basic_context)

        basic_context.provider.validate_binding.assert_called_once()

    @pytest.mark.asyncio
    async def test_configure_adds_output_format(
        self, mock_scope: MagicMock, mock_provider: MagicMock, mock_binding: MagicMock
    ) -> None:
        """ConfigurePhase should add output_format to composed binding."""
        output_format = {"type": "object", "properties": {"result": {"type": "string"}}}
        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(mock_binding,),
            output_format=output_format,
        )
        phase = ConfigurePhase()

        result = await phase.execute(ctx)

        assert result.composed_binding.output_format == output_format

    @pytest.mark.asyncio
    async def test_configure_rollback_is_noop(self, basic_context: PhaseContext) -> None:
        """ConfigurePhase rollback should be a no-op (pure phase)."""
        phase = ConfigurePhase()
        error = RuntimeError("test")

        result = await phase.rollback(basic_context, error)

        assert result is basic_context


# =============================================================================
# Tests: PreparePhase
# =============================================================================


class TestPreparePhase:
    """Tests for PreparePhase."""

    @pytest.mark.asyncio
    async def test_prepare_stores_prepared_contexts(self, basic_context: PhaseContext) -> None:
        """PreparePhase should store prepared contexts for rollback."""
        registry = SandboxRegistry()
        phase = PreparePhase(registry)

        result = await phase.execute(basic_context)

        assert "workspace" in result.prepared_contexts
        assert result.prepared_contexts["workspace"]._prepared

    @pytest.mark.asyncio
    async def test_prepare_creates_sandboxes_when_registered(self, basic_context: PhaseContext) -> None:
        """PreparePhase should create sandboxes for registered context types."""
        registry = SandboxRegistry()
        registry.register("MockContext", MockSandbox)
        phase = PreparePhase(registry)

        result = await phase.execute(basic_context)

        assert "workspace" in result.sandboxes
        assert result.sandboxes["workspace"].setup_called

    @pytest.mark.asyncio
    async def test_prepare_updates_scope(self, basic_context: PhaseContext) -> None:
        """PreparePhase should update scope with prepared contexts."""
        registry = SandboxRegistry()
        phase = PreparePhase(registry)

        await phase.execute(basic_context)

        basic_context.scope.update_context.assert_called()
        basic_context.scope.mark_binding_lifecycle.assert_called()

    @pytest.mark.asyncio
    async def test_prepare_rollback_cleans_up_contexts(self, basic_context: PhaseContext) -> None:
        """PreparePhase.rollback should clean up prepared contexts."""
        registry = SandboxRegistry()
        phase = PreparePhase(registry)

        # First prepare
        ctx = await phase.execute(basic_context)
        error = RuntimeError("test error")

        # Then rollback
        result = await phase.rollback(ctx, error)

        assert result.is_cleaned_up("workspace")

    @pytest.mark.asyncio
    async def test_prepare_rollback_discards_sandboxes(self, basic_context: PhaseContext) -> None:
        """PreparePhase.rollback should discard sandboxes."""
        registry = SandboxRegistry()
        registry.register("MockContext", MockSandbox)
        phase = PreparePhase(registry)

        # First prepare
        ctx = await phase.execute(basic_context)
        error = RuntimeError("test error")

        # Then rollback
        result = await phase.rollback(ctx, error)

        assert result.is_sandbox_discarded("workspace")
        assert ctx.sandboxes["workspace"].discard_called

    @pytest.mark.asyncio
    async def test_prepare_raises_preparation_error_on_failure(
        self, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """PreparePhase should raise PreparationError when prepare fails."""
        # Create a context that fails on prepare
        failing_context = MagicMock()
        failing_context.context_id = "failing"
        failing_context.configure = MagicMock(return_value=ProviderBinding())
        failing_context.prepare = MagicMock(side_effect=RuntimeError("Prepare failed"))

        binding = MagicMock()
        binding.name = "failing"
        binding.context = failing_context

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(binding,),
        )

        registry = SandboxRegistry()
        phase = PreparePhase(registry)

        with pytest.raises(PreparationError) as exc_info:
            await phase.execute(ctx)

        assert "failing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_prepare_emits_context_prepared_effect(
        self, basic_context: PhaseContext, mock_emitter: MagicMock
    ) -> None:
        """PreparePhase should emit ContextPrepared effect when emitter provided."""
        registry = SandboxRegistry()
        phase = PreparePhase(registry, emitter=mock_emitter)

        await phase.execute(basic_context)

        # Verify ContextPrepared was emitted
        mock_emitter.emit_context_prepared.assert_called_once()
        call_kwargs = mock_emitter.emit_context_prepared.call_args[1]
        assert call_kwargs["binding_name"] == "workspace"
        assert call_kwargs["task_name"] == "test-task"

    @pytest.mark.asyncio
    async def test_prepare_works_without_emitter(self, basic_context: PhaseContext) -> None:
        """PreparePhase should work when no emitter is provided."""
        registry = SandboxRegistry()
        phase = PreparePhase(registry)  # No emitter

        result = await phase.execute(basic_context)

        # Should complete without error
        assert "workspace" in result.prepared_contexts


# =============================================================================
# Tests: ExecutePhase
# =============================================================================


class TestExecutePhase:
    """Tests for ExecutePhase."""

    @pytest.mark.asyncio
    async def test_execute_calls_provider(self, basic_context: PhaseContext) -> None:
        """ExecutePhase should call provider.execute_sdk()."""
        phase = ExecutePhase()

        await phase.execute(basic_context)

        basic_context.provider.execute_sdk.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_stores_result(self, basic_context: PhaseContext) -> None:
        """ExecutePhase should store result in context."""
        phase = ExecutePhase()

        result = await phase.execute(basic_context)

        assert result.result is not None
        assert result.result.output_text == "Test output"

    @pytest.mark.asyncio
    async def test_execute_raises_on_none_result(self, basic_context: PhaseContext) -> None:
        """ExecutePhase should raise ExecutionError if provider returns None."""
        basic_context.provider.execute_sdk = AsyncMock(return_value=None)
        phase = ExecutePhase()

        with pytest.raises(ExecutionError) as exc_info:
            await phase.execute(basic_context)

        assert "returned None" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_raises_on_wrong_type(self, basic_context: PhaseContext) -> None:
        """ExecutePhase should raise ExecutionError if provider returns wrong type."""
        basic_context.provider.execute_sdk = AsyncMock(return_value="not a result")
        phase = ExecutePhase()

        with pytest.raises(ExecutionError) as exc_info:
            await phase.execute(basic_context)

        assert "instead of ExecutionResult" in str(exc_info.value)
