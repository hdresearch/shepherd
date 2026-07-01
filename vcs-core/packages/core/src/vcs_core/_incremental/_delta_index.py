"""Primitive B: a segmented delta-index over an append-only set.

Durable, fail-closed, rebuildable; generalizes the publish-frontier doc's
"protected-retention index": it answers set membership / ``key -> value`` in
O(active), never O(total refs).

This module ships the GENERIC single-compacted-segment implementation. Customers
(e.g. ``_lease_index.ActiveLeaseIndex``) wire it to a concrete ref + an
authoritative ``rebuild_source``. A full LSM segment chain is deferred to the
retention customer (large active set); the lease set is small, so we compact on
every write (one materialized segment) while keeping the general ``IndexSegment``
shape so the interface stays honest.

The record advances by CAS on its ref (``cas_update_ref``): a record written but
not CAS-committed is an inert orphan, so a crash mid-extend leaves the prior
generation authoritative, and concurrent extends resolve to one winner.

See ``260621-1730-incremental-frontier-primitive.md`` rev2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._incremental._contract import Health
from vcs_core._incremental._git_record import (
    cas_update_ref,
    current_ref_target,
    read_record,
    with_self_digest,
    write_record,
)
from vcs_core._ref_txn import RefMove

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import pygit2

_DIGEST_FIELD = "index_digest"


class _Tombstone:
    """Sentinel passed as a value in ``extend`` to remove a key (release/retract)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "TOMBSTONE"


TOMBSTONE = _Tombstone()


@dataclass(frozen=True)
class IndexSegment:
    """One compacted segment of a delta-index (the durable record's decoded form)."""

    base_segment_ref: str | None  # prior record commit oid — the inductive link
    generation: int
    entries: dict[str, Any]  # materialized active set at this generation
    delta_added: dict[str, Any]
    delta_removed: tuple[str, ...]
    segment_digest: str

    def members(self) -> frozenset[str]:
        return frozenset(self.entries)

    def query(self, key: str) -> Any | None:
        """Value for ``key`` in this segment, or ``None`` if absent from the active set."""
        return self.entries.get(key)


@dataclass(frozen=True)
class PreparedSegmentWrite:
    """A delta-index advance prepared but not yet committed (record written, ref not moved).

    The customer folds :meth:`ref_move` into its own atomic ``git update-ref --stdin``
    transaction (alongside the authority ref-moves); ``segment`` becomes live iff that transaction
    commits. On a CAS loss (the ref moved underneath) the customer re-prepares. When
    ``idempotent_noop`` is true the delta did not change the materialized set, so there is **no**
    ref-move to batch — skip ``ref_move``; ``segment`` is the unchanged current segment.
    """

    ref: str
    new_commit: str
    expected_oid: str | None
    segment: IndexSegment
    idempotent_noop: bool

    def ref_move(self) -> RefMove:
        """The :class:`RefMove` that commits this advance (do not call for an idempotent no-op).

        The customer batches it into a ``git update-ref --stdin`` transaction with the authority
        ref-moves, so index and authority advance atomically.
        """
        return RefMove(ref=self.ref, new_oid=self.new_commit, expected_oid=self.expected_oid)


class DeltaIndex(Protocol):
    """Set-membership accelerator answered in O(active). Also a ``RebuildableAccelerator``.

    Membership is read through :meth:`read`, which returns ``IndexSegment | None`` — and
    ``None`` means *missing* (the caller must fall back / rebuild), never *empty*. Queries
    live on the returned :class:`IndexSegment` precisely so a missing index cannot be read
    as an empty one (the derived-view contract; see ``_contract.py``).
    """

    def read(self) -> IndexSegment | None: ...
    def extend(self, delta: Mapping[str, Any]) -> IndexSegment: ...
    def compact(self) -> IndexSegment: ...
    def rebuild_from_durable_history(self) -> IndexSegment: ...
    def verify_against_authority(self) -> Health: ...


class SingleSegmentDeltaIndex:
    """Durable single-compacted-segment ``DeltaIndex`` (and ``RebuildableAccelerator``)."""

    def __init__(
        self,
        repo: pygit2.Repository,
        ref: str,
        *,
        schema: str,
        meta_name: str,
        message_prefix: str,
        rebuild_source: Callable[[], Mapping[str, Any]],
        max_cas_retries: int = 8,
    ) -> None:
        self._repo = repo
        self._ref = ref
        self._schema = schema
        self._meta_name = meta_name
        self._message_prefix = message_prefix
        self._rebuild_source = rebuild_source
        self._max_cas_retries = max_cas_retries

    # --- read (boundary-bounded: one record, never a ref-namespace scan) ---

    def read(self) -> IndexSegment | None:
        """Return the live segment, ``None`` if **missing**; raise if **corrupt**.

        This is the only read. ``None`` means *unknown* — the caller must fall back to
        the authority / rebuild, never treat it as an empty set. Membership queries are
        answered on the returned :class:`IndexSegment` (``.members()`` / ``.query(key)``),
        so "missing" cannot be silently collapsed into "empty" by a convenience accessor.
        """
        payload = read_record(
            self._repo, self._ref, meta_name=self._meta_name, schema=self._schema, digest_field=_DIGEST_FIELD
        )
        return None if payload is None else self._segment_from_payload(payload)

    # --- writes (CAS-bound advance) ---

    def extend(self, delta: Mapping[str, Any]) -> IndexSegment:
        """Apply ``delta`` and CAS a new segment via a standalone ``git update-ref`` (the per-publish path).

        Idempotent: a delta that does not change the materialized set is a no-op. For a hot per-op
        path that needs the ref-move batched atomically into another transaction, use
        :meth:`prepare_extend` instead of paying a standalone CAS subprocess.
        """
        added, removed = self._split_delta(delta)
        last_error: InvalidRepositoryStateError | None = None
        for _attempt in range(self._max_cas_retries):
            prepared = self._prepare_once(added, removed)
            if prepared.idempotent_noop:
                return prepared.segment
            if cas_update_ref(self._repo, prepared.ref, prepared.new_commit, expected_oid=prepared.expected_oid):
                return prepared.segment
            last_error = InvalidRepositoryStateError("CAS lost during extend")
        raise last_error or InvalidRepositoryStateError(f"delta index CAS contention exceeded retries at {self._ref}")

    def prepare_extend(self, delta: Mapping[str, Any]) -> PreparedSegmentWrite:
        """Prepare a delta advance WITHOUT moving the ref, for an atomic co-write.

        Reads the current segment, folds ``delta`` (missing → rebuild from authority, exactly as
        :meth:`extend`), writes the new record commit, and returns a :class:`PreparedSegmentWrite`
        whose ref-move the customer batches into its own ``git update-ref --stdin`` transaction
        (with the authority ref-moves) so index and authority advance together. The returned
        ``segment`` is live iff that transaction commits; on a CAS loss the customer re-prepares
        (the base moved). The CAS is the customer's, so this does **not** retry — the retry loop
        wraps the whole co-write.
        """
        added, removed = self._split_delta(delta)
        return self._prepare_once(added, removed)

    def compact(self) -> IndexSegment:
        current = self.read()
        return current if current is not None else self.rebuild_from_durable_history()

    # --- rebuild / verify (the only O(history) paths; off the hot path) ---

    def rebuild_from_durable_history(self) -> IndexSegment:
        """Recompute the index from the authoritative source and persist it.

        Tolerates a corrupt prior record (overwrites it). The only place the
        authority's full scan runs.
        """
        authoritative = dict(self._rebuild_source())
        last_error: InvalidRepositoryStateError | None = None
        for _attempt in range(self._max_cas_retries):
            expected_commit = current_ref_target(self._repo, self._ref)
            try:
                prior = self.read()
                prior_generation = prior.generation if prior is not None else -1
            except InvalidRepositoryStateError:
                prior_generation = -1  # corrupt prior — overwrite, restart numbering
            segment = self._write_segment(
                expected_commit=expected_commit,
                generation=prior_generation + 1,
                entries=authoritative,
                added=authoritative,
                removed=(),
            )
            if segment is not None:
                return segment
            last_error = InvalidRepositoryStateError("CAS lost during rebuild")
        raise last_error or InvalidRepositoryStateError(f"delta index rebuild CAS contention at {self._ref}")

    def verify_against_authority(self) -> Health:
        """Compare the live record to a fresh rebuild of the authority; never mutate."""
        authoritative = dict(self._rebuild_source())
        try:
            live = self.read()
        except InvalidRepositoryStateError as exc:
            return Health("corrupt", str(exc))
        if live is None:
            return Health("missing", "index record absent")
        if dict(live.entries) == authoritative:
            return Health("fresh")
        return Health("stale", f"index has {len(live.entries)} entries; authority has {len(authoritative)}")

    # --- internals ---

    @staticmethod
    def _split_delta(delta: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
        added = {key: value for key, value in delta.items() if value is not TOMBSTONE}
        removed = tuple(key for key, value in delta.items() if value is TOMBSTONE)
        return added, removed

    def _prepare_once(self, added: Mapping[str, Any], removed: tuple[str, ...]) -> PreparedSegmentWrite:
        """One fold+prepare pass: read base, apply delta, write the record (no ref-move).

        Shared by :meth:`extend` (standalone CAS) and :meth:`prepare_extend` (batched co-write).
        """
        expected_commit = current_ref_target(self._repo, self._ref)
        current = self.read()
        if current is not None:
            base_entries = dict(current.entries)
            base_generation = current.generation
        else:
            # Missing means UNKNOWN, not empty: reconstruct from the authority before folding, or
            # the write materializes a SUBSET the consumer would then trust (the derived-view
            # missing=rebuild invariant; see _contract.py).
            base_entries = dict(self._rebuild_source())
            base_generation = -1
        new_entries = dict(base_entries)
        new_entries.update(added)
        for key in removed:
            new_entries.pop(key, None)
        if current is not None and new_entries == base_entries:
            # idempotent no-op: nothing to commit (a missing index always materializes)
            return PreparedSegmentWrite(
                ref=self._ref,
                new_commit=expected_commit or "",
                expected_oid=expected_commit,
                segment=current,
                idempotent_noop=True,
            )
        payload = self._payload_for(
            generation=base_generation + 1, base=expected_commit, entries=new_entries, added=added, removed=removed
        )
        new_commit = write_record(
            self._repo,
            meta_name=self._meta_name,
            payload=payload,
            message=f"{self._message_prefix} gen {base_generation + 1}",
        )
        return PreparedSegmentWrite(
            ref=self._ref,
            new_commit=new_commit,
            expected_oid=expected_commit,
            segment=self._segment_from_payload(payload),
            idempotent_noop=False,
        )

    def _write_segment(
        self,
        *,
        expected_commit: str | None,
        generation: int,
        entries: Mapping[str, Any],
        added: Mapping[str, Any],
        removed: tuple[str, ...],
    ) -> IndexSegment | None:
        """Write a new record and CAS the ref. Returns the segment, or None on CAS loss."""
        payload = self._payload_for(
            generation=generation, base=expected_commit, entries=entries, added=added, removed=removed
        )
        new_commit = write_record(
            self._repo,
            meta_name=self._meta_name,
            payload=payload,
            message=f"{self._message_prefix} gen {generation}",
        )
        if cas_update_ref(self._repo, self._ref, new_commit, expected_oid=expected_commit):
            return self._segment_from_payload(payload)
        return None

    def _payload_for(
        self,
        *,
        generation: int,
        base: str | None,
        entries: Mapping[str, Any],
        added: Mapping[str, Any],
        removed: tuple[str, ...],
    ) -> dict[str, Any]:
        payload = {
            "schema": self._schema,
            "generation": generation,
            "base_segment_ref": base,
            "entries": dict(sorted(entries.items())),
            "delta_added": dict(sorted(added.items())),
            "delta_removed": tuple(sorted(removed)),
        }
        return with_self_digest(payload, digest_field=_DIGEST_FIELD)

    @staticmethod
    def _segment_from_payload(payload: Mapping[str, Any]) -> IndexSegment:
        # A valid self-digest only proves the bytes were not flipped — a structurally
        # malformed record can still carry a matching digest. Validate the shape so such
        # a record fails closed (InvalidRepositoryStateError -> classified corrupt),
        # rather than raising a raw KeyError/TypeError/ValueError out of read()/verify().
        generation = payload.get("generation")
        entries = payload.get("entries")
        if not isinstance(generation, int) or isinstance(generation, bool):
            raise InvalidRepositoryStateError("delta-index record has a missing or non-integer generation")
        if not isinstance(entries, dict):
            raise InvalidRepositoryStateError("delta-index record has a missing or malformed entries map")
        base = payload.get("base_segment_ref")
        if base is not None and not isinstance(base, str):
            raise InvalidRepositoryStateError("delta-index record has a malformed base_segment_ref")
        delta_added = payload.get("delta_added", {})
        delta_removed = payload.get("delta_removed", ())
        if not isinstance(delta_added, dict):
            raise InvalidRepositoryStateError("delta-index record has a malformed delta_added map")
        if not isinstance(delta_removed, (list, tuple)) or not all(isinstance(key, str) for key in delta_removed):
            raise InvalidRepositoryStateError("delta-index record has a malformed delta_removed list")
        return IndexSegment(
            base_segment_ref=base,
            generation=generation,
            entries=dict(entries),
            delta_added=dict(delta_added),
            delta_removed=tuple(delta_removed),
            segment_digest=str(payload[_DIGEST_FIELD]),
        )
