"""Browser-level layout checks for semantic trace shapes."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import pytest
from shepherd_trace_viewer.server import make_server

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def layout_server() -> Iterator[str]:
    view = _replay_layout_model()
    httpd = make_server(view, bind="127.0.0.1", port=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_replay_child_lane_is_anchored_below_replay_point(layout_server: str) -> None:
    playwright = pytest.importorskip("playwright.sync_api")

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(layout_server, wait_until="networkidle")
        page.wait_for_function("window.CY && window.CY.getElementById('child-start').length > 0")
        positions = page.evaluate(
            """() => ({
                replay: window.CY.getElementById("replay").position(),
                childStart: window.CY.getElementById("child-start").position(),
            })"""
        )
        browser.close()

    replay = positions["replay"]
    child_start = positions["childStart"]

    assert child_start["y"] > replay["y"]
    assert child_start["x"] > replay["x"]
    assert child_start["x"] - replay["x"] < 250


def _minimal_view() -> dict[str, Any]:
    return {
        "schema_version": "shepherd.trace-view.v3",
        "source": {
            "trace_runtime": "test",
            "trace_owner_id": "parent",
            "frontier_id": "frontier:test",
            "source_kind": "trace_store_slice",
        },
        "run": {"id": "test", "summary": {"events": 1, "lanes": 1}},
        "lanes": [{"id": "parent", "label": "parent", "node_ids": ["root"]}],
        "nodes": [_node("root", "parent", 0, "root")],
        "edges": [],
        "resources": [],
    }


def _replay_layout_model() -> dict[str, Any]:
    return {
        **_minimal_view(),
        "run": {"id": "replay-test", "summary": {"events": 6, "lanes": 2}},
        # Child first is intentional: replay layout should still place it below
        # the parent lane that owns the replay relation.
        "lanes": [
            {"id": "child", "label": "child", "node_ids": ["child-start", "child-done"]},
            {"id": "parent", "label": "parent", "node_ids": ["checkpoint", "revert", "replay", "after"]},
        ],
        "nodes": [
            _node("checkpoint", "parent", 0, "checkpoint"),
            _node("revert", "parent", 10, "revert"),
            _node("replay", "parent", 11, "replay"),
            _node("after", "parent", 12, "after"),
            _node("child-start", "child", 95, "started"),
            _node("child-done", "child", 96, "completed"),
        ],
        "edges": [
            {"id": "basis", "kind": "replay_basis", "source": "checkpoint", "target": "replay", "label": "basis"},
            {"id": "control", "kind": "replay_control", "source": "revert", "target": "replay", "label": "replay"},
            {"id": "branch", "kind": "causal", "source": "replay", "target": "child-start", "label": "causal"},
            {"id": "join", "kind": "causal", "source": "child-done", "target": "after", "label": "causal"},
        ],
    }


def _node(node_id: str, lane_id: str, sequence: int, label: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": "fact",
        "family": "execution",
        "role": "record",
        "lane_ids": [lane_id],
        "sequence": sequence,
        "label": label,
        "payload": {},
        "body": {},
    }
