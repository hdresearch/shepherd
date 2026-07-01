"""Static HTML embedding helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from shepherd_trace_viewer.durable_reader import read_trace_payload_file
from shepherd_trace_viewer.embed import render_static_html
from shepherd_trace_viewer.serde import to_json

FIXTURE = Path(__file__).parent / "fixtures" / "durable-basic.trace.json"


def test_render_static_html_embeds_trace_without_network_fetch() -> None:
    html = render_static_html(to_json(read_trace_payload_file(FIXTURE)))

    assert "window.__TRACE__" in html
    assert '<script src="/assets/viewer.js"></script>' not in html


def test_render_static_html_opens_from_file_url(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    html_path = tmp_path / "trace.html"
    html_path.write_text(render_static_html(to_json(read_trace_payload_file(FIXTURE))), encoding="utf-8")

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_function("window.CY && window.CY.nodes('.event').length > 0")
        event_count = page.evaluate("() => window.CY.nodes('.event').length")
        browser.close()

    assert event_count > 0
