"""The atomic co-write orchestrator: advance an accelerator together with an authority write.

Binds a fixed set of authority ``RefMove``s (the caller prepares them once — e.g. a journal entry
commit plus its open-ref create) with a per-attempt accelerator advance (``prepare`` re-prepares
each try) into ONE atomic ``git update-ref --stdin`` transaction, with a *classified* retry:

- the whole batch commits → the accelerator advanced and the authority refs moved together;
- the **accelerator ref** moved underneath us (another writer advanced the index) → RETRY, after
  re-preparing onto the new base. Nothing was applied (the batch is atomic), so the authority
  moves re-issue cleanly;
- an **authority ref** precondition failed → SURFACE immediately. That is a real conflict
  (same-operation race, recovery race, external mutation), never a blind retry.

This is the reusable multi-ref CAS primitive behind the open-journal index co-write and any future
co-writer (the retention frontier). See ``260622-admission-tier-open-ops-index.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._incremental._git_record import current_ref_target
from vcs_core._ref_txn import run_update_ref_stdin

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import pygit2

    from vcs_core._incremental._delta_index import IndexSegment, PreparedSegmentWrite
    from vcs_core._ref_txn import RefMove


def atomic_co_write(
    repo: pygit2.Repository,
    *,
    authority_moves: Sequence[RefMove],
    prepare: Callable[[], PreparedSegmentWrite],
    max_retries: int = 8,
) -> IndexSegment:
    """Advance an accelerator and an authority write in one atomic transaction, with classified retry.

    ``authority_moves`` are prepared ONCE by the caller; they stay valid across an
    accelerator-contention retry because a rejected batch applies nothing, so they re-issue cleanly.
    ``prepare`` is invoked each attempt to (re)prepare the accelerator advance onto the *current*
    base. Returns the live :class:`IndexSegment`. Raises :class:`InvalidRepositoryStateError` on an
    authority-ref conflict (surfaced, not retried) or when accelerator contention exceeds
    ``max_retries``.
    """
    last_detail = ""
    for _attempt in range(max_retries):
        prepared = prepare()
        moves = list(authority_moves)
        if not prepared.idempotent_noop:
            moves.append(prepared.ref_move())
        result = run_update_ref_stdin(repo, moves)
        if result.ok:
            return prepared.segment
        # Classify the rejection (version-independent — we re-check refs, we do not parse git's text).
        # Authority FIRST: a failed authority precondition is a real conflict (same-operation race,
        # recovery race, external mutation) and the batch could never have committed, so surface it
        # immediately — even if the accelerator ALSO moved, a retry would be wasted.
        if any(not _authority_precondition_holds(repo, move) for move in authority_moves):
            raise InvalidRepositoryStateError(f"co-write rejected by an authority ref precondition: {result.detail}")
        # Authority intact, but our accelerator ref moved off the base we prepared against: another
        # writer advanced the index -> re-prepare onto the new base and retry.
        if not prepared.idempotent_noop and current_ref_target(repo, prepared.ref) != prepared.expected_oid:
            last_detail = result.detail
            continue
        # Authority intact and the accelerator unmoved, yet rejected: a malformed batch or transient.
        raise InvalidRepositoryStateError(f"co-write rejected with no surviving precondition: {result.detail}")
    raise InvalidRepositoryStateError(f"co-write accelerator contention exceeded {max_retries} retries: {last_detail}")


def _authority_precondition_holds(repo: pygit2.Repository, move: RefMove) -> bool:
    """Whether ``move``'s CAS precondition still holds against the current ref state."""
    current = current_ref_target(repo, move.ref)
    if move.expected_oid is None:  # create-only: the ref must not exist
        return current is None
    return current == move.expected_oid  # update / delete: the ref must currently target expected_oid
