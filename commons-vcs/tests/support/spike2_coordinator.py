"""Minimal Spike 2 coordinator for concurrency and recovery tests.

This is deliberately not a vcs-core port. It only exercises the
coordination protocol from refactor.md §8.4 / §9.3.5 against GitBackend:
per-scope locks, pending-effect refs, commit append, and scope-head CAS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from commons_vcs import Edge, Object, Repo

if TYPE_CHECKING:
    from collections.abc import Callable

    from commons_vcs.backends.git import GitBackend

RecoveryOutcome = Literal["nothing_pending", "cleared_uncommitted_pending", "cleared_committed_pending"]


def _digest_segment(digest: str) -> str:
    if ":" not in digest:
        raise ValueError(f"digest missing algorithm prefix: {digest!r}")
    algo, hex_part = digest.split(":", 1)
    return f"{algo}/{hex_part}"


@dataclass
class Spike2Coordinator:
    """Coordinator harness for the Spike 2 emit/recovery protocol."""

    repo: Repo
    backend: GitBackend

    def head_ref(self, scope_id: str) -> str:
        return f"scopes/{_digest_segment(scope_id)}/head"

    def pending_ref(self, scope_id: str) -> str:
        return f"scopes/{_digest_segment(scope_id)}/pending-effect"

    def recover_scope(self, scope_id: str) -> RecoveryOutcome:
        """Clear stale pending-effect state after a crashed emit cycle.

        A pending effect counts as committed only if it is reachable from
        the current scope head chain. Raw object-store existence is not
        enough: a crash after commit append but before head CAS leaves an
        orphan commit that cites the effect but did not advance the scope.
        """
        pending = self.backend.get_ref(self.pending_ref(scope_id))
        if pending is None:
            return "nothing_pending"

        head = self.backend.get_ref(self.head_ref(scope_id))
        committed = head is not None and self.head_chain_contains_effect(head, pending)
        self.backend.delete_ref(self.pending_ref(scope_id))
        if committed:
            return "cleared_committed_pending"
        return "cleared_uncommitted_pending"

    def head_chain_contains_effect(self, head_id: str, effect_id: str) -> bool:
        seen: set[str] = set()
        cursor: str | None = head_id
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            obj = self.repo.get(cursor)
            if obj is None:
                return False
            if any(e.role == "effect" and e.target == effect_id for e in obj.edges):
                return True
            parents = [e.target for e in obj.edges if e.role == "parent"]
            cursor = parents[0] if parents else None
        return False

    def append_observed_effect(
        self,
        *,
        scope_id: str,
        effect: Object,
        workspace_tree: str,
        cutpoint: Callable[[str], None] | None = None,
    ) -> str:
        """Append an effect-backed commit and advance the scope head."""
        with self.backend.scope_lock(scope_id):
            self.recover_scope(scope_id)
            parent_id = self.backend.get_ref(self.head_ref(scope_id))
            effect_id = self.repo.append(effect)
            if not self.backend.compare_and_swap_ref(self.pending_ref(scope_id), None, effect_id):
                raise RuntimeError("pending-effect ref unexpectedly set after recovery")
            self._cut(cutpoint, "after_pending")

            edges = [Edge("effect", effect_id)]
            if parent_id is not None:
                edges.append(Edge("parent", parent_id))
            commit = Object(
                schema_ref="vcscore/commit/v1",
                body={"workspace_tree": workspace_tree},
                edges=tuple(edges),
            )
            commit_id = self.repo.append(commit)
            self._cut(cutpoint, "after_commit")

            if not self.backend.compare_and_swap_ref(self.head_ref(scope_id), parent_id, commit_id):
                raise RuntimeError("scope head drifted while scope lock was held")
            self._cut(cutpoint, "after_head")

            self.backend.delete_ref(self.pending_ref(scope_id))
            return commit_id

    @staticmethod
    def _cut(cutpoint: Callable[[str], None] | None, name: str) -> None:
        if cutpoint is not None:
            cutpoint(name)
