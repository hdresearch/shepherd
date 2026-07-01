"""The open-operation-journal index — the third ``DeltaIndex`` customer.

Admission must know the set of OPEN operation-journal refs (the ``ops/open/*`` family) without
enumerating the whole ref namespace. This index maintains that set as a durable record so the hot
read is O(open), one blob.

Unlike the lease index (per-*publish*, a standalone CAS subprocess is amortized over a heavy op),
the open set mutates **per ``mg.exec``** (open on start, tombstone on close), so a standalone CAS is
a net loss. Its writes are instead **batched into the journal's own atomic ``git update-ref --stdin``
transaction** (the ``crash_lag="atomic"`` co-write; see ``_co_write.atomic_co_write``), so index and
authority advance together — strictly stronger than the lease index's superset.

Membership is the open **ref** itself (not the decoded operation id), so corrupt / long / hashed
opens are covered without a payload read, and ``rebuild_source`` is a lightweight ref enumeration,
not a validating probe. See ``260622-admission-tier-open-ops-index.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._incremental._contract import DerivedViewContract, Health
from vcs_core._incremental._delta_index import TOMBSTONE, SingleSegmentDeltaIndex
from vcs_core._world_refs import is_open_operation_journal_ref, world_open_operation_journal_index_ref

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    import pygit2

    from vcs_core._incremental._delta_index import IndexSegment, PreparedSegmentWrite

OPEN_OPERATION_JOURNAL_INDEX_SCHEMA = "vcscore/open-operation-journal-index/v1"
_META_NAME = "open-operation-journal-index.json"


class OpenOperationJournalIndex:
    """Durable accelerator for the set of open operation-journal refs of one world store."""

    # The co-write makes index and authority advance in one atomic transaction, so the index is
    # EXACT for every mutation routed through it (no superset, no read-side reconciliation). Missing
    # -> fallback and corrupt -> fail-closed still resolve to the conservative (blocking) direction
    # for the admission gate; out-of-model stale drift is fsck/recovery's job.
    CONTRACT = DerivedViewContract(
        read_safety="exact",
        crash_lag="atomic",
        detail="index ref-move rides the journal's atomic update-ref --stdin co-write",
    )

    def __init__(
        self,
        repo: pygit2.Repository,
        world_store_id: str,
        *,
        rebuild_source: Callable[[], Iterable[str]],
    ) -> None:
        self._world_store_id = world_store_id
        self._index = SingleSegmentDeltaIndex(
            repo,
            world_open_operation_journal_index_ref(world_store_id),
            schema=OPEN_OPERATION_JOURNAL_INDEX_SCHEMA,
            meta_name=_META_NAME,
            message_prefix=f"open journal index {world_store_id}",
            # membership is the ref; metadata is empty so add and the lightweight rebuild agree.
            rebuild_source=lambda: {open_ref: {} for open_ref in rebuild_source()},
        )

    def prepare_add(self, open_ref: str) -> PreparedSegmentWrite:
        """Prepare adding ``open_ref`` to the index (batched into the journal open co-write)."""
        _require_open_journal_ref(open_ref)
        return self._index.prepare_extend({open_ref: {}})

    def prepare_remove(self, open_ref: str) -> PreparedSegmentWrite:
        """Prepare removing ``open_ref`` (batched into the journal close/archive/cleanup co-write)."""
        _require_open_journal_ref(open_ref)
        return self._index.prepare_extend({open_ref: TOMBSTONE})

    def read_open_refs(self) -> frozenset[str] | None:
        """The open ref set, or ``None`` when the index record is missing (caller falls back).

        Raises ``InvalidRepositoryStateError`` (fail closed) if the present record is corrupt or any
        entry is not a v2-shaped open ref — admission must never probe an arbitrary string from a
        malformed accelerator as if it were an authority ref.
        """
        segment = self._index.read()
        if segment is None:
            return None
        open_refs: set[str] = set()
        for key in segment.entries:
            if not isinstance(key, str) or not is_open_operation_journal_ref(key):
                raise InvalidRepositoryStateError(
                    f"open-journal index entry {key!r} is not a valid open operation-journal ref"
                )
            open_refs.add(key)
        return frozenset(open_refs)

    def rebuild_from_durable_history(self) -> IndexSegment:
        return self._index.rebuild_from_durable_history()

    def verify_against_authority(self) -> Health:
        try:
            self.read_open_refs()  # validate entry shapes; malformed -> corrupt, not stale
        except InvalidRepositoryStateError as exc:
            return Health("corrupt", str(exc))
        return self._index.verify_against_authority()


def _require_open_journal_ref(open_ref: str) -> None:
    # Fail fast at the WRITE boundary so a normal writer cannot self-corrupt the index with a
    # non-open ref (read_open_refs would catch it, but it should never get committed in the first place).
    if not is_open_operation_journal_ref(open_ref):
        raise InvalidRepositoryStateError(f"refusing to index a non-open operation-journal ref: {open_ref!r}")
