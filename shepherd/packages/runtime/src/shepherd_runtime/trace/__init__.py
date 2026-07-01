"""Public trace record schema for the 260503 §07 record taxonomy.

Plan: ``docs/design/proposed/260505-plans/02-trace-record-schema/``.

This package exposes:

- shared identity types (``Ref``, ``RunRef``) and the ``SubTag`` enum;
- kernel record re-exports (proof-oriented / normative when produced by the
  reference path);
- surface record base (``SurfaceBase``) and the runtime registry;
- the ``Trace`` container with canonical JSON serde (CONTRACTS E4).

Phase 1 runtime records are runtime-normalized evidence for the callable
syntax spine. They use ``claim_level="phase1-runtime"`` and
``proof_profile="runtime_only"``; they are not proof-backed kernel-v3 coverage.
Concrete surface subclasses register themselves into ``SURFACE_REGISTRY`` at
import time so ``Trace`` can round-trip JSON without an arbitrary dict escape
hatch.
"""

from __future__ import annotations

from shepherd_runtime.trace.container import SCHEMA_VERSION, Trace
from shepherd_runtime.trace.kernel import (
    ContinuationDelay,
    ContinuationPending,
    ContinuationResume,
    EffectCapture,
    EffectDeclaration,
    ForkBranch,
    ForkSummary,
    HandlerForward,
    HandlerSelection,
    KernelRecord,
    ResumeReturn,
    ResumptionHandle,
    SelectionClosed,
    TerminalResumeResult,
)
from shepherd_runtime.trace.runtime import (
    ArtifactEmitted,
    DeliveryCompleted,
    EffectRequested,
    HandlerReturned,
    HandlerSelected,
    ProviderCallCompleted,
    ProviderCallRequested,
    RuntimeSurfaceEvent,
    RuntimeTraceRecorder,
    RuntimeTraceRecorderError,
    SubstrateRefused,
    active_trace_recorder,
    pop_trace_recorder,
    push_trace_recorder,
)
from shepherd_runtime.trace.surface import (
    SURFACE_REGISTRY,
    SurfaceBase,
    SurfaceRecord,
)
from shepherd_runtime.trace.types import Ref, RunRef, SubTag

__all__ = [
    "SCHEMA_VERSION",
    "SURFACE_REGISTRY",
    "ArtifactEmitted",
    "ContinuationDelay",
    "ContinuationPending",
    "ContinuationResume",
    "DeliveryCompleted",
    "EffectCapture",
    "EffectDeclaration",
    "EffectRequested",
    "ForkBranch",
    "ForkSummary",
    "HandlerForward",
    "HandlerReturned",
    "HandlerSelected",
    "HandlerSelection",
    "KernelRecord",
    "ProviderCallCompleted",
    "ProviderCallRequested",
    "Ref",
    "ResumeReturn",
    "ResumptionHandle",
    "RunRef",
    "RuntimeSurfaceEvent",
    "RuntimeTraceRecorder",
    "RuntimeTraceRecorderError",
    "SelectionClosed",
    "SubTag",
    "SubstrateRefused",
    "SurfaceBase",
    "SurfaceRecord",
    "TerminalResumeResult",
    "Trace",
    "active_trace_recorder",
    "pop_trace_recorder",
    "push_trace_recorder",
]
