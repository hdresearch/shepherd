"""Tests for Phase protocol compliance.

Covers:
- PhaseBase default behavior
- Protocol implementation verification
- Phase name properties
"""

from unittest.mock import MagicMock

import pytest
from shepherd_runtime._lifecycle import (
    ApplyPhase,
    ArtifactPhase,
    CleanupPhase,
    ConfigurePhase,
    ExecutePhase,
    ExtractPhase,
    Phase,
    PhaseBase,
    PhaseContext,
    PreparePhase,
)
from shepherd_runtime.sandbox_registry import SandboxRegistry


class TestPhaseProtocol:
    """Tests for Phase protocol compliance."""

    def test_phase_base_has_default_rollback(self) -> None:
        """PhaseBase should provide default rollback that returns ctx unchanged."""

        class TestPhase(PhaseBase):
            @property
            def name(self) -> str:
                return "test"

            async def execute(self, ctx: PhaseContext) -> PhaseContext:
                return ctx

        phase = TestPhase()
        assert hasattr(phase, "rollback")

    @pytest.mark.asyncio
    async def test_phase_base_rollback_returns_unchanged(self, basic_context: PhaseContext) -> None:
        """PhaseBase.rollback should return context unchanged."""
        phase = PhaseBase()
        error = RuntimeError("test error")

        result = await phase.rollback(basic_context, error)

        assert result is basic_context

    def test_configure_phase_is_protocol_compliant(self) -> None:
        """ConfigurePhase should implement Phase protocol."""
        phase = ConfigurePhase()
        assert isinstance(phase, Phase)
        assert phase.name == "configure"

    def test_all_phases_have_name_property(self, mock_emitter: MagicMock) -> None:
        """All phase implementations should have name property."""
        registry = SandboxRegistry()
        phases = [
            ConfigurePhase(),
            PreparePhase(registry),
            ExecutePhase(),
            ArtifactPhase(mock_emitter),
            ExtractPhase(mock_emitter),
            ApplyPhase(mock_emitter),
            CleanupPhase(mock_emitter),
        ]

        names = [p.name for p in phases]
        assert names == [
            "configure",
            "prepare",
            "execute",
            "artifact",
            "extract",
            "apply",
            "cleanup",
        ]
