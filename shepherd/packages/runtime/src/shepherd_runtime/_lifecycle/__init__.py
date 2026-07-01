"""Runtime-private lifecycle substrate."""

from __future__ import annotations

from ._cleanup import cleanup_contexts, discard_sandboxes
from ._emitter import EffectEmitter
from ._phase_apply import ApplyPhase
from ._phase_artifact import ArtifactPhase
from ._phase_base import Phase, PhaseBase
from ._phase_cleanup import CleanupPhase
from ._phase_configure import ConfigurePhase
from ._phase_context import Attribution, CleanupError, PhaseContext
from ._phase_execute import ExecutePhase
from ._phase_extract import ExtractPhase
from ._phase_prepare import PreparePhase
from ._pipeline import LifecyclePipeline

__all__ = [
    "ApplyPhase",
    "ArtifactPhase",
    "Attribution",
    "CleanupError",
    "CleanupPhase",
    "ConfigurePhase",
    "EffectEmitter",
    "ExecutePhase",
    "ExtractPhase",
    "LifecyclePipeline",
    "Phase",
    "PhaseBase",
    "PhaseContext",
    "PreparePhase",
    "cleanup_contexts",
    "discard_sandboxes",
]
