from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._mutation_admission import RecoveryBlocker, blocker_subjects
from vcs_core._sibling_groups import BLOCKING_SIBLING_GROUP_STATUSES

if TYPE_CHECKING:
    from vcs_core.vcscore import VcsCore


def refresh_sibling_group_recovery_blockers(owner: VcsCore) -> tuple[RecoveryBlocker, ...]:
    listing = owner._store.list_sibling_groups()
    blockers: list[RecoveryBlocker] = []
    for snapshot in listing.groups:
        record = snapshot.record
        if record.status in BLOCKING_SIBLING_GROUP_STATUSES:
            blockers.append(
                RecoveryBlocker(
                    kind="sibling_group",
                    subject=f"{record.group_id} ({record.status})",
                    group_id=record.group_id,
                    ref=owner._store.sibling_group_ref(record.group_id),
                    status=record.status,
                )
            )
    for unreadable in listing.unreadable:
        blockers.append(
            RecoveryBlocker(
                kind="sibling_group",
                subject=f"{unreadable.group_id} (unreadable)",
                group_id=unreadable.group_id,
                ref=unreadable.ref,
                status="unreadable",
                reason=unreadable.reason,
            )
        )
    blockers_tuple = tuple(blockers)
    owner._sibling_group_blockers = list(blocker_subjects(blockers_tuple))
    return blockers_tuple


def refresh_sibling_group_blockers(owner: VcsCore) -> tuple[str, ...]:
    return blocker_subjects(refresh_sibling_group_recovery_blockers(owner))


def list_sibling_group_blockers(owner: VcsCore) -> tuple[str, ...]:
    with owner._lock:
        return refresh_sibling_group_blockers(owner)
