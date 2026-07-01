"""Thin coordinator (Phase -1 spike).

The §8.5 split: substrates observe, coordinator builds the canonical
`vcscore/commit/v1` Object and appends it to the Repo. Substrates have
no Repo handle. The coordinator owns commit construction, edge wiring,
and the (here trivial) ordering.

What the coordinator does *not* do in Phase -1:
- per-scope advisory locks (§9.3.5): no concurrency exercised
- CAS ref updates (§9.3.5): in-memory Repo, no refs
- pending-effect register lifecycle (§8.4 steps 2/7): the effect id is
  passed in directly, not registered via a separate API. Phase -1
  exercises the data flow, not the cross-process coordination protocol.
- materialization or push (§9.3): in-memory only
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from commons_vcs import Edge, Object, Repo

if TYPE_CHECKING:
    from support.filesystem_substrate import Observation


class PhaseMinus1Coordinator:
    """Builds and appends `vcscore/commit/v1` Objects from observations.

    Args:
        repo: the commons-vcs Repo to append into.
    """

    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def append_commit(
        self,
        effect_id: str,
        observation: Observation,
        parent_id: str | None = None,
    ) -> str:
        """Construct a `vcscore/commit/v1` from the inputs and append it.

        Args:
            effect_id: digest of the effect this commit materializes.
                       Cross-profile target permitted (e.g.,
                       `shepherd/effect/v1`).
            observation: substrate output. `workspace_tree` is required;
                         must be a Git OID format string.
            parent_id: digest of the predecessor commit, if any. Omit
                       for the first commit in a scope.

        Returns the new commit's digest.

        Raises ValueError (via Repo.append) if validation rejects.
        """
        if observation.workspace_tree is None:
            raise ValueError(
                "Observation.workspace_tree is None; coordinator cannot build a commit without a workspace tree"
            )

        edges = []
        edges.append(Edge("effect", effect_id))
        if parent_id is not None:
            edges.append(Edge("parent", parent_id))

        commit = Object(
            schema_ref="vcscore/commit/v1",
            body={"workspace_tree": observation.workspace_tree},
            edges=tuple(edges),
        )
        return self.repo.append(commit)
