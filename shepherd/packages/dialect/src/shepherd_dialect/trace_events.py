"""Trace event taxonomy helpers for public runtime-substrate observations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Any

__all__ = [
    "committed_operation_events_from_log",
    "log_entry_operation_id",
    "proposed_operation_events",
    "substrate_operation_events",
]


def substrate_operation_events(operation: Any, *, decision: str | None = None) -> tuple[dict[str, Any], ...]:
    """Return taxonomy events for a public runtime-substrate operation object."""
    effect = getattr(operation, "effect", None)
    effect_event = _effect_event(effect, phase=_operation_phase(operation), decision=decision)
    return (
        {
            "kind": type(operation).__name__,
            "binding": getattr(operation, "binding", None),
            "effect": _effect_summary(effect),
            **({"decision": decision} if decision is not None else {}),
        },
        effect_event,
    )


def proposed_operation_events(records: Iterable[Mapping[str, Any] | Any]) -> tuple[dict[str, Any], ...]:
    """Return ``SubstrateOperationProposed`` + effect-kind events for proposal summaries."""
    events: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record, Mapping):
            effect = _effect_summary_from_record(record)
            decision = record.get("decision")
            operation_id = record.get("operation_id")
            events.append(
                {
                    "kind": "SubstrateOperationProposed",
                    "binding": record.get("binding", "workspace"),
                    "effect": effect,
                    **({"operation_id": operation_id} if isinstance(operation_id, str) else {}),
                    **({"decision": decision} if decision is not None else {}),
                }
            )
            events.append(
                {
                    "kind": effect["kind"],
                    "binding": record.get("binding", "workspace"),
                    "path": effect.get("path"),
                    "phase": "proposed",
                    **({"operation_id": operation_id} if isinstance(operation_id, str) else {}),
                    **({"decision": decision} if decision is not None else {}),
                }
            )
        else:
            events.extend(substrate_operation_events(record))
    return tuple(events)


def committed_operation_events_from_log(
    entries: Iterable[Any],
    *,
    operation_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return committed taxonomy events from public vcs-core log entry metadata."""
    events: list[dict[str, Any]] = []
    for entry in entries:
        metadata = getattr(entry, "metadata", None)
        if not isinstance(metadata, Mapping):
            continue
        entry_operation_id = log_entry_operation_id(entry)
        if operation_id is not None and entry_operation_id != operation_id:
            continue
        effect_kind = metadata.get("type")
        if effect_kind not in {"FileCreate", "FilePatch"}:
            continue
        path = metadata.get("path")
        effect = {"kind": effect_kind, "path": path}
        events.append(
            {
                "kind": "SubstrateOperationCommitted",
                "binding": metadata.get("binding", "workspace"),
                "effect": effect,
                "operation_ref": getattr(entry, "oid", None),
                "operation_id": entry_operation_id,
            }
        )
        events.append(
            {
                "kind": effect_kind,
                "binding": metadata.get("binding", "workspace"),
                "path": path,
                "phase": "committed",
                "operation_id": entry_operation_id,
            }
        )
    return tuple(events)


def log_entry_operation_id(entry: Any) -> str | None:
    """Return the public operation id carried by a vcs-core log entry, if any."""
    metadata = getattr(entry, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    mg = metadata.get("mg")
    if not isinstance(mg, Mapping):
        return None
    operation = mg.get("operation")
    if not isinstance(operation, Mapping):
        return None
    operation_id = operation.get("id")
    return operation_id if isinstance(operation_id, str) else None


def _operation_phase(operation: Any) -> str:
    kind = type(operation).__name__
    if kind == "SubstrateOperationProposed":
        return "proposed"
    if kind == "SubstrateOperationCommitted":
        return "committed"
    return "observed"


def _effect_summary(effect: Any) -> dict[str, Any]:
    path = getattr(effect, "path", None)
    content = getattr(effect, "content", None)
    summary = {"kind": type(effect).__name__, "path": path}
    if isinstance(content, bytes):
        summary["content_sha256"] = sha256(content).hexdigest()
        summary["content_bytes"] = len(content)
    return summary


def _effect_summary_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    effect_kind = record.get("effect_kind", record.get("op"))
    return {"kind": effect_kind, "path": record.get("path")}


def _effect_event(effect: Any, *, phase: str, decision: str | None = None) -> dict[str, Any]:
    event = _effect_summary(effect)
    event["phase"] = phase
    if decision is not None:
        event["decision"] = decision
    return event
