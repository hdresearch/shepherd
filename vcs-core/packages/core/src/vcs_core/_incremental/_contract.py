"""The shared contract every vcs-core incremental accelerator obeys.

An accelerator is a *derived* answer to a hot-path question over append-only
history. It is NEVER authority: the durable history (refs, receipts, journals) is.
The library guarantees these invariants uniformly, in one place, because the real
risk is N bespoke accelerators with subtly different fail-closed semantics:

  missing accelerator   -> the caller falls back to full recompute (correct, slow)
  corrupt accelerator   -> fail closed (raise); never silently fall back
  durable across procs  -> persisted + cheaply loadable; a fresh process's first
                           query is O(delta), never O(total history)
  CAS-bound advance     -> a record written but not CAS-committed is inert; the
                           prior generation stays authoritative
  concurrent advance    -> one winner; the loser re-folds onto the new base or rebuilds

Authority-backed derived view (the contract two review passes showed was missing)
----------------------------------------------------------------------------------
The five invariants above govern a *single* record. But an accelerator shadows a
SEPARATE authority (lease refs, retention refs, journals), and git has no atomic
multi-ref write — so every customer must also decide how the two relate. Left
implicit (call-site ordering + prose), that decision produced two stale/missing
index bugs in a row. So it is a first-class, declared part of the contract:

  invariant (NOT a choice) -- missing means *unknown*, not empty. The engine
      rebuilds from the authority before folding a delta
      (``SingleSegmentDeltaIndex.extend``); a write over a missing index must
      never materialize a subset. A "missing = empty" component is a primary
      store, not a derived view, and does not belong in this library.

  per-customer choice -- each customer DECLARES a :class:`DerivedViewContract`
      (``read_safety`` + ``crash_lag``) stating which staleness direction its use
      tolerates and how it orders its accelerator write against the authority
      write to guarantee it. The conformance suite asserts the live accelerator
      honors the direction it claims (superset on a crash either side, for the
      lease index), so the policy is testable rather than a comment.

See ``260621-1730-incremental-frontier-primitive.md`` (rev2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

AcceleratorStatus = Literal["fresh", "stale", "missing", "corrupt"]

# Which staleness direction a customer's *read* tolerates.
ReadSafety = Literal["superset", "exact"]
# How a customer orders its accelerator write relative to the authoritative write.
CrashLagOrdering = Literal["index-leads", "authority-leads", "atomic"]

# A declared contract must pair a read-safety direction with a crash-lag ordering that actually
# produces it, so the policy cannot drift back into prose-only territory: index-leads over-reports
# (superset); an atomic co-write is both-or-neither (exact); authority-leads never over-reports
# (exact, via the consumer's fail-closed / re-verify). Any other pairing is incoherent.
_LEGAL_DERIVED_VIEW_PAIRINGS = frozenset(
    {
        ("superset", "index-leads"),
        ("exact", "atomic"),
        ("exact", "authority-leads"),
    }
)


@dataclass(frozen=True)
class Health:
    """Result of verifying an accelerator against its durable authority."""

    status: AcceleratorStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "fresh"


@dataclass(frozen=True)
class DerivedViewContract:
    """The per-customer policy an authority-backed accelerator declares.

    Two review passes found stale/missing-index correctness bugs because these
    choices lived only in call-site ordering and prose. Naming them makes each a
    diffable, testable decision instead of an implicit one.

    ``read_safety``
        ``"superset"`` — the accelerator may over-report relative to the
        authority, and the caller's use is conservative under over-reporting
        (e.g. a GC *protection* set: protecting a few extra refs is safe, dropping
        one is not). ``"exact"`` — staleness is not tolerated; the caller must
        fail closed or re-verify against the authority.
    ``crash_lag``
        ``"index-leads"`` — the accelerator is advanced BEFORE the matching
        authority write (add before the ref is created) and retracted AFTER
        (tombstone after the ref is deleted), so a crash in either window leaves a
        SUPERSET. ``"authority-leads"`` is the mirror, for callers that must never
        over-report. (Pairs with ``read_safety``: ``index-leads`` ⇒ ``superset``.)
        ``"atomic"`` (co-write) — the accelerator ref-move rides the **same**
        ``git update-ref --stdin`` transaction as the authority write, so a crash
        commits both or neither and the index is never out of step with authority
        for any mutation routed through that path. This is strictly stronger than
        ``index-leads``: it pairs with ``read_safety="exact"`` (no superset / no
        read-side reconciliation). The "exact" guarantee holds only within the
        closed set of writers routed through the co-write; out-of-model writers are
        reconciled by fsck/recovery, and missing→fallback / corrupt→fail-closed
        still resolve to the conservative direction for the consumer.
    """

    read_safety: ReadSafety
    crash_lag: CrashLagOrdering
    detail: str = ""

    def __post_init__(self) -> None:
        if (self.read_safety, self.crash_lag) not in _LEGAL_DERIVED_VIEW_PAIRINGS:
            raise ValueError(
                f"incoherent DerivedViewContract: read_safety={self.read_safety!r} with "
                f"crash_lag={self.crash_lag!r} (legal: superset/index-leads, exact/atomic, "
                "exact/authority-leads)"
            )


@runtime_checkable
class RebuildableAccelerator(Protocol):
    """A durable, fail-closed, rebuildable accelerator over append-only history."""

    def rebuild_from_durable_history(self) -> object:
        """Recompute the accelerator from its authoritative source and persist it.

        This is the ONLY path allowed to do O(history) work; it runs off the hot
        path (cold-start self-heal, fsck, fallback).
        """
        ...

    def verify_against_authority(self) -> Health:
        """Rebuild transiently and compare to the live record; never mutate.

        ``fresh`` iff the live record reproduces the rebuilt authority bit-for-bit.
        """
        ...
