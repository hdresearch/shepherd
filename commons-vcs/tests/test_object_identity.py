from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from commons_vcs import Edge, Object


@dataclass(frozen=True)
class _Record:
    value: str


def test_object_body_is_deeply_detached_and_immutable() -> None:
    original = {"items": [{"name": "before"}]}
    obj = Object(schema_ref="example/v1", body=original)
    original["items"].append({"name": "after"})
    original["items"][0]["name"] = "mutated"

    items = obj.body["items"]
    assert isinstance(items, tuple)
    assert items[0]["name"] == "before"
    with pytest.raises(TypeError):
        obj.body["items"][0]["name"] = "forbidden"  # type: ignore[index]


def test_object_edges_are_detached_from_caller_list() -> None:
    target = "sha256:" + "0" * 64
    extra = "sha256:" + "1" * 64
    edges = [Edge(role="parent", target=target)]
    obj = Object(schema_ref="example/v1", body={}, edges=edges)  # type: ignore[arg-type]
    original_id = obj.id

    edges.append(Edge(role="extra", target=extra))

    assert obj.id == original_id
    assert obj.edges == (Edge(role="parent", target=target),)
    assert obj.canonical_core()["edges"] == [{"role": "parent", "target": target}]


def test_canonical_core_returns_detached_json_primitives() -> None:
    obj = Object(schema_ref="example/v1", body={"items": [{"name": "before"}]})
    core = obj.canonical_core()
    core["body"]["items"][0]["name"] = "mutated"

    assert obj.canonical_core()["body"]["items"][0]["name"] == "before"
    items = obj.body["items"]
    assert isinstance(items, tuple)
    assert items[0]["name"] == "before"


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        ("a", "b"),
        b"bytes",
        bytearray(b"bytes"),
        {"values": {1, 2}},
        {1: "non-string key"},
        _Record("x"),
    ],
)
def test_object_rejects_non_json_native_body_values(value: Any) -> None:
    with pytest.raises(TypeError):
        Object(schema_ref="example/v1", body={"value": value})


@pytest.mark.parametrize("schema_ref", ["", 123, None])
def test_object_rejects_invalid_schema_ref(schema_ref: object) -> None:
    with pytest.raises(TypeError):
        Object(schema_ref=schema_ref, body={})  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ["", 123, None])
def test_edge_rejects_invalid_role(role: object) -> None:
    with pytest.raises(TypeError):
        Edge(role=role, target="sha256:" + "0" * 64)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "target",
    [
        "",
        123,
        None,
        "sha256:" + "0" * 63,
        "sha256:" + "0" * 65,
        "sha256:" + "A" * 64,
        "sha1:" + "0" * 40,
        "not-a-digest",
    ],
)
def test_edge_rejects_invalid_target(target: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Edge(role="parent", target=target)  # type: ignore[arg-type]


def test_object_rejects_non_edge_values_in_edges() -> None:
    with pytest.raises(TypeError):
        Object(schema_ref="example/v1", body={}, edges=("not-an-edge",))  # type: ignore[arg-type]
