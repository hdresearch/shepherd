"""Runtime-normalized trace recorder for the callable syntax spine.

This module intentionally builds on the existing ``Trace`` container and
kernel-v3 record dataclasses. It does not introduce a second trace shape.

The records emitted here are runtime-normalized Phase 1 evidence. They are
shaped so the kernel half can be validated by the runtime trace profile, but
they are not a claim that opaque Python was statically lowered through the
kernel-v3 reference evaluator or Lean proof path.
"""

from __future__ import annotations

import re
import time
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Literal

from shepherd_kernel_v3_reference.paths import source_path_ref

from shepherd_runtime.effects.effect_kind import register_effect_class
from shepherd_runtime.trace.container import Trace
from shepherd_runtime.trace.kernel import (
    EffectCapture,
    EffectDeclaration,
    HandlerSelection,
    KernelRecord,
    ResumptionHandle,
)
from shepherd_runtime.trace.surface import SURFACE_REGISTRY, SurfaceBase, SurfaceRecord
from shepherd_runtime.trace.types import Ref, RunRef, SubTag

__all__ = [
    "ArtifactEmitted",
    "DeliveryCompleted",
    "EffectRequested",
    "HandlerReturned",
    "HandlerSelected",
    "ProviderCallCompleted",
    "ProviderCallRequested",
    "RuntimeSurfaceEvent",
    "RuntimeTraceRecorder",
    "RuntimeTraceRecorderError",
    "SubstrateRefused",
    "active_trace_recorder",
    "pop_trace_recorder",
    "push_trace_recorder",
]


_DEFAULT_CLAIM_LEVEL = "phase1-runtime"
_DEFAULT_PROOF_PROFILE = "runtime_only"
_PROGRAM_REF = "program:phase1-runtime"
_BRANCH_REF = "branch:root"
_ANY_SCHEMA_REF = "schema:phase1-runtime:any"
_REDACTED = "<redacted>"
_MAX_TRACE_STRING_LENGTH = 512
_SENSITIVE_KEY_TOKENS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "content",
        "credential",
        "credentials",
        "message",
        "messages",
        "password",
        "prompt",
        "response",
        "secret",
        "token",
        "transcript",
    }
)
_RESERVED_CLAIM_KEYS = frozenset({"claim_level", "proof_profile"})
_RAW_VALUE_KEY_TOKENS = frozenset({"body", "raw", "text", "value"})
_SAFE_STRUCTURAL_SUFFIXES = frozenset(
    {
        "count",
        "counts",
        "kind",
        "len",
        "length",
        "metadata",
        "profile",
        "reason",
        "shape",
        "status",
        "summary",
        "type",
    }
)


class RuntimeTraceRecorderError(RuntimeError):
    """Raised when recorder methods are called out of lifecycle order."""


@dataclass(frozen=True, kw_only=True)
class RuntimeSurfaceEvent(SurfaceBase):
    """Generic Phase 1 surface event.

    ``claim_level`` and ``proof_profile`` keep runtime evidence from being
    mistaken for proof-backed source-lowered trace coverage.
    """

    sub_tag: SubTag = SubTag.control
    sequence: int = 0
    family: str = ""
    phase: str = ""
    kind: str = ""
    status: str = ""
    effect_key: str | None = None
    handler_key: str | None = None
    claim_level: str = _DEFAULT_CLAIM_LEVEL
    proof_profile: str = _DEFAULT_PROOF_PROFILE
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class EffectRequested(RuntimeSurfaceEvent):
    """Surface projection for a requested runtime effect."""

    family: str = "effect"
    phase: str = "requested"
    kind: str = "effect_requested"


@dataclass(frozen=True, kw_only=True)
class HandlerSelected(RuntimeSurfaceEvent):
    """Surface projection for handler selection."""

    family: str = "handler"
    phase: str = "selected"
    kind: str = "handler_selected"


@dataclass(frozen=True, kw_only=True)
class HandlerReturned(RuntimeSurfaceEvent):
    """Surface projection for handler return/resumption."""

    family: str = "handler"
    phase: str = "completed"
    kind: str = "handler_returned"


@dataclass(frozen=True, kw_only=True)
class ProviderCallRequested(RuntimeSurfaceEvent):
    """Surface projection for a requested ``model.call`` operation."""

    family: str = "provider"
    phase: str = "requested"
    kind: str = "provider_call_requested"


@dataclass(frozen=True, kw_only=True)
class ProviderCallCompleted(RuntimeSurfaceEvent):
    """Surface projection for a completed ``model.call`` operation."""

    family: str = "provider"
    phase: str = "completed"
    kind: str = "provider_call_completed"


@dataclass(frozen=True, kw_only=True)
class DeliveryCompleted(RuntimeSurfaceEvent):
    """Surface projection for completed typed delivery."""

    sub_tag: SubTag = SubTag.run
    family: str = "delivery"
    phase: str = "completed"
    kind: str = "delivery_completed"


@dataclass(frozen=True, kw_only=True)
class ArtifactEmitted(RuntimeSurfaceEvent):
    """Surface projection for emitted runtime artifacts."""

    sub_tag: SubTag = SubTag.artifact
    family: str = "artifact"
    phase: str = "emitted"
    kind: str = "artifact_emitted"


@dataclass(frozen=True, kw_only=True)
class SubstrateRefused(RuntimeSurfaceEvent):
    """Surface projection for a substrate-level refusal."""

    sub_tag: SubTag = SubTag.run
    family: str = "substrate"
    phase: str = "refused"
    kind: str = "substrate.refused"
    status: str = "refused"


_SURFACE_EVENT_TYPES_BY_KIND: dict[str, type[RuntimeSurfaceEvent]] = {
    "artifact_emitted": ArtifactEmitted,
    "delivery_completed": DeliveryCompleted,
    "effect_requested": EffectRequested,
    "handler_returned": HandlerReturned,
    "handler_selected": HandlerSelected,
    "provider_call_completed": ProviderCallCompleted,
    "provider_call_requested": ProviderCallRequested,
    "substrate.refused": SubstrateRefused,
}

for _surface_event_kind, _surface_event_type in _SURFACE_EVENT_TYPES_BY_KIND.items():
    register_effect_class(_surface_event_type, explicit_kind=_surface_event_kind, category=None)

for _surface_event_type in (
    RuntimeSurfaceEvent,
    ArtifactEmitted,
    DeliveryCompleted,
    EffectRequested,
    HandlerReturned,
    HandlerSelected,
    ProviderCallCompleted,
    ProviderCallRequested,
    SubstrateRefused,
):
    SURFACE_REGISTRY.setdefault(_surface_event_type.__name__, _surface_event_type)


@dataclass
class _EffectState:
    effect_key: str
    declaration_ref: Ref
    full_continuation_ref: Ref
    captured_continuation_ref: Ref
    outer_continuation_ref: Ref
    captured_control_ref: Ref
    outer_control_ref: Ref
    selected_binding_ref: Ref | None = None
    selection_ref: Ref | None = None
    resumption_ref: Ref | None = None
    selection_path_ref: Ref | None = None
    handler_context_ref: Ref | None = None
    outer_context_ref: Ref | None = None
    completed: bool = False


class RuntimeTraceRecorder:
    """Accumulates Phase 1 runtime evidence and finishes as a ``Trace``."""

    def __init__(
        self,
        run_ref: RunRef,
        *,
        claim_level: str = _DEFAULT_CLAIM_LEVEL,
        proof_profile: str = _DEFAULT_PROOF_PROFILE,
    ) -> None:
        self.run_ref = run_ref
        self.claim_level = claim_level
        self.proof_profile = proof_profile
        self._kernel: list[KernelRecord] = []
        self._surface: list[SurfaceRecord] = []
        self._counter = 0
        self._surface_sequence = 0
        self._states_by_declaration: dict[Ref, _EffectState] = {}
        self._states_by_selection: dict[Ref, _EffectState] = {}
        self._execution_context_ref = f"ctx:phase1-runtime:{run_ref.id}"

    def record_effect_requested(
        self,
        effect_key: str,
        *,
        payload_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record a requested typed or string-keyed effect."""
        return self._record_effect_requested(
            effect_key,
            family="effect",
            kind="effect_requested",
            payload={"payload_summary": payload_summary or {}},
        )

    def record_handler_selected(
        self,
        effect_ref: Ref,
        *,
        handler_key: str,
        status: str = "selected",
    ) -> Ref:
        """Record handler selection and mint the corresponding resumption."""
        state = self._state_for_declaration(effect_ref)
        if state.selection_ref is not None:
            raise RuntimeTraceRecorderError(f"effect {effect_ref!r} already has a selected handler")

        selection_ref = self._next_ref("selection")
        resumption_ref = self._next_ref("resumption")
        handler_context_ref = self._next_ref("ctx-handler")
        selected_binding_ref = f"binding:{handler_key}"

        selection = HandlerSelection(
            ref=selection_ref,
            declaration_ref=effect_ref,
            selected_binding_ref=selected_binding_ref,
            handler_id=handler_key,
            handler_frame_ref=self._next_ref("handler-frame"),
            captured_continuation_ref=state.captured_continuation_ref,
            outer_continuation_ref=state.outer_continuation_ref,
            captured_continuation_control_ref=state.captured_control_ref,
            outer_continuation_control_ref=state.outer_control_ref,
            handled_result_schema_ref=_ANY_SCHEMA_REF,
            worker_context_ref=self._execution_context_ref,
            handler_context_ref=handler_context_ref,
            outer_context_ref=self._execution_context_ref,
        )
        self._kernel.append(selection)

        resumption = ResumptionHandle(
            ref=resumption_ref,
            declaration_ref=effect_ref,
            selection_ref=selection_ref,
            continuation_ref=state.captured_continuation_ref,
            operation_result_schema_ref=None,
            handled_result_schema_ref=_ANY_SCHEMA_REF,
        )
        self._kernel.append(resumption)

        state.selected_binding_ref = selected_binding_ref
        state.selection_ref = selection_ref
        state.resumption_ref = resumption_ref
        state.selection_path_ref = source_path_ref(selection_ref, resumption_ref, _BRANCH_REF)
        state.handler_context_ref = handler_context_ref
        state.outer_context_ref = self._execution_context_ref
        self._states_by_selection[selection_ref] = state

        self._append_surface(
            family="handler",
            phase="selected",
            kind="handler_selected",
            status=status,
            effect_key=state.effect_key,
            handler_key=handler_key,
            citing=(effect_ref, selection_ref, resumption_ref),
            payload={},
        )
        return selection_ref

    def record_effect_completed(
        self,
        selection_ref: Ref,
        *,
        status: str = "returned",
        result_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record completion of a selected effect handler."""
        return self._record_selection_completed(
            selection_ref,
            family="handler",
            kind="handler_returned",
            status=status,
            payload={"result_summary": result_summary or {}},
        )

    def record_effect_default_ignored(
        self,
        effect_ref: Ref,
        *,
        result_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record the runtime's synthetic default-ignore Tell policy."""
        selection_ref = self.record_handler_selected(
            effect_ref,
            handler_key="runtime.default_ignore.v1",
            status="default_ignored",
        )
        return self.record_effect_completed(
            selection_ref,
            status="default_ignored",
            result_summary=result_summary or {"ignored": True},
        )

    def record_provider_call_requested(
        self,
        *,
        request_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record a requested ``model.call`` provider operation."""
        return self._record_effect_requested(
            "model.call",
            family="provider",
            kind="provider_call_requested",
            payload={"request_summary": request_summary or {}},
        )

    def record_provider_call_completed(
        self,
        selection_ref: Ref,
        *,
        status: str = "returned",
        response_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record completion of a selected ``model.call`` operation."""
        return self._record_selection_completed(
            selection_ref,
            family="provider",
            kind="provider_call_completed",
            status=status,
            payload={"response_summary": response_summary or {}},
        )

    def record_delivery_completed(
        self,
        *,
        result_type: str,
        status: str,
        citing: tuple[Ref, ...] = (),
        detail_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record typed delivery completion as a surface event."""
        payload: dict[str, Any] = {"result_type": result_type}
        if detail_summary is not None:
            payload["detail_summary"] = detail_summary
        _reject_reserved_claim_keys(payload)
        return self._append_surface(
            family="delivery",
            phase="completed",
            kind="delivery_completed",
            status=status,
            citing=citing,
            sub_tag=SubTag.run,
            payload=payload,
        )

    def record_artifact_emitted(
        self,
        *,
        artifact_kind: str,
        name: str | None = None,
        citing: tuple[Ref, ...] = (),
        metadata_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record artifact emission as surface evidence only."""
        payload = {
            "artifact_kind": artifact_kind,
            "name": name,
            "metadata_summary": metadata_summary or {},
        }
        _reject_reserved_claim_keys(payload)
        return self._append_surface(
            family="artifact",
            phase="emitted",
            kind="artifact_emitted",
            status="emitted",
            citing=citing,
            sub_tag=SubTag.artifact,
            payload=payload,
        )

    def record_substrate_refused(
        self,
        *,
        source: str,
        reason: str,
        profile: str | None = None,
        driver_id: str | None = None,
        offending: str | None = None,
        operation: str | None = None,
        path: str | None = None,
        detail_summary: dict[str, Any] | None = None,
    ) -> Ref:
        """Record a runtime-only substrate refusal surface event."""
        payload: dict[str, Any] = {
            "source": source,
            "reason": reason,
        }
        if profile is not None:
            payload["profile"] = profile
        if driver_id is not None:
            payload["driver_id"] = driver_id
        if offending is not None:
            payload["offending"] = offending
        if operation is not None:
            payload["operation"] = operation
        if path is not None:
            payload["path"] = path
        if detail_summary is not None:
            payload["detail_summary"] = detail_summary
        _reject_reserved_claim_keys(payload)
        return self._append_surface(
            family="substrate",
            phase="refused",
            kind="substrate.refused",
            status="refused",
            sub_tag=SubTag.run,
            payload=payload,
        )

    def append_kernel(self, record: KernelRecord) -> Ref:
        """Append a prebuilt kernel record for ``TraceWriter`` compatibility."""
        self._kernel.append(record)
        return record.ref

    def append_surface(self, record: SurfaceRecord) -> Ref:
        """Append a prebuilt surface record for ``TraceWriter`` compatibility."""
        self._surface.append(record)
        return record.ref

    def to_trace(self) -> Trace:
        """Return an immutable ``Trace`` snapshot."""
        return Trace(
            run_ref=self.run_ref,
            kernel=tuple(self._kernel),
            surface=tuple(self._surface),
        )

    def _record_effect_requested(
        self,
        effect_key: str,
        *,
        family: str,
        kind: str,
        payload: dict[str, Any],
    ) -> Ref:
        declaration_ref = self._next_ref("declaration")
        ordinal = self._counter
        state = _EffectState(
            effect_key=effect_key,
            declaration_ref=declaration_ref,
            full_continuation_ref=self._next_ref("continuation-full"),
            captured_continuation_ref=self._next_ref("continuation-captured"),
            outer_continuation_ref=self._next_ref("continuation-outer"),
            captured_control_ref=self._next_ref("continuation-control-captured"),
            outer_control_ref=self._next_ref("continuation-control-outer"),
        )
        summary = self._runtime_payload(payload)
        declaration = EffectDeclaration(
            ref=declaration_ref,
            program_ref=_PROGRAM_REF,
            effect_kind=effect_key,
            payload=summary,
            full_continuation_ref=state.full_continuation_ref,
            branch_ref=_BRANCH_REF,
            payload_schema_ref=None,
            operation_result_schema_ref=None,
            execution_context_ref=self._execution_context_ref,
        )
        self._kernel.append(declaration)
        self._states_by_declaration[declaration_ref] = state
        self._append_surface(
            family=family,
            phase="requested",
            kind=kind,
            status="requested",
            effect_key=effect_key,
            citing=(declaration_ref,),
            payload={**summary, "ordinal": ordinal},
        )
        return declaration_ref

    def _record_selection_completed(
        self,
        selection_ref: Ref,
        *,
        family: str,
        kind: str,
        status: str,
        payload: dict[str, Any],
    ) -> Ref:
        state = self._state_for_selection(selection_ref)
        if state.completed:
            raise RuntimeTraceRecorderError(f"selection {selection_ref!r} is already completed")
        if state.selection_path_ref is None:
            raise RuntimeTraceRecorderError(f"selection {selection_ref!r} has no selected path")

        action_kind, disposition = _capture_disposition(status)
        summary = self._runtime_payload(payload)
        capture = EffectCapture(
            ref=self._next_ref("capture"),
            selection_ref=selection_ref,
            selection_path_ref=state.selection_path_ref,
            branch_ref=_BRANCH_REF,
            action_kind=action_kind,
            action_payload=summary,
            continuation_disposition=disposition,
            outer_context_ref=state.outer_context_ref,
        )
        self._kernel.append(capture)
        state.completed = True
        self._append_surface(
            family=family,
            phase="completed",
            kind=kind,
            status=status,
            effect_key=state.effect_key,
            handler_key=self._handler_key_for_state(state),
            citing=(selection_ref, capture.ref),
            payload=summary,
        )
        return capture.ref

    def _append_surface(
        self,
        *,
        family: str,
        phase: str,
        kind: str,
        status: str,
        effect_key: str | None = None,
        handler_key: str | None = None,
        citing: tuple[Ref, ...] = (),
        payload: dict[str, Any] | None = None,
        sub_tag: SubTag = SubTag.control,
    ) -> Ref:
        self._surface_sequence += 1
        ref = self._next_ref("surface")
        event_type = _SURFACE_EVENT_TYPES_BY_KIND.get(kind, RuntimeSurfaceEvent)
        event = event_type(
            ref=ref,
            sub_tag=sub_tag,
            timestamp_us=time.time_ns() // 1_000,
            run_ref=self.run_ref,
            citing=citing,
            sequence=self._surface_sequence,
            family=family,
            phase=phase,
            kind=kind,
            status=status,
            effect_key=effect_key,
            handler_key=handler_key,
            claim_level=self.claim_level,
            proof_profile=self.proof_profile,
            payload=_sanitize_payload(payload or {}),
        )
        self._surface.append(event)
        return ref

    def _runtime_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        _reject_reserved_claim_keys(payload)
        return {
            "claim_level": self.claim_level,
            "proof_profile": self.proof_profile,
            **_sanitize_payload(payload),
        }

    def _state_for_declaration(self, ref: Ref) -> _EffectState:
        try:
            return self._states_by_declaration[ref]
        except KeyError as exc:
            raise RuntimeTraceRecorderError(f"unknown effect declaration ref {ref!r}") from exc

    def _state_for_selection(self, ref: Ref) -> _EffectState:
        try:
            return self._states_by_selection[ref]
        except KeyError as exc:
            raise RuntimeTraceRecorderError(f"unknown handler selection ref {ref!r}") from exc

    def _handler_key_for_state(self, state: _EffectState) -> str | None:
        if state.selected_binding_ref is None:
            return None
        return state.selected_binding_ref.removeprefix("binding:")

    def _next_ref(self, kind: str) -> Ref:
        self._counter += 1
        return f"{kind}:phase1-runtime:{self.run_ref.id}:{self._counter}"


_active_recorders: ContextVar[tuple[RuntimeTraceRecorder, ...]] = ContextVar(
    "shepherd_runtime_trace_recorders",
    default=(),
)


def active_trace_recorder() -> RuntimeTraceRecorder | None:
    """Return the innermost active runtime trace recorder, if any."""
    stack = _active_recorders.get()
    return stack[-1] if stack else None


def push_trace_recorder(
    recorder: RuntimeTraceRecorder,
) -> Token[tuple[RuntimeTraceRecorder, ...]]:
    """Push a recorder for the current dynamic extent."""
    return _active_recorders.set((*_active_recorders.get(), recorder))


def pop_trace_recorder(token: Token[tuple[RuntimeTraceRecorder, ...]]) -> None:
    """Restore the recorder stack to a previous token."""
    _active_recorders.reset(token)


def _capture_disposition(
    status: str,
) -> tuple[Literal["return", "abort"], Literal["completed", "aborted"]]:
    if status in {"abort", "aborted", "cancelled", "error", "failed", "raised", "runtime_failure"}:
        return "abort", "aborted"
    return "return", "completed"


def _sanitize_payload(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_TRACE_STRING_LENGTH:
            return value
        return {
            "type": "str",
            "length": len(value),
            "redacted": True,
        }
    if is_dataclass(value) and not isinstance(value, type):
        return _sanitize_payload(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_payload_key(key_text):
                sanitized[key_text] = _REDACTED
            else:
                sanitized[key_text] = _sanitize_payload(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item) for item in value]
    return {
        "type": type(value).__name__,
        "redacted": True,
    }


def _reject_reserved_claim_keys(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in _RESERVED_CLAIM_KEYS:
                location = ".".join((*path, key_text))
                raise RuntimeTraceRecorderError(f"runtime trace payload cannot set reserved claim key {location!r}")
            _reject_reserved_claim_keys(item, path=(*path, key_text))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_reserved_claim_keys(item, path=(*path, str(index)))


def _is_sensitive_payload_key(key: str) -> bool:
    tokens = _payload_key_tokens(key)
    folded = "".join(tokens)
    token_set = set(tokens)

    if "apikey" in folded or {"api", "key"} <= token_set:
        return True
    if {"access", "key"} <= token_set:
        return True
    if "response" in token_set:
        return bool(token_set & _RAW_VALUE_KEY_TOKENS or "content" in token_set)

    sensitive_tokens = token_set & _SENSITIVE_KEY_TOKENS
    if not sensitive_tokens:
        return False
    return not (tokens[-1] in _SAFE_STRUCTURAL_SUFFIXES and "raw" not in token_set)


def _payload_key_tokens(key: str) -> tuple[str, ...]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return tuple(token for token in re.split(r"[^A-Za-z0-9]+", camel_split.lower()) if token)
