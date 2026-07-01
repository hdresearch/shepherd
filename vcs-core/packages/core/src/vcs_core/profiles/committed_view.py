"""Committed-view queries over commons-vcs Objects.

The commons-vcs inverse index answers "which stored Objects cite this
digest?" Vcs-core admission rules usually need the narrower question "which
Objects reachable from these committed heads cite this digest?" Keep the two
queries separate so orphaned crash-recovery artifacts do not become committed
facts by accident.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from commons_vcs import Repo


def reachable_from_heads(
    repo: Repo,
    heads: Iterable[str],
    *,
    parent_role: str = "parent",
    schema_ref: str | None = None,
) -> list[str]:
    """Return stored Object digests reachable by following parent edges.

    Missing heads or parent links are repository-state corruption from the
    committed-view perspective, so this helper raises instead of silently
    returning a partial result.
    """
    seen: set[str] = set()
    stack = sorted(set(heads), reverse=True)
    while stack:
        digest = stack.pop()
        if digest in seen:
            continue
        obj = repo.get(digest)
        if obj is None:
            raise ValueError(f"committed view references missing Object {digest}")
        seen.add(digest)
        for edge in reversed(obj.edges):
            if edge.role == parent_role:
                stack.append(edge.target)
    if schema_ref is None:
        return sorted(seen)
    return sorted(digest for digest in seen if (obj := repo.get(digest)) is not None and obj.schema_ref == schema_ref)


def committed_citers(
    repo: Repo,
    target: str,
    role: str,
    *,
    heads: Iterable[str],
    parent_role: str = "parent",
    source_schema_ref: str | None = None,
) -> list[str]:
    """Return committed Objects that cite `target` under `role`.

    This intersects the global inverse index with an explicit committed-head
    traversal. Stored orphan Objects remain visible to `Repo.cited_by()` but
    are excluded here unless a supplied head reaches them.
    """
    reachable = set(
        reachable_from_heads(
            repo,
            heads,
            parent_role=parent_role,
            schema_ref=source_schema_ref,
        )
    )
    return sorted(digest for digest in repo.cited_by(target, role) if digest in reachable)


def head_chain_contains_citation(
    repo: Repo,
    head: str,
    target: str,
    role: str,
    *,
    parent_role: str = "parent",
    source_schema_ref: str | None = None,
) -> bool:
    """Return whether one committed head chain cites `target` under `role`."""
    return bool(
        committed_citers(
            repo,
            target,
            role,
            heads=(head,),
            parent_role=parent_role,
            source_schema_ref=source_schema_ref,
        )
    )


def committed_native_effect_citers(
    repo: Repo,
    effect_id: str,
    *,
    heads: Iterable[str],
) -> list[str]:
    """Return committed vcs-core commits that cite a native vcs-core effect."""
    effect = repo.get(effect_id)
    if effect is None:
        raise ValueError(f"native effect Object is missing: {effect_id}")
    if effect.schema_ref != "vcscore/effect/v1":
        return []
    return committed_citers(
        repo,
        effect_id,
        "effect",
        heads=heads,
        source_schema_ref="vcscore/commit/v1",
    )
