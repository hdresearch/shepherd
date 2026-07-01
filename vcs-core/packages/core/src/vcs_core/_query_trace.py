"""Projection from inventory facts into lightweight trace events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcs_core._query_inventory import InventoryItem, InventorySnapshot


@dataclass(frozen=True)
class VcsCoreTraceEvent:
    """Rebuildable trace event derived from canonical inventory facts."""

    id: str
    kind: str
    source_item_id: str
    subject_id: str
    fields: dict[str, object] = field(default_factory=dict)
    issue_codes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source_item_id": self.source_item_id,
            "subject_id": self.subject_id,
            "fields": dict(self.fields),
            "issue_codes": list(self.issue_codes),
        }


def project_inventory_trace(snapshot: InventorySnapshot) -> tuple[VcsCoreTraceEvent, ...]:
    """Project inventory items into non-authoritative trace events."""
    events: list[VcsCoreTraceEvent] = []
    for item in snapshot.items:
        event = _event_for_item(item)
        if event is not None:
            events.append(event)
    return tuple(events)


def _event_for_item(item: InventoryItem) -> VcsCoreTraceEvent | None:
    if item.domain == "operation_journal":
        return _event(
            item,
            kind="journal.entry_recorded",
            subject_id=_subject(item, "operation_id"),
            fields=_fields(item, "family", "operation_id", "status", "seq", "target_ref", "world_oid"),
        )
    if item.domain == "workspace_authority":
        return _event(
            item,
            kind="recovery.blocked",
            subject_id=_subject(item, "operation_id"),
            fields=_fields(item, "operation_id", "scope_ref", "phase", "driver_command", "retry_count"),
        )
    if item.domain == "recovery":
        return _event(
            item,
            kind="recovery.blocked",
            subject_id=_subject(item, "operation_id", "scope_ref", "label", default=item.id),
            fields=dict(item.fields),
        )
    return None


def _event(
    item: InventoryItem,
    *,
    kind: str,
    subject_id: str,
    fields: dict[str, object],
) -> VcsCoreTraceEvent:
    return VcsCoreTraceEvent(
        id=f"trace:{kind}:{item.id}",
        kind=kind,
        source_item_id=item.id,
        subject_id=subject_id,
        fields=fields,
        issue_codes=item.health.issue_codes,
    )


def _subject(item: InventoryItem, *fields: str, default: str | None = None) -> str:
    for field_name in fields:
        value = item.fields.get(field_name)
        if isinstance(value, str) and value:
            return value
    if item.locator:
        return item.locator
    return default or item.id


def _fields(item: InventoryItem, *field_names: str) -> dict[str, object]:
    return {field_name: value for field_name in field_names if (value := item.fields.get(field_name)) is not None}
