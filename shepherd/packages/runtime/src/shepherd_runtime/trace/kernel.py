"""Kernel record re-exports.

The proof-bearing kernel record types live in
``shepherd_kernel_v3_reference.trace.records``. This module re-exports them
under the ``shepherd_runtime.trace`` namespace so production code can import
the runtime trace package without crossing into the reference package.

Per Plan 02 (``docs/design/proposed/260505-plans/02-trace-record-schema/``),
this re-export does **not** fork the kernel record types. There is one source
of truth: the kernel-v3-reference dataclasses. The runtime depends on the
kernel-v3-reference package only for record types and serde, not for trace
machine internals.
"""

from __future__ import annotations

from shepherd_kernel_v3_reference.trace.records import (
    ContinuationDelay,
    ContinuationPending,
    ContinuationResume,
    EffectCapture,
    EffectDeclaration,
    ForkBranch,
    ForkSummary,
    HandlerForward,
    HandlerSelection,
    ResumeReturn,
    ResumptionHandle,
    SelectionClosed,
    TerminalResumeResult,
)
from shepherd_kernel_v3_reference.trace.records import (
    TraceRecord as KernelRecord,
)

__all__ = [
    "ContinuationDelay",
    "ContinuationPending",
    "ContinuationResume",
    "EffectCapture",
    "EffectDeclaration",
    "ForkBranch",
    "ForkSummary",
    "HandlerForward",
    "HandlerSelection",
    "KernelRecord",
    "ResumeReturn",
    "ResumptionHandle",
    "SelectionClosed",
    "TerminalResumeResult",
]
