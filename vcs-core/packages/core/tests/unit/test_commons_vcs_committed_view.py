from __future__ import annotations

import pytest
from commons_vcs import Edge, Failure, Object, Profile, Repo
from vcs_core.profiles.committed_view import (
    committed_citers,
    committed_native_effect_citers,
    head_chain_contains_citation,
    reachable_from_heads,
)
from vcs_core.profiles.commons_vcs import profile as vcscore_profile

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _shepherd_accept(_obj: Object, _resolver: object) -> Failure | None:
    return None


shepherd_profile = Profile(
    name="shepherd-test",
    validators={"shepherd/effect/v1": _shepherd_accept},
)


def _repo(*profiles: Profile) -> Repo:
    return Repo(profiles=[vcscore_profile, *profiles])


def _scope() -> Object:
    return Object(
        schema_ref="vcscore/scope/v1",
        body={
            "name": "ground",
            "world_id": "world:ground",
            "scope_instance_id": "ground",
        },
    )


def _native_effect(effect_type: str = "Marker") -> Object:
    return Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": effect_type,
            "substrate": "vcscore",
            "payload": {},
        },
    )


def _shepherd_effect() -> Object:
    return Object(
        schema_ref="shepherd/effect/v1",
        body={"effect_type": "tool_call_completed"},
    )


def _commit(scope_id: str, effect_id: str, *, parent_id: str | None = None) -> Object:
    edges = [Edge("effect", effect_id), Edge("scope", scope_id)]
    if parent_id is not None:
        edges.append(Edge("parent", parent_id))
    return Object(
        schema_ref="vcscore/commit/v1",
        body={
            "workspace_tree": EMPTY_TREE,
            "git_object_format": "sha1",
        },
        edges=edges,
    )


def test_committed_citers_ignore_orphan_stored_commits() -> None:
    repo = _repo()
    scope_id = repo.append(_scope())
    effect_id = repo.append(_native_effect())
    orphan_id = repo.append(_commit(scope_id, effect_id))

    assert repo.cited_by(effect_id, "effect") == [orphan_id]
    assert committed_citers(repo, effect_id, "effect", heads=()) == []
    assert committed_native_effect_citers(repo, effect_id, heads=()) == []


def test_committed_citers_follow_explicit_heads() -> None:
    repo = _repo()
    scope_id = repo.append(_scope())
    first_effect_id = repo.append(_native_effect("First"))
    first_id = repo.append(_commit(scope_id, first_effect_id))
    second_effect_id = repo.append(_native_effect("Second"))
    second_id = repo.append(_commit(scope_id, second_effect_id, parent_id=first_id))

    assert reachable_from_heads(repo, [second_id], schema_ref="vcscore/commit/v1") == sorted([first_id, second_id])
    assert committed_citers(repo, first_effect_id, "effect", heads=[second_id]) == [first_id]
    assert head_chain_contains_citation(repo, second_id, first_effect_id, "effect")


def test_committed_native_effect_citers_are_schema_scoped() -> None:
    repo = _repo(shepherd_profile)
    scope_id = repo.append(_scope())
    shepherd_effect_id = repo.append(_shepherd_effect())
    commit_id = repo.append(_commit(scope_id, shepherd_effect_id))

    assert committed_citers(repo, shepherd_effect_id, "effect", heads=[commit_id]) == [commit_id]
    assert committed_native_effect_citers(repo, shepherd_effect_id, heads=[commit_id]) == []


def test_committed_view_rejects_missing_head() -> None:
    repo = _repo()
    missing = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="missing Object"):
        reachable_from_heads(repo, [missing])
