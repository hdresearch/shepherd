"""Server + CLI smoke tests."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import jsonschema
import pytest
from shepherd_trace_viewer.cli import build_parser
from shepherd_trace_viewer.durable_reader import read_trace_payload_file
from shepherd_trace_viewer.schema import build_json_schema
from shepherd_trace_viewer.serde import to_json
from shepherd_trace_viewer.server import make_server

FIXTURE = Path(__file__).parent / "fixtures" / "durable-basic.trace.json"


@pytest.fixture
def running_server():
    view = to_json(read_trace_payload_file(FIXTURE))
    httpd = make_server(view, bind="127.0.0.1", port=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read(), resp.headers.get("Content-Type", "")


def test_api_trace_returns_valid_viewmodel(running_server: str) -> None:
    status, body, ctype = _get(f"{running_server}/api/trace")
    assert status == 200
    assert "application/json" in ctype
    data = json.loads(body)
    jsonschema.validate(data, build_json_schema())
    assert data["schema_version"] == "shepherd.trace-view.v3"
    assert data["nodes"]


def test_index_served(running_server: str) -> None:
    status, body, ctype = _get(f"{running_server}/")
    assert status == 200
    assert "text/html" in ctype
    assert b"<html" in body.lower() or b"<!doctype" in body.lower()


def test_vendored_assets_served(running_server: str) -> None:
    status, body, ctype = _get(f"{running_server}/assets/vendor/cytoscape.min.js")
    assert status == 200
    assert "javascript" in ctype
    assert len(body) > 1000


def test_path_traversal_blocked(running_server: str) -> None:
    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(f"{running_server}/assets/../../../../etc/passwd")
    assert exc.value.code in (403, 404)


def test_cli_parser_requires_a_source() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["serve"])
