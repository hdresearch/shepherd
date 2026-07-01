from __future__ import annotations

import subprocess
from pathlib import Path

import pygit2
import pytest
from commons_vcs import Repo
from commons_vcs.backends.git import GitBackend
from vcs_core.profiles.committed_view import committed_native_effect_citers
from vcs_core.profiles.commons_refs import workspace_tree_pin_name
from vcs_core.profiles.commons_vcs import profile as vcscore_profile
from vcs_core.profiles.projection import (
    _canonical_project,
    append_projected_scope_history,
    project_commit_object,
    project_effect_object,
    project_scope_object,
)
from vcs_core.store import Store
from vcs_core.types import ScopeInfo


def _store(tmp_path: Path) -> Store:
    store = Store(str(tmp_path / ".vcscore"))
    store.create_root_commit()
    return store


def _commit(store: Store, oid: str) -> pygit2.Commit:
    obj = store._repo[oid]
    assert isinstance(obj, pygit2.Commit)
    return obj


def _ground_scope() -> ScopeInfo:
    return ScopeInfo(
        name="ground",
        ref=Store.GROUND_REF,
        instance_id="ground-test",
        creation_oid="",
        world_id="world-ground",
    )


def test_project_real_store_effect_normalizes_metadata_and_workspace_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-project")
    oid = store._emit_effect(
        scope,
        "FilePatch",
        {"confidence": 0.5, "labels": ("fast", "safe")},
        workspace_changes=[("bin/run", b"#!/bin/sh\necho hi\n", 0o100755)],
        substrate="filesystem",
    )

    effect = project_effect_object(store._repo, _commit(store, oid))

    assert effect.schema_ref == "vcscore/effect/v1"
    assert effect.body["effect_type"] == "FilePatch"
    assert effect.body["substrate"] == "filesystem"
    payload = effect.body["payload"]
    assert payload["confidence"] == {"__type__": "float", "value": "0.5"}
    assert payload["labels"] == ("fast", "safe")
    assert "scope" not in payload
    assert "timestamp" not in payload
    changes = effect.body["workspace_changes"]
    assert len(changes) == 1
    change = changes[0]
    assert change["path"] == "bin/run"
    assert change["status"] == "added"
    assert change["git_filemode"] == "100755"
    assert str(change["content_digest"]).startswith("sha256:")


def test_project_real_store_effect_projects_deleted_workspace_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-project-delete")
    store._emit_effect(
        scope,
        "Add",
        {"seq": 1},
        workspace_changes=[("notes.txt", b"hello\n")],
        substrate="filesystem",
    )
    oid = store._emit_effect(
        scope,
        "Delete",
        {"seq": 2},
        workspace_changes=[("notes.txt", None)],
        substrate="filesystem",
    )

    effect = project_effect_object(store._repo, _commit(store, oid))

    assert effect.body["workspace_changes"] == ({"path": "notes.txt", "status": "deleted"},)


def test_project_effect_identity_ignores_store_carrier_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-stable-effect")
    first_oid = store._emit_effect(
        scope,
        "Marker",
        {"note": "same"},
        substrate="marker",
    )
    second_oid = store._emit_effect(
        scope,
        "Marker",
        {"note": "same"},
        substrate="marker",
    )

    first = project_effect_object(store._repo, _commit(store, first_oid))
    second = project_effect_object(store._repo, _commit(store, second_oid))

    assert first.body == second.body
    assert first.id == second.id
    assert first.body["payload"] == {"note": "same"}


def test_project_effect_identity_keeps_semantic_metadata_distinct(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-distinct-effect")
    first_oid = store._emit_effect(scope, "Marker", {"note": "first"}, substrate="marker")
    second_oid = store._emit_effect(scope, "Marker", {"note": "second"}, substrate="marker")

    first = project_effect_object(store._repo, _commit(store, first_oid))
    second = project_effect_object(store._repo, _commit(store, second_oid))

    assert first.body["payload"] == {"note": "first"}
    assert second.body["payload"] == {"note": "second"}
    assert first.id != second.id


def test_canonical_projection_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(TypeError, match="keys must be strings"):
        _canonical_project({1: "one"})


def test_project_real_store_commit_validates_against_profile(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-commit")
    oid = store._emit_effect(
        scope,
        "Marker",
        {"note": "from-real-store"},
        workspace_changes=[("README.md", b"# hello\n")],
        substrate="marker",
    )
    carrier_commit = _commit(store, oid)
    repo = Repo(profiles=[vcscore_profile])

    scope_id = repo.append(project_scope_object(scope))
    effect_id = repo.append(project_effect_object(store._repo, carrier_commit))
    commit_id = repo.append(
        project_commit_object(
            store._repo,
            carrier_commit,
            effect_id=effect_id,
            scope_id=scope_id,
        )
    )

    result = repo.verify(
        head=commit_id,
        trust_root=commit_id,
        validate_trust_root=True,
    )

    assert result.outcome == "ok.verified"
    assert result.failures == []


def test_shadow_append_scope_history_projects_real_first_parent_chain(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow")
    first_oid = store._emit_effect(
        scope,
        "First",
        {"seq": 1},
        workspace_changes=[("one.txt", b"one")],
        substrate="marker",
    )
    second_oid = store._emit_effect(
        scope,
        "Second",
        {"seq": 2},
        workspace_changes=[("two.txt", b"two")],
        substrate="marker",
    )
    repo = Repo(profiles=[vcscore_profile])

    projected = append_projected_scope_history(
        repo,
        store._repo,
        scope=scope,
        head_oid=second_oid,
    )

    assert [entry.carrier_oid for entry in projected.entries] == [first_oid, second_oid]
    assert projected.head_id == projected.entries[-1].commit_id
    second_commit = repo.get(projected.entries[-1].commit_id)
    assert second_commit is not None
    assert any(edge.role == "parent" and edge.target == projected.entries[0].commit_id for edge in second_commit.edges)
    assert committed_native_effect_citers(
        repo,
        projected.entries[0].effect_id,
        heads=[projected.head_id or ""],
    ) == [projected.entries[0].commit_id]


def test_shadow_append_scope_history_projects_first_ground_effect_without_root(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ground = _ground_scope()
    first_oid = store._emit_effect(
        ground,
        "FirstGround",
        {"seq": 1},
        workspace_changes=[("one.txt", b"one")],
        substrate="marker",
    )
    second_oid = store._emit_effect(
        ground,
        "SecondGround",
        {"seq": 2},
        workspace_changes=[("two.txt", b"two")],
        substrate="marker",
    )
    repo = Repo(profiles=[vcscore_profile])

    projected = append_projected_scope_history(
        repo,
        store._repo,
        scope=ground,
        head_oid=second_oid,
    )

    assert [entry.carrier_oid for entry in projected.entries] == [first_oid, second_oid]
    first_commit = repo.get(projected.entries[0].commit_id)
    second_commit = repo.get(projected.entries[1].commit_id)
    assert first_commit is not None
    assert second_commit is not None
    assert [edge.target for edge in first_commit.edges if edge.role == "parent"] == []
    assert [edge.target for edge in second_commit.edges if edge.role == "parent"] == [projected.entries[0].commit_id]


def test_shadow_projected_scope_history_persists_in_git_backend_with_tree_pins(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-git")
    first_oid = store._emit_effect(
        scope,
        "First",
        {"seq": 1},
        workspace_changes=[("one.txt", b"one")],
        substrate="marker",
    )
    second_oid = store._emit_effect(
        scope,
        "Second",
        {"seq": 2},
        workspace_changes=[("two.txt", b"two")],
        substrate="marker",
    )
    git_dir = Path(store._repo.path).resolve()
    backend = GitBackend.open(git_dir)
    repo = Repo(profiles=[vcscore_profile], backend=backend)

    projected = append_projected_scope_history(
        repo,
        store._repo,
        scope=scope,
        head_oid=second_oid,
    )
    assert [entry.carrier_oid for entry in projected.entries] == [first_oid, second_oid]
    assert projected.head_id is not None

    pinned_trees: list[str] = []
    for entry in projected.entries:
        commit = repo.get(entry.commit_id)
        assert commit is not None
        tree_oid = str(commit.body["workspace_tree"])
        pinned_trees.append(tree_oid)
        backend.pin_git_object(workspace_tree_pin_name(entry.commit_id), tree_oid)

    result = subprocess.run(
        ["git", "gc", "--prune=now", "--aggressive"],
        cwd=str(git_dir),
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    fresh_backend = GitBackend.open(git_dir)
    fresh_repo = Repo(profiles=[vcscore_profile], backend=fresh_backend)
    assert fresh_repo.get(projected.head_id) is not None
    for tree_oid in pinned_trees:
        assert tree_oid in fresh_backend._repo
    assert committed_native_effect_citers(
        fresh_repo,
        projected.entries[0].effect_id,
        heads=[projected.head_id],
    ) == [projected.entries[0].commit_id]
