"""Smoke tests for checked-in examples."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples" / "workspace-handles"


def test_best_of_n_example_runs_as_script() -> None:
    """The best-of-N example is executable and reports explicit settlement."""
    summary = _run_example(EXAMPLES / "best_of_n.py")

    assert summary["example"] == "workspace-handles.best_of_n"
    assert summary["settlements"] == {
        "discarded": "discarded",
        "released": "released",
        "selected": "selected",
    }
    assert summary["winner"]["state"] == "selected"
    assert "winner" in summary["winner"]["text"]
    assert {loser["state"] for loser in summary["losers"]} == {"released", "discarded"}
    assert {"world_oid", "store_id", "resource_id", "head"} <= set(summary["selected_basis"])


def test_retry_until_acceptable_example_runs_as_script() -> None:
    """The retry example is executable and reports explicit settlement."""
    summary = _run_example(EXAMPLES / "retry_until_acceptable.py")

    assert summary["example"] == "workspace-handles.retry_until_acceptable"
    assert summary["settlements"] == {
        "released": "released",
        "selected": "selected",
    }
    assert summary["rejected"]["state"] == "released"
    assert summary["rejected"]["text"] == "10:first:rejected\n"
    assert summary["accepted"]["state"] == "selected"
    assert summary["accepted"]["text"] == "90:second:accepted\n"
    assert {"world_oid", "store_id", "resource_id", "head"} <= set(summary["selected_basis"])


def _run_example(script: Path) -> dict[str, object]:
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    value = json.loads(proc.stdout)
    assert isinstance(value, dict)
    return value
