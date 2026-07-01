"""Tests for capture lifecycle phases: Artifact, Extract, Apply, Cleanup.

These phases handle the post-execution flow:
- ArtifactPhase: Artifact collection and validation
- ExtractPhase: Effect extraction and attribution
- ApplyPhase: Effect application and scope updates
- CleanupPhase: Idempotent cleanup with error tracking
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from shepherd_core.effects import ContextCaptured, ContextCleanedUp
from shepherd_core.errors import ArtifactNotFoundError
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
)
from shepherd_runtime._lifecycle import ApplyPhase, ArtifactPhase, CleanupPhase, ExtractPhase, PhaseContext

from .conftest import MockSandbox

# =============================================================================
# Tests: ArtifactPhase
# =============================================================================


class TestArtifactPhase:
    """Tests for ArtifactPhase."""

    @pytest.mark.asyncio
    async def test_artifact_returns_empty_when_no_markers(
        self, basic_context: PhaseContext, mock_emitter: MagicMock
    ) -> None:
        """ArtifactPhase should return empty when no artifact_markers."""
        phase = ArtifactPhase(mock_emitter)

        result = await phase.execute(basic_context)

        assert result.artifact_outputs == {}
        assert result.artifact_effects == ()

    @pytest.mark.asyncio
    async def test_artifact_collects_existing_files(
        self, mock_scope: MagicMock, mock_provider: MagicMock, mock_emitter: MagicMock
    ) -> None:
        """ArtifactPhase should collect existing artifact files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create artifacts directory and file
            artifacts_dir = Path(tmpdir) / ".artifacts"
            artifacts_dir.mkdir()
            (artifacts_dir / "report.md").write_text("# Report\nContent here")

            # Create marker
            marker = MagicMock()
            marker.filename = "report.md"
            marker.required = True

            # Create context with cwd
            binding = ProviderBinding(cwd=tmpdir)
            ctx = PhaseContext(
                scope=mock_scope,
                provider=mock_provider,
                task_name="test",
                artifact_markers={"report": marker},
                composed_binding=binding,
            )

            phase = ArtifactPhase(mock_emitter)
            result = await phase.execute(ctx)

            assert "report" in result.artifact_outputs
            assert "# Report" in result.artifact_outputs["report"]
            mock_emitter.emit.assert_called()

    @pytest.mark.asyncio
    async def test_artifact_raises_for_missing_required(
        self, mock_scope: MagicMock, mock_provider: MagicMock, mock_emitter: MagicMock
    ) -> None:
        """ArtifactPhase should raise ArtifactNotFoundError for missing required."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty artifacts directory
            artifacts_dir = Path(tmpdir) / ".artifacts"
            artifacts_dir.mkdir()

            marker = MagicMock()
            marker.filename = "missing.md"
            marker.required = True

            binding = ProviderBinding(cwd=tmpdir)
            ctx = PhaseContext(
                scope=mock_scope,
                provider=mock_provider,
                task_name="test",
                artifact_markers={"report": marker},
                composed_binding=binding,
            )

            phase = ArtifactPhase(mock_emitter)

            with pytest.raises(ArtifactNotFoundError):
                await phase.execute(ctx)

    @pytest.mark.asyncio
    async def test_artifact_skips_missing_optional(
        self, mock_scope: MagicMock, mock_provider: MagicMock, mock_emitter: MagicMock
    ) -> None:
        """ArtifactPhase should skip missing optional artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts_dir = Path(tmpdir) / ".artifacts"
            artifacts_dir.mkdir()

            marker = MagicMock()
            marker.filename = "optional.md"
            marker.required = False

            binding = ProviderBinding(cwd=tmpdir)
            ctx = PhaseContext(
                scope=mock_scope,
                provider=mock_provider,
                task_name="test",
                artifact_markers={"optional": marker},
                composed_binding=binding,
            )

            phase = ArtifactPhase(mock_emitter)
            result = await phase.execute(ctx)

            # Should not raise, but artifact not in outputs
            assert "optional" not in result.artifact_outputs


# =============================================================================
# Tests: ExtractPhase
# =============================================================================


class TestExtractPhase:
    """Tests for ExtractPhase."""

    @pytest.mark.asyncio
    async def test_extract_calls_extract_effects_on_contexts(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """ExtractPhase should call extract_effects on each prepared context."""
        mock_effect = MagicMock()
        mock_effect.with_attribution = MagicMock(return_value=mock_effect)

        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.extract_effects = MagicMock(return_value=[mock_effect])

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            prepared_contexts={"workspace": mock_context},
            result=ExecutionResult(output_text="output"),
        )

        phase = ExtractPhase(mock_emitter)
        result = await phase.execute(ctx)

        mock_context.extract_effects.assert_called_once()
        assert len(result.extracted_effects) == 1

    @pytest.mark.asyncio
    async def test_extract_attributes_effects(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """ExtractPhase should attribute effects with task/provider/context info."""
        mock_effect = MagicMock()
        attributed_effect = MagicMock()
        mock_effect.with_attribution = MagicMock(return_value=attributed_effect)

        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.extract_effects = MagicMock(return_value=[mock_effect])

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test-task",
            prepared_contexts={"workspace": mock_context},
            result=ExecutionResult(output_text="output"),
        )

        phase = ExtractPhase(mock_emitter)
        await phase.execute(ctx)

        mock_effect.with_attribution.assert_called_once_with(
            task_name="test-task",
            provider_id="test-provider",
            context_id="ctx-1",
            binding_name="workspace",
        )

    @pytest.mark.asyncio
    async def test_extract_groups_effects_by_context(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """ExtractPhase should group effects by binding name."""
        effect1 = MagicMock()
        effect1.with_attribution = MagicMock(return_value=effect1)
        effect2 = MagicMock()
        effect2.with_attribution = MagicMock(return_value=effect2)

        context1 = MagicMock()
        context1.context_id = "ctx-1"
        context1.extract_effects = MagicMock(return_value=[effect1])

        context2 = MagicMock()
        context2.context_id = "ctx-2"
        context2.extract_effects = MagicMock(return_value=[effect2])

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            prepared_contexts={"workspace": context1, "session": context2},
            result=ExecutionResult(output_text="output"),
        )

        phase = ExtractPhase(mock_emitter)
        result = await phase.execute(ctx)

        assert "workspace" in result.context_effects
        assert "session" in result.context_effects
        assert len(result.context_effects["workspace"]) == 1
        assert len(result.context_effects["session"]) == 1


# =============================================================================
# Tests: ApplyPhase
# =============================================================================


class TestApplyPhase:
    """Tests for ApplyPhase."""

    @pytest.mark.asyncio
    async def test_apply_calls_apply_effect_on_contexts(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """ApplyPhase should call apply_effect for each context's effects."""
        mock_effect = MagicMock()
        new_context = MagicMock()
        new_context.context_id = "ctx-new"

        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.apply_effect = MagicMock(return_value=new_context)

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            prepared_contexts={"workspace": mock_context},
            context_effects={"workspace": (mock_effect,)},
        )

        phase = ApplyPhase(mock_emitter)
        result = await phase.execute(ctx)

        mock_context.apply_effect.assert_called_once_with(mock_effect)
        assert result.context_outputs["workspace"] is new_context

    @pytest.mark.asyncio
    async def test_apply_emits_context_captured(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """ApplyPhase should emit ContextCaptured effect."""
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.apply_effect = MagicMock(return_value=mock_context)

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            prepared_contexts={"workspace": mock_context},
            context_effects={"workspace": ()},
        )

        phase = ApplyPhase(mock_emitter)
        await phase.execute(ctx)

        # Check that ContextCaptured was emitted
        calls = mock_emitter.emit.call_args_list
        captured_calls = [c for c in calls if isinstance(c[0][0], ContextCaptured)]
        assert len(captured_calls) == 1

    @pytest.mark.asyncio
    async def test_apply_updates_scope_when_auto_update(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """ApplyPhase should update scope when auto_update=True."""
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.apply_effect = MagicMock(return_value=mock_context)

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            prepared_contexts={"workspace": mock_context},
            context_effects={"workspace": ()},
        )

        phase = ApplyPhase(mock_emitter, auto_update=True)
        await phase.execute(ctx)

        mock_scope.update_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_skips_scope_update_when_disabled(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """ApplyPhase should not update scope when auto_update=False."""
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.apply_effect = MagicMock(return_value=mock_context)

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            prepared_contexts={"workspace": mock_context},
            context_effects={"workspace": ()},
        )

        phase = ApplyPhase(mock_emitter, auto_update=False)
        await phase.execute(ctx)

        mock_scope.update_context.assert_not_called()


# =============================================================================
# Tests: CleanupPhase
# =============================================================================


class TestCleanupPhase:
    """Tests for CleanupPhase."""

    @pytest.mark.asyncio
    async def test_cleanup_discards_sandboxes(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """CleanupPhase should discard all sandboxes."""
        sandbox = MockSandbox(None)
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.cleanup = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(),
            prepared_contexts={"workspace": mock_context},
            sandboxes={"workspace": sandbox},
        )

        phase = CleanupPhase(mock_emitter)
        result = await phase.execute(ctx)

        assert sandbox.discard_called
        assert result.is_sandbox_discarded("workspace")

    @pytest.mark.asyncio
    async def test_cleanup_calls_context_cleanup(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """CleanupPhase should call cleanup on all contexts."""
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.cleanup = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(),
            prepared_contexts={"workspace": mock_context},
        )

        phase = CleanupPhase(mock_emitter)
        result = await phase.execute(ctx)

        mock_context.cleanup.assert_called_once()
        assert result.is_cleaned_up("workspace")

    @pytest.mark.asyncio
    async def test_cleanup_skips_already_cleaned(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """CleanupPhase should skip already cleaned contexts."""
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.cleanup = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(),
            prepared_contexts={"workspace": mock_context},
            cleaned_up_contexts=frozenset({"workspace"}),  # Already cleaned
        )

        phase = CleanupPhase(mock_emitter)
        await phase.execute(ctx)

        mock_context.cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_emits_context_cleaned_up(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """CleanupPhase should emit ContextCleanedUp effect."""
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.cleanup = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(),
            prepared_contexts={"workspace": mock_context},
        )

        phase = CleanupPhase(mock_emitter)
        await phase.execute(ctx)

        # Check ContextCleanedUp was emitted
        calls = mock_emitter.emit.call_args_list
        cleanup_calls = [c for c in calls if isinstance(c[0][0], ContextCleanedUp)]
        assert len(cleanup_calls) == 1
        assert cleanup_calls[0][0][0].already_cleaned is False

    @pytest.mark.asyncio
    async def test_cleanup_records_errors_without_raising(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """CleanupPhase should record errors without raising."""
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.cleanup = MagicMock(side_effect=RuntimeError("Cleanup failed"))

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(),
            prepared_contexts={"workspace": mock_context},
        )

        phase = CleanupPhase(mock_emitter)
        result = await phase.execute(ctx)  # Should not raise

        assert result.has_cleanup_errors
        assert len(result.cleanup_errors) == 1
        assert "context:workspace" in result.cleanup_errors[0].resource_name

    @pytest.mark.asyncio
    async def test_cleanup_passes_error_to_contexts(
        self, mock_emitter: MagicMock, mock_scope: MagicMock, mock_provider: MagicMock
    ) -> None:
        """CleanupPhase should pass error to context.cleanup() when set."""
        error = RuntimeError("Pipeline failed")
        mock_context = MagicMock()
        mock_context.context_id = "ctx-1"
        mock_context.cleanup = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=mock_provider,
            task_name="test",
            bindings=(),
            prepared_contexts={"workspace": mock_context},
            error=error,
        )

        phase = CleanupPhase(mock_emitter)
        await phase.execute(ctx)

        mock_context.cleanup.assert_called_once_with(error)

    @pytest.mark.asyncio
    async def test_cleanup_rollback_is_noop(self, mock_emitter: MagicMock, basic_context: PhaseContext) -> None:
        """CleanupPhase rollback should be a no-op."""
        phase = CleanupPhase(mock_emitter)
        error = RuntimeError("test")

        result = await phase.rollback(basic_context, error)

        assert result is basic_context
