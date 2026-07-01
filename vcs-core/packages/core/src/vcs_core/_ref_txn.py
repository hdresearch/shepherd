"""Neutral low-level multi-ref CAS transaction primitive.

Lives BELOW both the operation-journal store (``_world_operation_journal``) and the incremental
accelerators (``_incremental``): each side contributes :class:`RefMove` instances, and one
transaction owner runs them as a single atomic ``git update-ref --stdin`` (all-or-none). Keeping it
here lets the journal store stay repo/ref-only and the accelerators stay storage-agnostic, while the co-write
that binds them has a real, reusable transaction abstraction (retention/frontier co-writes want the
same thing).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygit2

from vcs_core._errors import InvalidRepositoryStateError

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class RefMove:
    """One ref mutation in a ``git update-ref --stdin`` transaction.

    Exactly three shapes, validated at construction so impossible states cannot be built:

    - **create** — ``new_oid`` set, ``expected_oid`` None (the batch is rejected if the ref exists).
    - **update** — ``new_oid`` set, ``expected_oid`` set (CAS: the ref must currently target ``expected_oid``).
    - **delete** — ``new_oid`` None, ``expected_oid`` set (delete iff the ref still targets ``expected_oid``).
    """

    ref: str
    new_oid: str | None
    expected_oid: str | None

    def __post_init__(self) -> None:
        if not pygit2.reference_is_valid_name(self.ref):
            raise InvalidRepositoryStateError(f"invalid ref name: {self.ref!r}")
        if self.new_oid is None and self.expected_oid is None:
            raise InvalidRepositoryStateError(
                f"RefMove for {self.ref!r} is a no-op: neither new_oid (create/update) nor expected_oid (delete)"
            )

    def command(self) -> str:
        """Render the single ``git update-ref --stdin`` line for this move."""
        if self.new_oid is None:
            return f"delete {self.ref} {self.expected_oid}\n"
        if self.expected_oid is None:
            return f"create {self.ref} {self.new_oid}\n"
        return f"update {self.ref} {self.new_oid} {self.expected_oid}\n"


@dataclass(frozen=True)
class UpdateRefStdinResult:
    """Outcome of an atomic ``git update-ref --stdin`` transaction.

    ``ok`` is True iff the whole batch committed. On rejection, ``detail`` carries git's
    stderr/stdout so the caller can surface a real error. The caller should classify *retry vs
    surface* by re-checking WHICH ref moved (version-independent), not by parsing ``detail``.
    """

    ok: bool
    detail: str = ""


def run_update_ref_stdin(repo: pygit2.Repository, moves: Sequence[RefMove]) -> UpdateRefStdinResult:
    """Run one ATOMIC ``git update-ref --stdin`` transaction (all-or-none).

    Returns :class:`UpdateRefStdinResult`: ``ok=True`` iff the whole batch committed; otherwise
    ``ok=False`` with git's ``detail``. Any precondition loss, ref conflict, or malformed batch
    rejects the WHOLE transaction, so no ref moves either way. Raises
    :class:`InvalidRepositoryStateError` only if git could not be run at all.
    """
    try:
        result = subprocess.run(
            ["git", "update-ref", "--stdin"],
            cwd=repo.path,
            input="".join(move.command() for move in moves),
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        raise InvalidRepositoryStateError(f"failed to run git update-ref --stdin: {exc}") from exc
    if result.returncode == 0:
        return UpdateRefStdinResult(ok=True)
    detail = (result.stderr or result.stdout or "git update-ref --stdin rejected").strip()
    return UpdateRefStdinResult(ok=False, detail=detail)
