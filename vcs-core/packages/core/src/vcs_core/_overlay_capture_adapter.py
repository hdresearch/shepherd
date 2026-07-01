"""Overlay capture adapter — first ``CaptureAdapter`` Protocol implementation.

The adapter parses raw filesystem capture event records (the dicts that
already live on ``CaptureEvent`` commits in the operation history) into
typed ``ObservationDraft`` values and emits them via the caller-supplied
``ObservationSink``.

The parsing logic mirrors the existing free functions in
``_capture_reducer.py`` (``capture_event_from_metadata``,
``ordered_capture_events``) so that consumers can migrate from the
free-function pipeline to the adapter contract without semantic drift.
Once production callers (``vcscore.py::_reduce_capture_for_command_operation``)
migrate to the adapter in T2/T3, the free functions can be deleted per
the EXECPLAN T5 cleanup.

Per SPI v0.1 §Q2, adapters are parse-only: they can never return
``TransitionDraft`` values, persist evidence, call coordinator entry
points, or write durable refs. They can emit ``ObservationDraft`` and
``Diagnostic`` values to the sink, or decline to parse with
``ParseResult.skip()``.

Citation-resolution threading (called out for review): when production
control flow rewires through this adapter, the coordinator persists the
emitted observations as ``EvidenceRecord`` values first, then builds a
``ReductionBatch`` from the persisted refs, and the workspace driver
reduces over citations (not raw observations). That control-flow change
lands in T2 (Python-tier wiring) and T3 (overlay-merge rewire); T1b
ships the adapter type with semantically-equivalent parsing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from vcs_core._capture_reducer import (
    CAPTURE_EVENT_EFFECT,
    CaptureJournalEvent,
    capture_event_from_metadata,
)
from vcs_core._substrate_driver import (
    Diagnostic,
    DriverContext,
    ObservationDraft,
    ObservationSink,
    ParseResult,
)
from vcs_core._substrate_evidence_kinds import (
    OVERLAY_EVIDENCE_KINDS,
    EvidenceKind,
    Mechanism,
)

OVERLAY_ADAPTER_ID = "vcscore.overlay_capture"
OVERLAY_ADAPTER_VERSION = "v1"
OVERLAY_MECHANISM = Mechanism.OVERLAY


# Evidence-kind vocabulary (Q4 / §Q2). Re-exported from
# ``_substrate_evidence_kinds`` for backwards compatibility with existing
# importers; new code should reference ``EvidenceKind.OVERLAY_*`` directly.
OVERLAY_EVIDENCE_KIND_FS_EVENT_BUNDLE = EvidenceKind.OVERLAY_FS_EVENT_BUNDLE
OVERLAY_EVIDENCE_KIND_WRITE_CLOSE = EvidenceKind.OVERLAY_WRITE_CLOSE
OVERLAY_EVIDENCE_KIND_WRITE_OPEN = EvidenceKind.OVERLAY_WRITE_OPEN
OVERLAY_EVIDENCE_KIND_WRITE_OBSERVED = EvidenceKind.OVERLAY_WRITE_OBSERVED
OVERLAY_EVIDENCE_KIND_METADATA_CHANGE = EvidenceKind.OVERLAY_METADATA_CHANGE
OVERLAY_EVIDENCE_KIND_UNLINK = EvidenceKind.OVERLAY_UNLINK


# Map FsCaptureOp values to evidence_kind for fine-grained observations.
_OP_EVIDENCE_KIND: Mapping[str, str] = {
    "write_close": OVERLAY_EVIDENCE_KIND_WRITE_CLOSE,
    "write_open": OVERLAY_EVIDENCE_KIND_WRITE_OPEN,
    "write_observed": OVERLAY_EVIDENCE_KIND_WRITE_OBSERVED,
    "metadata_change": OVERLAY_EVIDENCE_KIND_METADATA_CHANGE,
    "unlink": OVERLAY_EVIDENCE_KIND_UNLINK,
}


class OverlayCaptureAdapter:
    """``CaptureAdapter`` implementation for overlay-captured filesystem events.

    Parses raw capture-event metadata (the ``CaptureEvent``-typed commit
    metadata produced by overlay-mediated capture) into one
    ``ObservationDraft`` per recognized event. Unrecognized event types
    are silently skipped — the adapter only claims events whose
    ``type`` is ``"CaptureEvent"`` and whose ``capture_record`` is
    ``"raw_event"``.

    The adapter is stateless; one instance can be shared across calls.
    The patch manager owns adapter lifetime; ``SubstrateDriver.capture_adapters``
    returns this adapter as a driver default for workspace ingress
    (wired in T1c).
    """

    @property
    def adapter_id(self) -> str:
        return OVERLAY_ADAPTER_ID

    @property
    def adapter_version(self) -> str:
        return OVERLAY_ADAPTER_VERSION

    @property
    def mechanism(self) -> str:
        return OVERLAY_MECHANISM

    @property
    def evidence_kinds(self) -> tuple[str, ...]:
        return OVERLAY_EVIDENCE_KINDS

    def parse(
        self,
        context: DriverContext,
        raw_events: Sequence[Mapping[str, object]],
        sink: ObservationSink,
    ) -> ParseResult:
        del context  # Overlay capture is context-invariant in v0.1.

        parsed = 0
        diagnostics = 0
        relevant = False

        for index, raw in enumerate(raw_events):
            event_type = raw.get("type") if isinstance(raw, Mapping) else None
            if event_type != CAPTURE_EVENT_EFFECT:
                # Not our event — skip silently.
                continue
            relevant = True
            try:
                event = capture_event_from_metadata(dict(raw))
            except ValueError as exc:
                sink.diagnostic(
                    Diagnostic(
                        code="overlay:parse_error",
                        message=f"failed to parse overlay capture event at index {index}",
                        subject=str(raw.get("path", "?")) if isinstance(raw, Mapping) else "?",
                        detail={"error": str(exc), "index": index},
                    )
                )
                diagnostics += 1
                continue
            if event is None:
                # Recognized as a capture-event commit but the op kind or
                # subtype was outside the overlay vocabulary.
                continue
            observation = _observation_from_event(event, index=index)
            sink.emit(observation)
            parsed += 1

        if not relevant:
            return ParseResult.skip()
        return ParseResult.complete(parsed=parsed, diagnostics=diagnostics)


def _observation_from_event(
    event: CaptureJournalEvent,
    *,
    index: int,
) -> ObservationDraft:
    """Translate one parsed capture event into a typed ``ObservationDraft``."""
    evidence_kind = _OP_EVIDENCE_KIND.get(event.op, OVERLAY_EVIDENCE_KIND_FS_EVENT_BUNDLE)
    observation_id = _observation_id(event, index)
    stable_observation: dict[str, object] = {
        "op": event.op,
        "path": event.path,
        "scope": event.scope,
        "scope_instance_id": event.scope_instance_id,
        "pid": event.pid,
        "proc_seq": event.proc_seq,
        "global_seq": event.global_seq,
        "event_seq": event.event_seq,
        "command_operation_id": event.command_operation_id,
        "binding_name": event.binding_name,
        "capture_mechanism": event.capture_mechanism,
    }
    if event.capture_epoch is not None:
        stable_observation["capture_epoch"] = event.capture_epoch
    if event.ppid is not None:
        stable_observation["ppid"] = event.ppid
    if event.exe is not None:
        stable_observation["exe"] = event.exe
    if event.cwd is not None:
        stable_observation["cwd"] = event.cwd
    return ObservationDraft(
        observation_id=observation_id,
        evidence_kind=evidence_kind,
        stable_observation=stable_observation,
        mechanism=OVERLAY_MECHANISM,
        correlation_id=event.command_operation_id,
    )


def _observation_id(event: CaptureJournalEvent, index: int) -> str:
    """Deterministic per-event observation id, stable across re-parses."""
    return (
        f"overlay:{event.command_operation_id}:{event.global_seq}"
        f":{event.pid}:{event.proc_seq}:{event.event_seq}:{index}"
    )


__all__ = [
    "OVERLAY_ADAPTER_ID",
    "OVERLAY_ADAPTER_VERSION",
    "OVERLAY_EVIDENCE_KINDS",
    "OVERLAY_EVIDENCE_KIND_FS_EVENT_BUNDLE",
    "OVERLAY_EVIDENCE_KIND_METADATA_CHANGE",
    "OVERLAY_EVIDENCE_KIND_UNLINK",
    "OVERLAY_EVIDENCE_KIND_WRITE_CLOSE",
    "OVERLAY_EVIDENCE_KIND_WRITE_OBSERVED",
    "OVERLAY_EVIDENCE_KIND_WRITE_OPEN",
    "OVERLAY_MECHANISM",
    "OverlayCaptureAdapter",
]
