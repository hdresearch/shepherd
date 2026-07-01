"""Write a self-contained Trace Viewer HTML file for a trace payload."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shepherd_trace_viewer.durable_reader import read_trace_payload
from shepherd_trace_viewer.embed import write_static_html
from shepherd_trace_viewer.serde import from_json, to_json


def main(argv: list[str] | None = None) -> int:
    """Run the snapshot builder CLI."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        sys.stderr.write("usage: build_snapshot.py ASSETS DATASET OUT\n")
        return 2
    assets = Path(args[0])
    dataset = Path(args[1])
    out = Path(args[2])
    data = json.loads(dataset.read_text(encoding="utf-8"))
    view_json = to_json(from_json(data)) if data.get("schema_version") else to_json(read_trace_payload(data))
    write_static_html(view_json, out, assets_dir=assets)
    sys.stdout.write(f"wrote {out} ({out.stat().st_size:,} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
