"""vcscore/commit/v1 profile (Phase -1 spike).

Validator enforces structural shape only:
- body has `workspace_tree` as a Git-OID-format string (40 hex SHA1
  or 64 hex SHA256). Not dereferenced.
- edges either:
    a) empty (genesis commit; Phase -1 simplification S1 — see
       preflight/worked-example.md), or
    b) at most one `parent` and exactly one `effect`. The `effect`
       target is not constrained at this layer (cross-profile per
       refactor.md §3.3).

`scope` edge is intentionally not required in Phase -1. Phase 0/1
introduces `vcscore/scope/v1` per refactor.md §3.4 and §8.2.

`vcscore/commit/v1`'s schema-scoped inverse-C1 rule (refactor.md §8.1
— a `vcscore/effect/v1` may be cited by at most one commit) is *not*
enforced here. Phase -1 doesn't append `vcscore/effect/v1` Objects
(genesis is no-edges, projection cites `shepherd/effect/v1`); the
inverse-C1 enforcement lives where it can fire and is deferred until
the spike actually exercises it.
"""

from __future__ import annotations

from commons_vcs import Failure, Object, Profile, Resolver

_HEX = set("0123456789abcdef")


def _is_git_oid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) not in (40, 64):
        return False
    return all(c in _HEX for c in value)


def validate_commit_v1(obj: Object, r: Resolver) -> Failure | None:
    body = obj.body
    if "workspace_tree" not in body:
        return Failure("schema", "vcscore/commit/v1.body must contain `workspace_tree`")
    if not _is_git_oid(body["workspace_tree"]):
        return Failure(
            "schema",
            "vcscore/commit/v1.body.workspace_tree must be a 40- or 64-char hex string (Git OID format)",
        )

    if not obj.edges:
        # Genesis commit — Phase -1 simplification.
        return None

    parent_edges = [e for e in obj.edges if e.role == "parent"]
    effect_edges = [e for e in obj.edges if e.role == "effect"]

    if len(parent_edges) > 1:
        return Failure("schema", "vcscore/commit/v1 allows at most one `parent` edge")
    if len(effect_edges) != 1:
        return Failure(
            "schema",
            "vcscore/commit/v1 (non-genesis) requires exactly one `effect` edge",
        )

    return None


profile = Profile(
    name="vcscore",
    validators={
        "vcscore/commit/v1": validate_commit_v1,
    },
)
