"""Self-contained HTML rendering helpers for notebooks and static snapshots."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shepherd_trace_viewer.server import ASSETS_DIR

if TYPE_CHECKING:
    from collections.abc import Mapping


def render_static_html(view_json: Mapping[str, Any], *, assets_dir: str | Path = ASSETS_DIR) -> str:
    """Return a self-contained Trace Viewer HTML document for one ViewModel."""
    assets = Path(assets_dir)
    index = (assets / "index.html").read_text(encoding="utf-8")
    css = (assets / "viewer.css").read_text(encoding="utf-8")
    fonts_css = _inline_fonts((assets / "fonts.css").read_text(encoding="utf-8"), assets)
    viewer = (assets / "viewer.js").read_text(encoding="utf-8")
    vendor = "\n".join(
        f"<script>{(assets / 'vendor' / name).read_text(encoding='utf-8')}</script>"
        for name in ("cytoscape.min.js", "dagre.min.js", "cytoscape-dagre.js")
    )
    trace_literal = json.dumps(dict(view_json)).replace("</", "<\\/")

    html = index
    html = html.replace('<link rel="stylesheet" href="/assets/fonts.css" />', f"<style>{fonts_css}</style>")
    html = html.replace('<link rel="stylesheet" href="/assets/viewer.css" />', f"<style>{css}</style>")
    html = html.replace('<script src="/assets/vendor/cytoscape.min.js"></script>', "")
    html = html.replace('<script src="/assets/vendor/dagre.min.js"></script>', "")
    html = html.replace('<script src="/assets/vendor/cytoscape-dagre.js"></script>', vendor)
    return html.replace(
        '<script src="/assets/viewer.js"></script>',
        f"<script>window.__TRACE__ = {trace_literal};</script>\n<script>{viewer}</script>",
    )


def write_static_html(view_json: Mapping[str, Any], out: str | Path, *, assets_dir: str | Path = ASSETS_DIR) -> Path:
    """Write a self-contained Trace Viewer HTML document and return its path."""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_static_html(view_json, assets_dir=assets_dir), encoding="utf-8")
    return path


def _inline_fonts(fonts_css: str, assets: Path) -> str:
    def embed(match: re.Match[str]) -> str:
        woff2 = (assets / match.group(1)).read_bytes()
        return f"url('data:font/woff2;base64,{base64.b64encode(woff2).decode()}')"

    return re.sub(r"url\('\./([^']+\.woff2)'\)", embed, fonts_css)
