"""Doc-consistency gates, mechanized — the doc-closure sweep's W7 checks as machinery.

The 2026-06-10 doc-closure sweep (`260610-1447-doc-closure-plan.md`) ran these
checks by hand at its W7 gate; landing them here means orientation-layer drift
is caught between sweeps instead of accumulating until the next dedicated
execplan. The sweep's §4 "newcomer test" (a fresh reader reaches the true
current state with zero contradictions) stays human — these gates cover its
mechanical substrate: links resolve, and phrases a sweep retired stay retired.

The 7 anchor breaks the sweep verified pre-existing (territory) were
fixed when this gate landed, so the link-audit gates assert zero, not a
baseline count.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LINK_AUDIT = REPO / "docs" / "tools" / "link-audit.py"


def _real_breaks(target: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(LINK_AUDIT), str(REPO / target)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    match = re.search(r"REAL BREAKS:\s*(\d+)", proc.stdout)
    assert match, f"link-audit output had no REAL BREAKS summary:\n{proc.stdout}"
    return int(match.group(1)), proc.stdout


@pytest.mark.parametrize("target", ["docs/engineering", "docs/spec"])
def test_link_audit_zero_real_breaks(target: str) -> None:
    """Every markdown link under the audited tree resolves (file + anchor)."""
    breaks, out = _real_breaks(target)
    assert breaks == 0, f"link-audit found real breaks under {target}:\n{out}"


#: Phrases a closure sweep retired must stay retired. Each row pins the file
#: the phrase lived in (scoped — never a tree-wide grep, so quoting a phrase
#: in a plan or this file never trips it), the phrase, and the sweep item
#: that removed it. Extend this table on each future closure sweep.
RETIRED_PHRASES: list[tuple[str, str, str]] = [
    ("docs/engineering/v1.0-roadmap.md", "work not yet started", "doc-closure W1"),
    ("docs/engineering/v1.0-roadmap.md", "0/10 boxes green", "doc-closure W1"),
    ("docs/engineering/v1.0-roadmap.md", "will be re-drawn", "doc-closure W1"),
    ("docs/engineering/v1.0-roadmap.md", "forthcoming migration execplan", "doc-closure W1"),
    ("integration-tests/test_d2_boundary.py", "Phase 0b B10", "doc-closure W4"),
    ("skeleton/README.md", "18/18", "doc-closure W6"),
    ("vcs-core/packages/core/src/vcs_core/vcscore.py", "archive_losers", "candidate-evidence boundary"),
    (
        "vcs-core/packages/core/src/vcs_core/_retained_output_selection.py",
        "archive_losers",
        "candidate-evidence boundary",
    ),
    (
        "the design proposals",
        "release/discard receipts, ratified boundary verbs, and N-candidate settlement remain build gates",
        "Capability-C status repair",
    ),
    (
        "the design proposals",
        "Generalized retained-world materialization/read APIs, release/discard receipts, and N-candidate settlement remain build gates",
        "Capability-C status repair",
    ),
    (
        "260614-2100-capability-c-seal-and-select-build-plan.md",
        "and N-candidate settlement.",
        "Capability-C status repair",
    ),
]


def test_retired_phrases_stay_retired() -> None:
    """Phrases a closure sweep removed must not silently return to their files."""
    offenders = [
        f"{rel}: {phrase!r} returned (retired by {sweep})"
        for rel, phrase, sweep in RETIRED_PHRASES
        if phrase in (REPO / rel).read_text()
    ]
    assert offenders == [], offenders
