from __future__ import annotations

import pytest
from commons_vcs import Edge, Failure, Object, Profile, Repo
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


def _native_effect(effect_type: str = "Init") -> Object:
    return Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": effect_type,
            "substrate": "vcscore",
            "payload": {},
        },
    )


def _shepherd_effect(tool_call_id: str = "call-1") -> Object:
    return Object(
        schema_ref="shepherd/effect/v1",
        body={
            "effect_type": "tool_call_completed",
            "tool_call_id": tool_call_id,
        },
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


def test_genesis_with_explicit_init_effect_verifies() -> None:
    repo = _repo()
    scope = _scope()
    init_effect = _native_effect()
    scope_id = repo.append(scope)
    effect_id = repo.append(init_effect)
    genesis = _commit(scope_id, effect_id)
    genesis_id = repo.append(genesis)

    result = repo.verify(
        head=genesis_id,
        trust_root=genesis_id,
        validate_trust_root=True,
    )

    assert result.outcome == "ok.verified"
    assert result.failures == []


def test_effect_accepts_omitted_workspace_changes() -> None:
    repo = _repo()

    effect_id = repo.append(_native_effect())

    assert repo.get(effect_id) is not None


def test_effect_accepts_empty_workspace_changes() -> None:
    repo = _repo()
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Update",
            "substrate": "vcscore",
            "payload": {},
            "workspace_changes": [],
        },
    )

    effect_id = repo.append(effect)

    assert repo.get(effect_id) is not None


def test_effect_rejects_null_workspace_changes() -> None:
    repo = _repo()
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Update",
            "substrate": "vcscore",
            "payload": {},
            "workspace_changes": None,
        },
    )

    with pytest.raises(ValueError, match="workspace_changes must be an array"):
        repo.append(effect)


def test_effect_rejects_null_optional_workspace_change_fields() -> None:
    repo = _repo()

    for field in ("content_digest", "git_filemode"):
        effect = Object(
            schema_ref="vcscore/effect/v1",
            body={
                "effect_type": "Update",
                "substrate": "vcscore",
                "payload": {},
                "workspace_changes": [
                    {
                        "path": "README.md",
                        "status": "modified",
                        field: None,
                        **(
                            {"git_filemode": "100644"}
                            if field == "content_digest"
                            else {"content_digest": "sha256:" + "0" * 64}
                        ),
                    },
                ],
            },
        )

        with pytest.raises(ValueError, match=field):
            repo.append(effect)


def test_effect_rejects_unknown_workspace_change_fields() -> None:
    repo = _repo()
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Update",
            "substrate": "vcscore",
            "payload": {},
            "workspace_changes": [
                {
                    "path": "README.md",
                    "status": "modified",
                    "unexpected": True,
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="unknown fields"):
        repo.append(effect)


def test_effect_rejects_unknown_workspace_change_status() -> None:
    repo = _repo()
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Update",
            "substrate": "vcscore",
            "payload": {},
            "workspace_changes": [
                {
                    "path": "README.md",
                    "status": "renamed",
                    "content_digest": "sha256:" + "0" * 64,
                    "git_filemode": "100644",
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="status must be added, modified, or deleted"):
        repo.append(effect)


def test_effect_rejects_malformed_workspace_change_digest() -> None:
    repo = _repo()
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Update",
            "substrate": "vcscore",
            "payload": {},
            "workspace_changes": [
                {
                    "path": "README.md",
                    "status": "modified",
                    "content_digest": "not-a-digest",
                    "git_filemode": "100644",
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="content_digest must be a sha256 digest"):
        repo.append(effect)


def test_effect_requires_content_fields_for_materialized_workspace_changes() -> None:
    repo = _repo()
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Update",
            "substrate": "vcscore",
            "payload": {},
            "workspace_changes": [
                {
                    "path": "README.md",
                    "status": "added",
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="content_digest is required"):
        repo.append(effect)


def test_effect_rejects_content_fields_for_deleted_workspace_changes() -> None:
    repo = _repo()
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Update",
            "substrate": "vcscore",
            "payload": {},
            "workspace_changes": [
                {
                    "path": "README.md",
                    "status": "deleted",
                    "content_digest": "sha256:" + "0" * 64,
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="deleted change has invalid fields"):
        repo.append(effect)


def test_effect_rejects_edges() -> None:
    repo = _repo()
    target_id = repo.append(_native_effect("Target"))
    effect = Object(
        schema_ref="vcscore/effect/v1",
        body={
            "effect_type": "Derived",
            "substrate": "vcscore",
            "payload": {},
        },
        edges=(Edge("unexpected", target_id),),
    )

    with pytest.raises(ValueError, match="does not allow edges"):
        repo.append(effect)


def test_commit_rejects_malformed_workspace_tree_oid() -> None:
    repo = _repo()
    scope_id = repo.append(_scope())
    effect_id = repo.append(_native_effect())
    commit = Object(
        schema_ref="vcscore/commit/v1",
        body={
            "workspace_tree": "not-a-git-oid",
            "git_object_format": "sha1",
        },
        edges=(Edge("effect", effect_id), Edge("scope", scope_id)),
    )

    with pytest.raises(ValueError, match="workspace_tree"):
        repo.append(commit)


def test_commit_requires_explicit_effect_and_scope_edges() -> None:
    repo = _repo()
    scope_id = repo.append(_scope())
    effect_id = repo.append(_native_effect())

    missing_effect = Object(
        schema_ref="vcscore/commit/v1",
        body={"workspace_tree": EMPTY_TREE, "git_object_format": "sha1"},
        edges=(Edge("scope", scope_id),),
    )
    missing_scope = Object(
        schema_ref="vcscore/commit/v1",
        body={"workspace_tree": EMPTY_TREE, "git_object_format": "sha1"},
        edges=(Edge("effect", effect_id),),
    )

    with pytest.raises(ValueError, match="exactly one effect"):
        repo.append(missing_effect)
    with pytest.raises(ValueError, match="exactly one scope"):
        repo.append(missing_scope)


def test_commit_accepts_cross_profile_shepherd_effect() -> None:
    repo = _repo(shepherd_profile)
    scope_id = repo.append(_scope())
    effect_id = repo.append(_shepherd_effect())
    commit_id = repo.append(_commit(scope_id, effect_id))

    assert repo.get(commit_id) is not None
    assert repo.cited_by(effect_id, "effect") == [commit_id]


def test_native_vcscore_effect_uniqueness_is_not_profile_local() -> None:
    repo = _repo()
    scope_id = repo.append(_scope())
    effect_id = repo.append(_native_effect("Marker"))
    first_id = repo.append(_commit(scope_id, effect_id))
    second_id = repo.append(_commit(scope_id, effect_id, parent_id=first_id))

    assert repo.cited_by(effect_id, "effect") == sorted([first_id, second_id])


def test_cross_profile_effect_can_be_cited_by_multiple_commits() -> None:
    repo = _repo(shepherd_profile)
    scope_id = repo.append(_scope())
    effect_id = repo.append(_shepherd_effect())
    first_id = repo.append(_commit(scope_id, effect_id))
    second_id = repo.append(_commit(scope_id, effect_id, parent_id=first_id))

    assert repo.cited_by(effect_id, "effect") == sorted([first_id, second_id])


def test_scope_parent_must_target_scope_object() -> None:
    repo = _repo()
    effect_id = repo.append(_native_effect())
    bad_scope = Object(
        schema_ref="vcscore/scope/v1",
        body={
            "name": "child",
            "world_id": "world:child",
            "scope_instance_id": "child-1",
        },
        edges=(Edge("parent_scope", effect_id),),
    )

    with pytest.raises(ValueError, match="parent_scope"):
        repo.append(bad_scope)
