"""Reusable incremental accelerators over append-only vcs-core history.

One library, one fail-closed/durable/rebuildable contract, so the same machinery
serves retention, journals, leases, and the future vcs-core-backed TraceStore
without N bespoke frontiers. Validated first by the active-publication-lease
customer (``DeltaIndex`` (B) + the contract); the cut-frontier (A) / DAG and the
content-addressed ObjectStore (C) are reserved (see ``_stubs``).

Design: ``260621-1730-incremental-frontier-primitive.md`` (rev2: DAG cut-frontier
substrate, linear streams as the degenerate case).
"""

from vcs_core._incremental._co_write import atomic_co_write
from vcs_core._incremental._contract import (
    AcceleratorStatus,
    CrashLagOrdering,
    DerivedViewContract,
    Health,
    ReadSafety,
    RebuildableAccelerator,
)
from vcs_core._incremental._delta_index import (
    TOMBSTONE,
    DeltaIndex,
    IndexSegment,
    PreparedSegmentWrite,
    SingleSegmentDeltaIndex,
)
from vcs_core._incremental._lease_index import ACTIVE_LEASE_INDEX_SCHEMA, ActiveLeaseIndex
from vcs_core._incremental._open_journal_index import (
    OPEN_OPERATION_JOURNAL_INDEX_SCHEMA,
    OpenOperationJournalIndex,
)

__all__ = [
    "ACTIVE_LEASE_INDEX_SCHEMA",
    "OPEN_OPERATION_JOURNAL_INDEX_SCHEMA",
    "TOMBSTONE",
    "AcceleratorStatus",
    "ActiveLeaseIndex",
    "CrashLagOrdering",
    "DeltaIndex",
    "DerivedViewContract",
    "Health",
    "IndexSegment",
    "OpenOperationJournalIndex",
    "PreparedSegmentWrite",
    "ReadSafety",
    "RebuildableAccelerator",
    "SingleSegmentDeltaIndex",
    "atomic_co_write",
]
