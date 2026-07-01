"""Python-runtime capture adapter — second ``CaptureAdapter`` Protocol implementation.

The adapter parses Python-runtime intercept events (the workspace changes
captured by the patch manager when application code writes through
``open()``, ``os.remove()``, etc.) into typed ``ObservationDraft`` values.

Why this adapter exists: before T2, Python-tier capture produced scalar
``EffectRecord`` values that the runtime dispatched as
``driver_command="scan"`` against the workspace driver — which classified
the writes as ``semantic_op="workspace-scan"`` with ``ingress_kind="command"``.
Both classifications were wrong: the runtime *observed* every raw write
as capture evidence, then reduced that evidence into a workspace candidate
(``ingress_kind="reduce"``), and the user did not *declare* the writes
(``ingress_kind="command"`` is reserved for declared intent). The
push-admission bug at
``vcs-core/design/spikes/260515-world-vectors/260523-python-tier-push-admission/``
was the visible symptom of the mis-classification.

The Python-runtime adapter is the registry-owned half of the SPI v0.1 §Q2
Discovery boundary criterion: it is owned by the patch manager (a
cross-cutting installation component, not a single substrate driver), so
it lives in ``CaptureAdapterRegistry`` rather than on
``WorkspaceSubstrateDriver.capture_adapters``.

Per SPI v0.1 §Q2, adapters are parse-only: they emit ``ObservationDraft``
values to a sink, or decline to parse with ``ParseResult.skip()``. They
never return ``TransitionDraft`` values, persist evidence, call coordinator
entry points, or write durable refs. Evidence persistence is coordinator-
owned (T2c wires this); the typed ``ReduceRequest`` flow over a
``ReductionBatch`` of citations is the driver's reduction surface.

Event shape (consumed by ``parse``):

    {
        "type": "PythonRuntimeEffect",
        "op": "write" | "delete",
        "path": "relative/workspace/path",
        "content_digest": "sha256:..." (writes only),
        "mode": 0o100644 | 0o100755 (writes only),
        "command_operation_id": "op_...",
        "binding_name": "workspace",
        "global_seq": int,
    }

T2c builds these dicts from the existing ``EffectRecord.workspace_changes``
output of the patch manager and routes them through this adapter →
coordinator persistence → ``ReduceRequest`` → ``workspace-capture-reduction``
transition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._substrate_driver import (
    Diagnostic,
    DriverContext,
    ObservationDraft,
    ObservationSink,
    ParseResult,
)
from vcs_core._substrate_evidence_kinds import (
    PYTHON_RUNTIME_EVIDENCE_KINDS,
    EvidenceKind,
    Mechanism,
)
from vcs_core._transition_kernel_records import PayloadDescriptorClaim

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


PYTHON_RUNTIME_ADAPTER_ID = "vcscore.python_runtime_capture"
PYTHON_RUNTIME_ADAPTER_VERSION = "v1"
PYTHON_RUNTIME_MECHANISM = Mechanism.PYTHON_RUNTIME

# Event-type tag the adapter claims; events with other ``type`` values are
# skipped (per the overlay adapter's discipline of filtering on a
# mechanism-specific tag rather than greedily claiming every event).
PYTHON_RUNTIME_EFFECT_TYPE = "PythonRuntimeEffect"

# Operation tokens used in the raw event dict's ``op`` field.
_OP_WRITE = "write"
_OP_DELETE = "delete"

# Map operation tokens to evidence_kind values.
_OP_EVIDENCE_KIND: Mapping[str, str] = {
    _OP_WRITE: EvidenceKind.PYTHON_RUNTIME_WRITE,
    _OP_DELETE: EvidenceKind.PYTHON_RUNTIME_DELETE,
}


class PythonRuntimeCaptureAdapter:
    """``CaptureAdapter`` implementation for patch-manager Python-runtime events.

    Parses raw runtime-intercept event records produced by the patch
    manager when application code writes through the Python standard library
    (``open()`` for write, ``os.remove()``, ``os.rename()``, etc.) into
    typed ``ObservationDraft`` values.

    The adapter is stateless; one instance can be shared across calls.
    Registry-owned per SPI v0.1 §Q2 Discovery boundary criterion: the
    patch manager owns lifetime (registered during ``VcsCore.__init__``
    in T2c), not a single substrate driver. This is the inverse of
    ``OverlayCaptureAdapter``, which is owned by ``WorkspaceSubstrateDriver``
    because the overlay mechanism is intrinsic to the workspace substrate.
    """

    @property
    def adapter_id(self) -> str:
        return PYTHON_RUNTIME_ADAPTER_ID

    @property
    def adapter_version(self) -> str:
        return PYTHON_RUNTIME_ADAPTER_VERSION

    @property
    def mechanism(self) -> str:
        return PYTHON_RUNTIME_MECHANISM

    @property
    def evidence_kinds(self) -> tuple[str, ...]:
        return PYTHON_RUNTIME_EVIDENCE_KINDS

    def parse(
        self,
        context: DriverContext,
        raw_events: Sequence[Mapping[str, object]],
        sink: ObservationSink,
    ) -> ParseResult:
        del context  # Python-runtime capture is context-invariant in v0.1.

        parsed = 0
        diagnostics = 0
        relevant = False

        for index, raw in enumerate(raw_events):
            event_type = raw.get("type")
            if event_type != PYTHON_RUNTIME_EFFECT_TYPE:
                # Not our event — skip silently.
                continue
            relevant = True

            op = raw.get("op")
            if not isinstance(op, str) or op not in _OP_EVIDENCE_KIND:
                sink.diagnostic(
                    Diagnostic(
                        code="python-runtime:unknown_op",
                        message=f"unrecognized op {op!r} in python-runtime event at index {index}",
                        subject=str(raw.get("path", "?")),
                        detail={"index": index, "op": op},
                    )
                )
                diagnostics += 1
                continue

            path = raw.get("path")
            if not isinstance(path, str) or not path:
                sink.diagnostic(
                    Diagnostic(
                        code="python-runtime:malformed_event",
                        message=f"python-runtime event at index {index} missing path",
                        subject="?",
                        detail={"index": index},
                    )
                )
                diagnostics += 1
                continue

            observation = _observation_from_event(raw, op=op, path=path, index=index)
            sink.emit(observation)
            parsed += 1

        if not relevant:
            return ParseResult.skip()
        return ParseResult.complete(parsed=parsed, diagnostics=diagnostics)


def _observation_from_event(
    raw: Mapping[str, object],
    *,
    op: str,
    path: str,
    index: int,
) -> ObservationDraft:
    """Translate one parsed python-runtime event into a typed ``ObservationDraft``."""
    evidence_kind = _OP_EVIDENCE_KIND[op]
    command_operation_id = _optional_str(raw, "command_operation_id")
    global_seq = _optional_int(raw, "global_seq")
    observation_id = _observation_id(
        command_operation_id=command_operation_id,
        global_seq=global_seq,
        path=path,
        op=op,
        index=index,
    )

    stable_observation: dict[str, object] = {
        "op": op,
        "path": path,
    }
    binding_name = _optional_str(raw, "binding_name")
    if binding_name is not None:
        stable_observation["binding_name"] = binding_name
    if command_operation_id is not None:
        stable_observation["command_operation_id"] = command_operation_id
    if global_seq is not None:
        stable_observation["global_seq"] = global_seq

    # Write-specific fields.
    if op == _OP_WRITE:
        content_digest = _optional_str(raw, "content_digest")
        if content_digest is not None:
            stable_observation["content_digest"] = content_digest
        mode = _optional_int(raw, "mode")
        if mode is not None:
            stable_observation["mode"] = mode

    return ObservationDraft(
        observation_id=observation_id,
        evidence_kind=evidence_kind,
        stable_observation=stable_observation,
        mechanism=PYTHON_RUNTIME_MECHANISM,
        correlation_id=command_operation_id,
        evidence_payload_descriptor_claim=PayloadDescriptorClaim.for_json_payload(
            stable_observation,
        ),
    )


def _observation_id(
    *,
    command_operation_id: str | None,
    global_seq: int | None,
    path: str,
    op: str,
    index: int,
) -> str:
    """Deterministic per-event observation id, stable across re-parses.

    Encodes operation id + ordering + path + op + array index so two
    re-parses of the same event batch produce byte-identical observation
    ids. Falls back to ``"unknown"`` for missing operation id / seq so
    malformed-but-recoverable events still produce a stable id.
    """
    op_id = command_operation_id or "unknown"
    seq = global_seq if global_seq is not None else -1
    return f"python-runtime:{op_id}:{seq}:{op}:{path}:{index}"


def _optional_str(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _optional_int(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    if isinstance(value, bool):
        # bool is a subclass of int in Python; guard explicitly.
        return None
    return value if isinstance(value, int) else None


__all__ = [
    "PYTHON_RUNTIME_ADAPTER_ID",
    "PYTHON_RUNTIME_ADAPTER_VERSION",
    "PYTHON_RUNTIME_EFFECT_TYPE",
    "PYTHON_RUNTIME_MECHANISM",
    "PythonRuntimeCaptureAdapter",
]
