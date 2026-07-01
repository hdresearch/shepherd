"""Phase handlers for the lifecycle pipeline - backward compatibility facade.

This module re-exports all phase implementations from their individual modules.
Each phase is complex enough to warrant its own module:

- _phase_base.py: Phase protocol and PhaseBase class
- _phase_configure.py: ConfigurePhase (compose bindings from contexts)
- _phase_prepare.py: PreparePhase (prepare contexts and create sandboxes)
- _phase_execute.py: ExecutePhase (execute via provider)
- _phase_artifact.py: ArtifactPhase (collect artifacts from .artifacts/)
- _phase_extract.py: ExtractPhase (extract effects from contexts)
- _phase_apply.py: ApplyPhase (apply effects to derive new state)
- _phase_cleanup.py: CleanupPhase (cleanup contexts and discard sandboxes)

Usage:
    from shepherd_runtime._lifecycle import ConfigurePhase, PreparePhase

    configure = ConfigurePhase()
    ctx = await configure.execute(initial_ctx)
"""

from __future__ import annotations

from .._phase_cache import CacheCheckPhase, CacheStorePhase
from ._phase_apply import ApplyPhase
from ._phase_artifact import ArtifactPhase

# Re-export Phase protocol and base class
from ._phase_base import Phase, PhaseBase
from ._phase_cleanup import CleanupPhase

# Re-export all phase implementations
from ._phase_configure import ConfigurePhase
from ._phase_execute import ExecutePhase
from ._phase_extract import ExtractPhase
from ._phase_prepare import PreparePhase

__all__ = [
    "ApplyPhase",
    "ArtifactPhase",
    "CacheCheckPhase",
    "CacheStorePhase",
    "CleanupPhase",
    # Phase implementations
    "ConfigurePhase",
    "ExecutePhase",
    "ExtractPhase",
    # Protocol
    "Phase",
    "PhaseBase",
    "PreparePhase",
]
