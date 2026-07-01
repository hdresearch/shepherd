"""Capability tests for the proposed v2 world-vector storage model.

These tests intentionally exercise storage primitives directly. They are not a
production v2 API contract yet; they pin down the assumptions that the v2
WorldStore/SubstrateStore implementation is expected to rely on.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path

import pygit2
import pytest

from .world_vectors_v2_helpers import (
    CANONICAL_PREFIX,
    FILEMODE_COMMIT,
)
from .world_vectors_v2_helpers import (
    candidate_ref as _candidate_ref,
)
from .world_vectors_v2_helpers import (
    canonical_bytes as _canonical_bytes,
)
from .world_vectors_v2_helpers import (
    canonical_digest as _canonical_digest,
)
from .world_vectors_v2_helpers import (
    commit_json as _commit_json,
)
from .world_vectors_v2_helpers import (
    encode_ref_component as _encode_ref_component,
)
from .world_vectors_v2_helpers import (
    is_ref_safe_component as _is_ref_safe_component,
)
from .world_vectors_v2_helpers import (
    measure_ref_resolution as _measure_ref_resolution,
)
from .world_vectors_v2_helpers import (
    publish_cas as _publish_cas,
)
from .world_vectors_v2_helpers import (
    read_blob_bytes as _read_blob_bytes,
)
from .world_vectors_v2_helpers import (
    read_json_blob as _read_json_blob,
)
from .world_vectors_v2_helpers import (
    store_identity as _store_identity,
)
from .world_vectors_v2_helpers import (
    update_ref as _update_ref,
)
from .world_vectors_v2_helpers import (
    validate_bound_store as _validate_bound_store,
)
from .world_vectors_v2_helpers import (
    validate_candidate_ref as _validate_candidate_ref,
)
from .world_vectors_v2_helpers import (
    validate_optional_gitlinks as _validate_optional_gitlinks,
)
from .world_vectors_v2_helpers import (
    validate_world_bindings as _validate_world_bindings,
)
from .world_vectors_v2_helpers import (
    world_commit as _world_commit,
)
from .world_vectors_v2_helpers import (
    world_snapshot as _world_snapshot,
)


@pytest.mark.spike
def test_world_commit_selects_sibling_heads_with_manifest_authority(tmp_path: Path) -> None:
    world_repo = pygit2.init_repository(str(tmp_path / "world.git"), bare=True)
    workspace_repo = pygit2.init_repository(str(tmp_path / "substrates" / "workspace.git"), bare=True)
    session_repo = pygit2.init_repository(str(tmp_path / "substrates" / "session.git"), bare=True)

    workspace_head = _commit_json(workspace_repo, "refs/heads/main", {"label": "workspace W43"})
    session_head = _commit_json(session_repo, "refs/heads/main", {"label": "session S7"})
    world_oid = _world_commit(
        world_repo,
        "refs/vcscore/ground",
        {"workspace": workspace_head, "session": session_head},
        {"schema": "vcscore/transition/v2", "operation_id": "op-initial", "parent_worlds": []},
    )

    commit = world_repo[world_oid]
    manifest = _read_json_blob(world_repo, commit.tree, "meta/world.json")
    transition = _read_json_blob(world_repo, commit.tree, "meta/transition.json")
    substrates_tree = world_repo[commit.tree["substrates"].id]

    assert manifest["snapshot"]["workspace"]["head"] == str(workspace_head)
    assert manifest["snapshot"]["session"]["head"] == str(session_head)
    assert "store_locator" not in manifest["snapshot"]["workspace"]
    assert manifest["locator_hints"] == {
        "store_session": "substrates/session.git",
        "store_workspace": "substrates/workspace.git",
    }
    relocated_manifest = {
        **manifest,
        "locator_hints": {
            "store_session": "/imported/substrates/session.git",
            "store_workspace": "/imported/substrates/workspace.git",
        },
    }
    assert _canonical_digest(manifest["snapshot"]) == _canonical_digest(relocated_manifest["snapshot"])
    assert _canonical_digest(manifest) != _canonical_digest(relocated_manifest)
    operation_final_bytes = _read_blob_bytes(world_repo, commit.tree, transition["operation_final"]["path"])
    assert operation_final_bytes.startswith(CANONICAL_PREFIX)
    assert transition["operation_final"]["digest"] == (f"sha256:{hashlib.sha256(operation_final_bytes).hexdigest()}")
    assert substrates_tree["workspace"].filemode == FILEMODE_COMMIT
    assert substrates_tree["workspace"].id == workspace_head
    assert substrates_tree["session"].id == session_head
    with pytest.raises(KeyError):
        world_repo[workspace_head]


@pytest.mark.spike
def test_parent_merge_can_take_child_workspace_and_pin_prior_session(tmp_path: Path) -> None:
    world_repo = pygit2.init_repository(str(tmp_path / "world.git"), bare=True)
    workspace_repo = pygit2.init_repository(str(tmp_path / "substrates" / "workspace.git"), bare=True)
    session_repo = pygit2.init_repository(str(tmp_path / "substrates" / "session.git"), bare=True)

    w42 = _commit_json(workspace_repo, "refs/heads/main", {"label": "workspace W42"})
    w43 = _commit_json(
        workspace_repo,
        "refs/vcscore/candidates/op-child/workspace",
        {"label": "workspace W43"},
        parents=(w42,),
    )
    s7 = _commit_json(session_repo, "refs/checkpoints/S7", {"label": "session S7"})
    s19 = _commit_json(session_repo, "refs/heads/parent", {"label": "session S19"}, parents=(s7,))
    s8 = _commit_json(
        session_repo,
        "refs/vcscore/candidates/op-child/session",
        {"label": "session S8", "requires": [{"binding": "workspace", "head": str(w42)}]},
        parents=(s7,),
    )

    p0 = _world_commit(
        world_repo,
        "refs/vcscore/ground",
        {"workspace": w42, "session": s19},
        {"schema": "vcscore/transition/v2", "operation_id": "op-parent-initial", "parent_worlds": []},
    )
    c0 = _world_commit(
        world_repo,
        "refs/vcscore/scopes/child",
        {"workspace": w42, "session": s7},
        {"schema": "vcscore/transition/v2", "operation_id": "op-child-fork", "parent_worlds": [str(p0)]},
        parents=(p0,),
    )
    c1 = _world_commit(
        world_repo,
        "refs/vcscore/scopes/child",
        {"workspace": w43, "session": s8},
        {"schema": "vcscore/transition/v2", "operation_id": "op-child-final", "parent_worlds": [str(c0)]},
        parents=(c0,),
    )
    p1 = _world_commit(
        world_repo,
        None,
        {"workspace": w43, "session": s7},
        {
            "schema": "vcscore/transition/v2",
            "operation_id": "op-parent-merge",
            "input_world": str(p0),
            "parent_worlds": [str(p0), str(c1)],
            "changes": {
                "workspace": {"from": str(w42), "to": str(w43), "policy": "take-child"},
                "session": {"from": str(s19), "to": str(s7), "policy": "pin-prior"},
            },
        },
        parents=(p0, c1),
    )

    _update_ref(workspace_repo, f"refs/vcscore/pins/world/store_world_test/{p1}/workspace", w43)
    _update_ref(session_repo, f"refs/vcscore/pins/world/store_world_test/{p1}/session", s7)

    assert _publish_cas(world_repo, "refs/vcscore/ground", p1, p0)
    commit = world_repo[world_repo.references["refs/vcscore/ground"].target]
    manifest = _read_json_blob(world_repo, commit.tree, "meta/world.json")

    assert manifest["snapshot"]["workspace"]["head"] == str(w43)
    assert manifest["snapshot"]["session"]["head"] == str(s7)
    assert manifest["snapshot"]["session"]["head"] not in {str(s8), str(s19)}


@pytest.mark.spike
def test_update_ref_cas_prevents_lost_world_ref_updates(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path / "world.git"), bare=True)
    p0 = _world_commit(
        repo,
        "refs/vcscore/ground",
        {},
        {"schema": "vcscore/transition/v2", "operation_id": "p0", "parent_worlds": []},
    )
    p1 = _world_commit(
        repo,
        None,
        {},
        {"schema": "vcscore/transition/v2", "operation_id": "p1", "parent_worlds": [str(p0)]},
        parents=(p0,),
    )
    p2 = _world_commit(
        repo,
        None,
        {},
        {"schema": "vcscore/transition/v2", "operation_id": "p2", "parent_worlds": [str(p0)]},
        parents=(p0,),
    )

    assert not _publish_cas(repo, "refs/vcscore/ground", p2, p1)
    assert repo.references["refs/vcscore/ground"].target == p0
    assert _publish_cas(repo, "refs/vcscore/ground", p1, p0)
    assert not _publish_cas(repo, "refs/vcscore/ground", p2, p0)
    assert repo.references["refs/vcscore/ground"].target == p1


@pytest.mark.spike
def test_ref_backend_contract_uses_pygit2_api_without_loose_ref_layout(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path / "default.git"), bare=True)

    _exercise_ref_backend_api(repo)


@pytest.mark.spike
def test_reftable_ref_backend_uses_same_pygit2_api_when_available(tmp_path: Path) -> None:
    repo = _init_reftable_repo_or_skip(tmp_path)

    _exercise_ref_backend_api(repo)


@pytest.mark.spike
def test_runtime_ref_namespace_smoke_uses_git_ref_api(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path / "runtime-refs.git"), bare=True)
    initial = _commit_json(repo, "refs/heads/initial", {"label": "initial"})
    moved = _commit_json(repo, "refs/heads/moved", {"label": "moved"}, parents=(initial,))
    namespace = "refs/vcscore/runtime/frontiers"

    for index in range(256):
        _update_ref(repo, f"{namespace}/run-{index:04d}", initial)

    names = sorted(ref for ref in repo.references if ref.startswith(f"{namespace}/"))

    assert len(names) == 256
    assert f"{namespace}/run-0042" in names
    assert _publish_cas(repo, f"{namespace}/run-0042", moved, initial)
    assert repo.references[f"{namespace}/run-0042"].target == moved
    assert not _publish_cas(repo, f"{namespace}/run-0042", initial, initial)
    assert repo.references[f"{namespace}/run-0042"].target == moved


@pytest.mark.spike
def test_store_identity_includes_resource_identity_and_rejects_alias_mismatch() -> None:
    manifest_head = _world_snapshot(
        {"workspace": pygit2.Oid(hex="1" * 40)},
        store_ids={"workspace": "store_workspace"},
        resource_ids={"workspace": "fs:repo-main"},
    )["snapshot"]["workspace"]

    _validate_bound_store(manifest_head, _store_identity())
    with pytest.raises(ValueError, match="resource_id"):
        _validate_bound_store(manifest_head, _store_identity(resource_id="fs:other-repo"))


@pytest.mark.spike
@pytest.mark.parametrize(
    "raw",
    [
        "",
        ".hidden",
        "workspace/main",
        "contains whitespace",
        "control-\x01-char",
        "component.lock",
        "has@{selector",
        "unicode-\u2603",
        "x" * 256,
    ],
)
def test_ref_safe_component_encoding_handles_pathological_values(tmp_path: Path, raw: str) -> None:
    repo = pygit2.init_repository(str(tmp_path / "refs.git"), bare=True)
    head = _commit_json(repo, "refs/heads/main", {"label": "payload"})

    encoded = _encode_ref_component(raw)

    assert _is_ref_safe_component(encoded)
    assert "/" not in encoded
    assert encoded != raw or _is_ref_safe_component(raw)
    if len(raw.encode("utf-8")) > 96:
        assert encoded.startswith("sha256_")
    _update_ref(repo, f"refs/vcscore/encoded/{encoded}", head)


@pytest.mark.spike
def test_optional_gitlink_index_must_match_manifest_when_present(tmp_path: Path) -> None:
    world_repo = pygit2.init_repository(str(tmp_path / "world.git"), bare=True)
    workspace_repo = pygit2.init_repository(str(tmp_path / "substrates" / "workspace.git"), bare=True)
    session_repo = pygit2.init_repository(str(tmp_path / "substrates" / "session.git"), bare=True)

    workspace_head = _commit_json(workspace_repo, "refs/heads/main", {"label": "workspace W1"})
    wrong_workspace_head = _commit_json(workspace_repo, "refs/heads/wrong", {"label": "workspace W0"})
    session_head = _commit_json(session_repo, "refs/heads/main", {"label": "session S1"})
    heads = {"workspace": workspace_head, "session": session_head}

    matching = _world_commit(
        world_repo,
        None,
        heads,
        {"schema": "vcscore/transition/v2", "operation_id": "op-matching", "parent_worlds": []},
    )
    _validate_optional_gitlinks(world_repo, world_repo[matching])

    manifest_only = _world_commit(
        world_repo,
        None,
        heads,
        {"schema": "vcscore/transition/v2", "operation_id": "op-manifest-only", "parent_worlds": []},
        include_gitlinks=False,
    )
    _validate_optional_gitlinks(world_repo, world_repo[manifest_only])

    mismatched = _world_commit(
        world_repo,
        None,
        heads,
        {"schema": "vcscore/transition/v2", "operation_id": "op-mismatched", "parent_worlds": []},
        gitlink_heads={"workspace": wrong_workspace_head, "session": session_head},
    )
    with pytest.raises(ValueError, match="disagrees with manifest"):
        _validate_optional_gitlinks(world_repo, world_repo[mismatched])

    extra = _world_commit(
        world_repo,
        None,
        heads,
        {"schema": "vcscore/transition/v2", "operation_id": "op-extra", "parent_worlds": []},
        gitlink_heads={"workspace": workspace_head, "session": session_head, "unexpected": session_head},
    )
    with pytest.raises(ValueError, match="unexpected gitlink"):
        _validate_optional_gitlinks(world_repo, world_repo[extra])


@pytest.mark.spike
def test_multi_resource_bindings_require_distinct_stores_or_explicit_alias_policy() -> None:
    oid_a = pygit2.Oid(hex="1" * 40)
    oid_b = pygit2.Oid(hex="2" * 40)

    shared_store_manifest = _world_snapshot(
        {"workspace_a": oid_a, "workspace_b": oid_b},
        store_ids={"workspace_a": "store_workspace_shared", "workspace_b": "store_workspace_shared"},
        resource_ids={"workspace_a": "fs:repo-a", "workspace_b": "fs:repo-b"},
    )
    with pytest.raises(ValueError, match="resource_id"):
        _validate_world_bindings(
            shared_store_manifest,
            {
                "store_workspace_shared": _store_identity(
                    store_id="store_workspace_shared",
                    resource_id="fs:repo-a",
                )
            },
        )

    aliased_manifest = _world_snapshot(
        {"session_main": oid_a, "session_alias": oid_b},
        store_ids={"session_main": "store_session", "session_alias": "store_session"},
        resource_ids={"session_main": "shepherd-session:shared", "session_alias": "shepherd-session:shared"},
    )
    identities = {
        "store_session": _store_identity(
            store_id="store_session",
            kind="shepherd.session_state",
            resource_id="shepherd-session:shared",
        )
    }
    with pytest.raises(ValueError, match="same-resource aliases"):
        _validate_world_bindings(aliased_manifest, identities)
    _validate_world_bindings(aliased_manifest, identities, allow_same_resource_alias=True)

    duplicate_store_manifest = _world_snapshot(
        {"session_main": oid_a, "session_alias": oid_b},
        store_ids={"session_main": "store_session_a", "session_alias": "store_session_b"},
        resource_ids={"session_main": "shepherd-session:shared", "session_alias": "shepherd-session:shared"},
    )
    with pytest.raises(ValueError, match="multiple substrate stores"):
        _validate_world_bindings(
            duplicate_store_manifest,
            {
                "store_session_a": _store_identity(
                    store_id="store_session_a",
                    kind="shepherd.session_state",
                    resource_id="shepherd-session:shared",
                ),
                "store_session_b": _store_identity(
                    store_id="store_session_b",
                    kind="shepherd.session_state",
                    resource_id="shepherd-session:shared",
                ),
            },
            allow_same_resource_alias=True,
        )


@pytest.mark.spike
def test_candidate_records_require_durable_operation_scoped_candidate_refs(tmp_path: Path) -> None:
    substrate_repo = pygit2.init_repository(str(tmp_path / "substrate.git"), bare=True)
    parent = _commit_json(substrate_repo, "refs/heads/main", {"label": "parent"})
    candidate = _commit_json(
        substrate_repo,
        _candidate_ref("op child/unsafe", "workspace/main"),
        {"label": "candidate"},
        parents=(parent,),
    )
    wrong = _commit_json(substrate_repo, "refs/heads/wrong", {"label": "wrong"})

    _validate_candidate_ref(
        substrate_repo,
        operation_id="op child/unsafe",
        binding="workspace/main",
        expected_head=candidate,
    )
    with pytest.raises(ValueError, match="disagrees"):
        _validate_candidate_ref(
            substrate_repo,
            operation_id="op child/unsafe",
            binding="workspace/main",
            expected_head=wrong,
        )
    with pytest.raises(ValueError, match="without a durable candidate ref"):
        _validate_candidate_ref(
            substrate_repo,
            operation_id="op missing",
            binding="workspace/main",
            expected_head=candidate,
        )


@pytest.mark.spike
def test_vcscore_canonical_digest_is_domain_separated_and_stable() -> None:
    left = {"schema": "example/v1", "payload": {"b": 2, "a": 1}}
    right = {"payload": {"a": 1, "b": 2}, "schema": "example/v1"}

    assert _canonical_bytes(left).startswith(CANONICAL_PREFIX)
    assert _canonical_digest(left) == _canonical_digest(right)
    assert _canonical_digest(left) != f"sha256:{hashlib.sha256(json.dumps(left, sort_keys=True).encode()).hexdigest()}"
    with pytest.raises(ValueError):
        _canonical_bytes({"bad": math.nan})


@pytest.mark.benchmark
@pytest.mark.spike
def test_ref_heavy_pin_scheme_has_an_opt_in_10k_scale_guard(tmp_path: Path) -> None:
    if os.environ.get("VCSCORE_RUN_REF_SCALE") != "1":
        pytest.skip("set VCSCORE_RUN_REF_SCALE=1 to run the 10,000-ref v2 scale guard")

    repo = pygit2.init_repository(str(tmp_path / "substrate.git"), bare=True)
    head = _commit_json(repo, "refs/heads/main", {"label": "payload"})
    fresh_resolution_seconds = _measure_ref_resolution(repo, "refs/heads/main")

    started = time.perf_counter()
    for index in range(10_000):
        _update_ref(repo, f"refs/vcscore/pins/world/store_world_test/w{index:05d}/binding", head)
    create_seconds = time.perf_counter() - started
    loose_resolution_seconds = _measure_ref_resolution(repo, "refs/heads/main")

    started = time.perf_counter()
    loose = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", "refs/vcscore/pins"],
        cwd=repo.path,
        capture_output=True,
        check=True,
        text=True,
    )
    loose_seconds = time.perf_counter() - started

    subprocess.run(["git", "pack-refs", "--all"], cwd=repo.path, capture_output=True, check=True, text=True)
    packed_resolution_seconds = _measure_ref_resolution(repo, "refs/heads/main")
    started = time.perf_counter()
    packed = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", "refs/vcscore/pins"],
        cwd=repo.path,
        capture_output=True,
        check=True,
        text=True,
    )
    packed_seconds = time.perf_counter() - started

    assert len(loose.stdout.splitlines()) == 10_000
    assert len(packed.stdout.splitlines()) == 10_000
    assert create_seconds < 30.0
    assert loose_seconds < 5.0
    assert packed_seconds < 1.0
    assert loose_resolution_seconds <= max(
        fresh_resolution_seconds * 2.0,
        fresh_resolution_seconds + 0.25,
    )
    assert packed_resolution_seconds <= max(
        fresh_resolution_seconds * 2.0,
        fresh_resolution_seconds + 0.25,
    )


def _init_reftable_repo_or_skip(tmp_path: Path) -> pygit2.Repository:
    repo_path = tmp_path / "reftable.git"
    try:
        result = subprocess.run(
            ["git", "init", "--bare", "--ref-format=reftable", str(repo_path)],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("git executable is not available for reftable capability check")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git init did not report a reason"
        pytest.skip(f"git does not support reftable init here: {detail}")
    try:
        return pygit2.Repository(str(repo_path))
    except pygit2.GitError as exc:
        pytest.skip(f"pygit2/libgit2 cannot open reftable repos here: {exc}")


def _exercise_ref_backend_api(repo: pygit2.Repository) -> None:
    first = _commit_json(repo, "refs/heads/main", {"label": "first"})
    second = _commit_json(repo, "refs/heads/second", {"label": "second"}, parents=(first,))
    runtime_ref = "refs/vcscore/runtime/frontiers/run-0001"

    _update_ref(repo, runtime_ref, first)
    assert repo.references[runtime_ref].target == first

    _update_ref(repo, runtime_ref, second)
    assert repo.references[runtime_ref].target == second

    runtime_refs = [ref for ref in repo.references if ref.startswith("refs/vcscore/runtime/frontiers/")]
    assert runtime_refs == [runtime_ref]

    repo.references.delete(runtime_ref)
    assert runtime_ref not in repo.references
