"""Private read-only query helpers for v2 world storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcs_core._world_storage_manager import WorldStorageManager


@dataclass(frozen=True)
class WorldSummary:
    """Compact private status summary for one world commit or authority ref."""

    world_oid: str
    snapshot_digest: str
    operation_id: str
    selected_heads: dict[str, str]
    pin_classification: dict[str, tuple[str, ...]]
    issue_codes: tuple[str, ...]
    journal_statuses: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issue_codes


def summarize_world(manager: WorldStorageManager, ref_or_oid: str) -> WorldSummary:
    """Summarize selected heads, pins, fsck issues, and linked operation journals."""
    world = manager.read_world(ref_or_oid)
    report = manager.fsck_world(world.oid)
    linked_journals = tuple(
        f"{summary.family}:{summary.status}"
        for summary in manager.list_operation_journals()
        if summary.world_oid == world.oid
    )
    operation_id = world.transition.get("operation_id")
    if not isinstance(operation_id, str):
        operation_id = ""
    return WorldSummary(
        world_oid=world.oid,
        snapshot_digest=world.snapshot.digest(),
        operation_id=operation_id,
        selected_heads={head.binding: head.head for head in world.snapshot.heads},
        pin_classification=report.pin_classification,
        issue_codes=tuple(issue.code for issue in report.issue_details),
        journal_statuses=linked_journals,
    )
