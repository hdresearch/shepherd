"""Regenerate `expected.batches` for the `-lite` positive corpus fixtures.

Run from anywhere:

    uv run python shepherd/packages/kernel-v3-reference/tests/conformance/v0_lite/regenerate.py

Rewrites each `positive/NN_name.json` fixture's `expected.batches` with the
canonical wire encoding of its full transition sequence (initial-run-prefix
plus one resume transition per observation), computed by
`test_corpus.collect_batches_wire(...)` — the same function the
byte-stability test compares against. Everything else in each fixture
(input, description, envelope_status, completed_value, ...) is preserved.

This is the regenerate-and-commit step: run it deliberately when a
projection or serializer change intentionally alters the canonical bytes,
then review the resulting fixture diff before committing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load_fixture
from test_corpus import collect_batches_wire


def regenerate() -> int:
    positive_dir = _HERE / "positive"
    count = 0
    for path in sorted(positive_dir.glob("*.json")):
        fixture = load_fixture(path)
        data = json.loads(path.read_text())
        expected = data.setdefault("expected", {})
        expected["batches"] = collect_batches_wire(fixture)
        expected.pop("batch", None)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        count += 1
        print(f"regenerated expected.batches for {path.name}")
    print(f"\n{count} fixture(s) regenerated.")
    return count


if __name__ == "__main__":
    regenerate()
