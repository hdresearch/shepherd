"""The active-publication-lease index — the first ``DeltaIndex`` customer.

Publication leases protect in-flight worlds from GC. Finding the (few) active leases
today requires scanning the entire repo ref namespace
(``_world_storage_manager._active_publication_lease_refs``, O(total refs)). This
index maintains the active set as a single durable record so the hot read is
O(active), one blob.

Authority remains the lease refs themselves; this index is a fail-closed, rebuildable
accelerator whose ``rebuild_source`` IS the existing full scan. The lease set is small,
so the single-compacted-segment ``DeltaIndex`` is appropriate (a real LSM segment chain
is the retention customer's concern). See ``260621-1730-incremental-frontier-primitive.md``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._incremental._contract import DerivedViewContract, Health
from vcs_core._incremental._delta_index import TOMBSTONE, IndexSegment, SingleSegmentDeltaIndex
from vcs_core._world_refs import world_publication_lease_index_ref

if TYPE_CHECKING:
    import pygit2

ACTIVE_LEASE_INDEX_SCHEMA = "vcscore/active-lease-index/v1"
_META_NAME = "active-lease-index.json"

# entries shape: lease_ref -> {"world_oid": ..., "operation_id": ...}
LeaseEntries = Mapping[str, Mapping[str, str]]


class ActiveLeaseIndex:
    """Durable accelerator for the set of active publication leases of one world store."""

    # The only consumer is a GC *protection* set (``_protected_retention``), which is
    # conservative under over-reporting — protecting a stale ref is safe, dropping a live
    # one is not. So the index is ordered to LEAD the authority on add (before the ref is
    # created) and TRAIL it on release (after the ref is deleted): a crash in either window
    # leaves a superset, never a subset. Declared here so the ordering in
    # ``_world_storage_manager._write_publication_leases`` / ``_release_publication_leases``
    # is a checked contract, not a comment — the conformance tests assert it holds.
    CONTRACT = DerivedViewContract(
        read_safety="superset",
        crash_lag="index-leads",
        detail="leads on add (before ref create), trails on release (after ref delete)",
    )

    def __init__(
        self,
        repo: pygit2.Repository,
        world_store_id: str,
        *,
        rebuild_source: Callable[[], LeaseEntries],
    ) -> None:
        self._world_store_id = world_store_id
        self._index = SingleSegmentDeltaIndex(
            repo,
            world_publication_lease_index_ref(world_store_id),
            schema=ACTIVE_LEASE_INDEX_SCHEMA,
            meta_name=_META_NAME,
            message_prefix=f"active lease index {world_store_id}",
            rebuild_source=lambda: {ref: dict(entry) for ref, entry in rebuild_source().items()},
        )

    def add(self, lease_ref: str, *, world_oid: str, operation_id: str) -> None:
        self._index.extend({lease_ref: {"world_oid": world_oid, "operation_id": operation_id}})

    def remove(self, lease_ref: str) -> None:
        self._index.extend({lease_ref: TOMBSTONE})

    def read_world_oids(self) -> frozenset[str] | None:
        """Leased world oids, or ``None`` when the index record is missing.

        ``None`` signals the caller to fall back to the authoritative full scan.
        Raises ``InvalidRepositoryStateError`` when the record is present but corrupt
        (fail closed — never silently fall back on corruption).
        """
        segment = self._index.read()
        if segment is None:
            return None
        world_oids: set[str] = set()
        for lease_ref, entry in segment.entries.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("world_oid"), str):
                raise InvalidRepositoryStateError(
                    f"active-lease index entry {lease_ref!r} is missing a string world_oid"
                )
            world_oids.add(entry["world_oid"])
        return frozenset(world_oids)

    def rebuild_from_durable_history(self) -> IndexSegment:
        return self._index.rebuild_from_durable_history()

    def verify_against_authority(self) -> Health:
        try:
            self.read_world_oids()  # validate lease-entry shape; malformed -> corrupt, not stale
        except InvalidRepositoryStateError as exc:
            return Health("corrupt", str(exc))
        return self._index.verify_against_authority()
