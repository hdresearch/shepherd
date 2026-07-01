"""Runtime-substrate support vocabulary.

This module is the non-experimental import home for the small helper surface
that Shepherd's runtime drivers still share with vcs-core: the typed command
SPI vocabulary, the task-trace driver, and the in-process composition helper
types used by the current dialect path.

It is not the substrate SPI version surface; implement-side contracts continue
to live in :mod:`vcs_core.spi`.
"""

from __future__ import annotations

from vcs_core._runtime_substrate_helpers import (
    ExecutionProvider,
    FileCreate,
    FilePatch,
    HandlerStack,
    InProcessExecutionProvider,
    SubstrateOperationCommitted,
    SubstrateOperationProposed,
    TaskIdResolutionError,
    TraceAppend,
    UnhandledAsk,
    resolve_task_id,
)
from vcs_core._world_substrate_adapters import TaskTraceSubstrateDriver
from vcs_core.spi import (
    BaseSubstrateDriver,
    CapabilitySet,
    CommandRequest,
    CommandSpec,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    ParamSpec,
    SubstrateStoreIdentity,
    TransitionDraft,
)

__all__ = [
    "BaseSubstrateDriver",
    "CapabilitySet",
    "CommandRequest",
    "CommandSpec",
    "DriverContext",
    "DriverIngressResult",
    "DriverSchema",
    "ExecutionProvider",
    "FileCreate",
    "FilePatch",
    "HandlerStack",
    "InProcessExecutionProvider",
    "ParamSpec",
    "SubstrateOperationCommitted",
    "SubstrateOperationProposed",
    "SubstrateStoreIdentity",
    "TaskIdResolutionError",
    "TaskTraceSubstrateDriver",
    "TraceAppend",
    "TransitionDraft",
    "UnhandledAsk",
    "resolve_task_id",
]
