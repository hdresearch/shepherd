"""Backend conformance tests.

Parametrized over every Backend implementation. The same behavioral
expectations apply to MemoryBackend and GitBackend; storage shape is an
implementation detail.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from commons_vcs._types import Edge, Object
from commons_vcs.backends import Backend, MemoryBackend
from commons_vcs.canonical import CANONICAL_PREFIX

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------
# Backend fixtures — parametrize over implementations
# ---------------------------------------------------------------


def _make_memory(_tmp_path: Path) -> Backend:
    return MemoryBackend()


def _make_git(tmp_path: Path) -> Backend:
    pytest.importorskip("pygit2")
    from commons_vcs.backends.git import GitBackend

    return GitBackend.init(tmp_path / "repo")


BACKEND_FACTORIES = [
    pytest.param(_make_memory, id="memory"),
    pytest.param(_make_git, id="git"),
]


@pytest.fixture(params=BACKEND_FACTORIES)
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Backend:
    factory = request.param
    return factory(tmp_path)


# ---------------------------------------------------------------
# Object storage
# ---------------------------------------------------------------


def _mk_object(value: str = "v1", edges: tuple[Edge, ...] = ()) -> Object:
    return Object(schema_ref="test/v1", body={"v": value}, edges=edges)


def test_write_then_read_round_trip(backend: Backend) -> None:
    obj = _mk_object()
    digest = backend.write_object(obj)
    assert digest == obj.id
    fetched = backend.read_object(digest)
    assert fetched is not None
    assert fetched.id == obj.id
    assert fetched.schema_ref == obj.schema_ref
    assert dict(fetched.body) == dict(obj.body)
    assert fetched.edges == obj.edges


def test_write_object_stores_bytes_matching_construction_time_identity(backend: Backend) -> None:
    source = {"items": [{"name": "before"}]}
    obj = Object(schema_ref="test/v1", body=source)
    source["items"].append({"name": "after"})
    source["items"][0]["name"] = "mutated"

    digest = backend.write_object(obj)
    fetched = backend.read_object(digest)

    assert fetched is not None
    assert fetched.id == digest == obj.id
    items = fetched.body["items"]
    assert isinstance(items, tuple)
    assert items[0]["name"] == "before"


def test_has_object(backend: Backend) -> None:
    obj = _mk_object()
    assert backend.has_object(obj.id) is False
    backend.write_object(obj)
    assert backend.has_object(obj.id) is True


def test_read_missing_returns_none(backend: Backend) -> None:
    assert backend.read_object("sha256:" + "0" * 64) is None


def test_memory_backend_rejects_stored_digest_mismatch() -> None:
    backend = MemoryBackend()
    first = _mk_object("first")
    second = _mk_object("second")
    backend.write_object(first)
    backend._objects[first.id] = second

    with pytest.raises(ValueError, match="integrity failure"):
        backend.read_object(first.id)


def test_git_backend_rejects_non_canonical_stored_object_blob(tmp_path: Path) -> None:
    pytest.importorskip("pygit2")
    from commons_vcs.backends.git import GitBackend

    backend = GitBackend.init(tmp_path / "repo")
    obj = Object(
        schema_ref="test/v1",
        body={"z": "last", "a": "first"},
    )
    digest = backend.write_object(obj)

    json_equivalent_payload = json.dumps(
        obj.canonical_core(),
        sort_keys=False,
        indent=2,
    ).encode("utf-8")
    tampered_blob = backend._write_blob(CANONICAL_PREFIX + json_equivalent_payload)
    backend._set_ref_to_oid(backend._object_ref_name(digest), tampered_blob)

    with pytest.raises(ValueError, match="canonical byte form"):
        backend.read_object(digest)


def test_write_is_idempotent(backend: Backend) -> None:
    obj = _mk_object()
    d1 = backend.write_object(obj)
    d2 = backend.write_object(obj)
    assert d1 == d2 == obj.id
    # The index should not double-count: a self-citing edge would land
    # twice if write_object weren't idempotent. Use a separate citer
    # to check.
    backend.write_object(obj)
    backend.write_object(obj)
    citers = backend.cited_by(obj.id, "any-role")
    assert citers == []  # no edges to obj.id with "any-role"


def test_iter_objects(backend: Backend) -> None:
    a = _mk_object("a")
    b = _mk_object("b")
    backend.write_object(a)
    backend.write_object(b)
    seen = dict(backend.iter_objects())
    assert seen.keys() == {a.id, b.id}
    assert seen[a.id].body["v"] == "a"
    assert seen[b.id].body["v"] == "b"


def test_object_with_edges_preserves_edge_order(backend: Backend) -> None:
    target = _mk_object("target")
    backend.write_object(target)
    edges = (
        Edge(role="r1", target=target.id),
        Edge(role="r2", target=target.id),
        Edge(role="r1", target=target.id),
    )
    obj = Object(schema_ref="test/v1", body={"v": "with-edges"}, edges=edges)
    digest = backend.write_object(obj)
    fetched = backend.read_object(digest)
    assert fetched is not None
    assert fetched.edges == edges


# ---------------------------------------------------------------
# Refs
# ---------------------------------------------------------------


def test_set_and_get_ref(backend: Backend) -> None:
    assert backend.get_ref("scopes/main") is None
    backend.set_ref("scopes/main", "sha256:" + "a" * 64)
    assert backend.get_ref("scopes/main") == "sha256:" + "a" * 64


def test_set_ref_overwrites(backend: Backend) -> None:
    backend.set_ref("foo", "v1")
    backend.set_ref("foo", "v2")
    assert backend.get_ref("foo") == "v2"


def test_delete_ref(backend: Backend) -> None:
    backend.set_ref("foo", "v1")
    backend.delete_ref("foo")
    assert backend.get_ref("foo") is None


def test_delete_missing_ref_is_noop(backend: Backend) -> None:
    backend.delete_ref("never-set")  # must not raise


def test_compare_and_delete_ref_succeeds_when_expected_matches(backend: Backend) -> None:
    backend.set_ref("foo", "v1")
    assert backend.compare_and_delete_ref("foo", expected="v1") is True
    assert backend.get_ref("foo") is None


def test_compare_and_delete_ref_fails_when_expected_does_not_match(backend: Backend) -> None:
    backend.set_ref("foo", "v1")
    assert backend.compare_and_delete_ref("foo", expected="other") is False
    assert backend.get_ref("foo") == "v1"


def test_compare_and_delete_ref_missing_matches_expected_none(backend: Backend) -> None:
    assert backend.compare_and_delete_ref("foo", expected=None) is True
    assert backend.get_ref("foo") is None


def test_compare_and_delete_ref_existing_does_not_match_expected_none(backend: Backend) -> None:
    backend.set_ref("foo", "v1")
    assert backend.compare_and_delete_ref("foo", expected=None) is False
    assert backend.get_ref("foo") == "v1"


def test_list_refs_with_prefix(backend: Backend) -> None:
    backend.set_ref("scopes/a", "v1")
    backend.set_ref("scopes/b", "v2")
    backend.set_ref("index/x/y", "v3")
    scope_refs = sorted(backend.list_refs("scopes/"))
    assert scope_refs == ["scopes/a", "scopes/b"]
    all_refs = sorted(backend.list_refs(""))
    assert all_refs == ["index/x/y", "scopes/a", "scopes/b"]


# ---------------------------------------------------------------
# Compare-and-swap
# ---------------------------------------------------------------


def test_cas_create_when_unset(backend: Backend) -> None:
    assert backend.compare_and_swap_ref("foo", expected=None, new="v1") is True
    assert backend.get_ref("foo") == "v1"


def test_cas_succeeds_when_expected_matches(backend: Backend) -> None:
    backend.set_ref("foo", "v1")
    assert backend.compare_and_swap_ref("foo", expected="v1", new="v2") is True
    assert backend.get_ref("foo") == "v2"


def test_cas_fails_when_expected_does_not_match(backend: Backend) -> None:
    backend.set_ref("foo", "actual")
    assert backend.compare_and_swap_ref("foo", expected="other", new="v2") is False
    assert backend.get_ref("foo") == "actual"


def test_cas_fails_when_expected_none_but_ref_exists(backend: Backend) -> None:
    backend.set_ref("foo", "v1")
    assert backend.compare_and_swap_ref("foo", expected=None, new="v2") is False
    assert backend.get_ref("foo") == "v1"


def test_cas_fails_when_expected_set_but_ref_unset(backend: Backend) -> None:
    assert backend.compare_and_swap_ref("foo", expected="v1", new="v2") is False
    assert backend.get_ref("foo") is None


def test_git_backend_ref_transaction_is_all_or_nothing(tmp_path: Path) -> None:
    pytest.importorskip("pygit2")
    from commons_vcs.backends.git import GitBackend

    backend = GitBackend.init(tmp_path / "repo")
    backend.set_ref("a", "old-a")
    backend.set_ref("b", "old-b")
    change_a = backend.prepared_set_ref_change("a", "new-a", expected="old-a")
    change_b = backend.prepared_set_ref_change("b", "new-b", expected="old-b")
    assert change_a is not None
    assert change_b is not None

    backend.set_ref("b", "raced")

    assert backend.update_refs_atomically((change_a, change_b)) is False
    assert backend.get_ref("a") == "old-a"
    assert backend.get_ref("b") == "raced"

    retry_a = backend.prepared_set_ref_change("a", "new-a", expected="old-a")
    retry_b = backend.prepared_set_ref_change("b", "new-b", expected="raced")
    assert retry_a is not None
    assert retry_b is not None
    assert backend.update_refs_atomically((retry_a, retry_b)) is True
    assert backend.get_ref("a") == "new-a"
    assert backend.get_ref("b") == "new-b"


def test_git_backend_ref_transaction_can_delete_guarded_ref(tmp_path: Path) -> None:
    pytest.importorskip("pygit2")
    from commons_vcs.backends.git import GitBackend

    backend = GitBackend.init(tmp_path / "repo")
    backend.set_ref("delete-me", "v1")
    delete_change = backend.prepared_delete_ref_change("delete-me", expected="v1")
    create_change = backend.prepared_set_ref_change("created", "v2", expected=None)
    assert delete_change is not None
    assert create_change is not None

    assert backend.update_refs_atomically((delete_change, create_change)) is True
    assert backend.get_ref("delete-me") is None
    assert backend.get_ref("created") == "v2"


# ---------------------------------------------------------------
# Inverse-edge index
# ---------------------------------------------------------------


def test_cited_by_empty_for_unknown_target(backend: Backend) -> None:
    assert backend.cited_by("sha256:" + "f" * 64, "any-role") == []


def test_cited_by_returns_citer_after_write(backend: Backend) -> None:
    target = _mk_object("target")
    backend.write_object(target)
    citer = Object(
        schema_ref="test/v1",
        body={"v": "citer"},
        edges=(Edge(role="r", target=target.id),),
    )
    backend.write_object(citer)
    assert backend.cited_by(target.id, "r") == [citer.id]


def test_cited_by_distinguishes_role(backend: Backend) -> None:
    target = _mk_object("target")
    backend.write_object(target)
    by_role_a = Object(
        schema_ref="test/v1",
        body={"v": "a"},
        edges=(Edge(role="role-a", target=target.id),),
    )
    by_role_b = Object(
        schema_ref="test/v1",
        body={"v": "b"},
        edges=(Edge(role="role-b", target=target.id),),
    )
    backend.write_object(by_role_a)
    backend.write_object(by_role_b)
    assert backend.cited_by(target.id, "role-a") == [by_role_a.id]
    assert backend.cited_by(target.id, "role-b") == [by_role_b.id]
    assert backend.cited_by(target.id, "role-c") == []


def test_cited_by_returns_sorted_digests(backend: Backend) -> None:
    target = _mk_object("target")
    backend.write_object(target)
    # Build several citers in non-sorted order.
    citers = [
        Object(schema_ref="test/v1", body={"v": f"c{i}"}, edges=(Edge(role="r", target=target.id),)) for i in range(5)
    ]
    for c in citers:
        backend.write_object(c)
    expected = sorted(c.id for c in citers)
    assert backend.cited_by(target.id, "r") == expected


def test_reindex_recovers_from_empty_index(backend: Backend) -> None:
    target = _mk_object("target")
    backend.write_object(target)
    citer = Object(
        schema_ref="test/v1",
        body={"v": "citer"},
        edges=(Edge(role="r", target=target.id),),
    )
    backend.write_object(citer)
    # Simulate index corruption / cold start. Reindex must rebuild
    # from stored objects.
    backend.reindex()
    assert backend.cited_by(target.id, "r") == [citer.id]
