"""Boundary guard: product output resolution goes through the semantic TraceStore ABI, not the carrier.

Pre-launch three-layer cut (`docs/engineering/v1-deferred-scope.md`, "Pre-launch trace architecture"):
the vcs-core task-trace *carrier* (layer 1 — `read_trace_revision` / `store_trace`) backs the run-level
`runs.trace()` summary; the semantic execution **TraceStore ABI** (layer 2) owns descriptor/output
resolution. This guard pins that the output-resolution module reaches the ABI and **never** the carrier,
so a future swap of the TraceStore backend (V1D-008) cannot silently route product resolution through
the JSON trace-revision payload.

This is a fast source-text tripwire and can miss carrier use reached *indirectly* through another
helper module, so it is **supplemented** (not replaced) by the behavioral gate
``test_output_resolution_does_not_read_the_trace_carrier_behaviorally`` in
``test_workspace_control_core_loop.py`` — that test makes ``VcsCore.read_trace_revision`` explode and
proves ``runs.outputs()`` still resolves through the TraceStore, catching the indirect carrier use this
guard cannot. Keep this one as the cheap, import-time first line of defense.
"""

from __future__ import annotations

from pathlib import Path

import shepherd_dialect.workspace_control.outputs as outputs_module

_SOURCE = Path(outputs_module.__file__).read_text()


def test_output_resolution_uses_the_tracestore_abi() -> None:
    # Descriptor resolution flows through the store ABI resolver.
    assert "resolve_run_output_descriptor_from_store" in _SOURCE


def test_output_resolution_never_reads_the_trace_carrier() -> None:
    # The carrier reads (layer 1) belong to runs.trace() / queries, not to output resolution.
    assert "read_trace_revision" not in _SOURCE
    assert "store_trace" not in _SOURCE
    assert 'exec("trace"' not in _SOURCE
