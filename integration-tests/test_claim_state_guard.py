"""Focused tests for the lightweight launch-claim guard."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO / "scripts" / "check_claim_states.py"
P031_CHECKER_PATH = REPO / "scripts" / "check_spec_p031_markers.py"

spec = importlib.util.spec_from_file_location("check_claim_states", CHECKER_PATH)
assert spec is not None
check_claim_states = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = check_claim_states
spec.loader.exec_module(check_claim_states)

p031_spec = importlib.util.spec_from_file_location("check_spec_p031_markers", P031_CHECKER_PATH)
assert p031_spec is not None
check_spec_p031_markers = importlib.util.module_from_spec(p031_spec)
assert p031_spec.loader is not None
sys.modules[p031_spec.name] = check_spec_p031_markers
p031_spec.loader.exec_module(check_spec_p031_markers)


def _scan_text(tmp_path: Path, text: str) -> list[tuple[int, str]]:
    path = tmp_path / "claims.md"
    path.write_text(text, encoding="utf-8")
    return check_claim_states._scan(path)


def _scan_p031_text(tmp_path: Path, text: str) -> list[object]:
    path = tmp_path / "spec.md"
    path.write_text(text, encoding="utf-8")
    return list(check_spec_p031_markers.scan_path(path))


def test_release_claim_docs_are_active_state_guard_inputs() -> None:
    """Release-facing Path-A docs are covered by the claim-state guard."""
    assert {
        "docs/engineering/convergence/prelaunch-acceptance-checklist.md",
        "docs/engineering/convergence/path-a-release-notes.md",
    }.issubset(set(check_claim_states.DOCS))


def test_manifest_table_yes_for_deferred_surface_is_a_violation(tmp_path: Path) -> None:
    """A manifest yes-row for a deferred surface is an overclaim."""
    violations = _scan_text(
        tmp_path,
        "| Gap | Blocking for v1.0? | Tracking |\n"
        "|---|---|---|\n"
        "| `run.control` write-side verbs | yes | target work |\n",
    )
    assert violations == [(3, "| `run.control` write-side verbs | yes | target work |")]


def test_deferred_manifest_table_row_is_exempt(tmp_path: Path) -> None:
    """A manifest row with Path-A deferral language is allowed."""
    violations = _scan_text(
        tmp_path,
        "| Gap | Blocking for v1.0? | Tracking |\n"
        "|---|---|---|\n"
        "| `run.control` write-side verbs | **no — deferred (V1D-016, Path A)** | target work |\n",
    )
    assert violations == []


def test_v1_best_of_n_commitment_is_a_violation(tmp_path: Path) -> None:
    """A fresh v1 best-of-N commitment must not pass unmarked."""
    violations = _scan_text(
        tmp_path,
        "This remains the demonstrability spine of the v1 best-of-N commitment.\n",
    )
    assert violations == [(1, "This remains the demonstrability spine of the v1 best-of-N commitment.")]


def test_live_control_shipped_v1_claim_requires_deferral_marker(tmp_path: Path) -> None:
    """A fresh live-control v1 shipping claim must carry a deferral marker."""
    violations = _scan_text(
        tmp_path,
        "The run.control surface ships in v1 as the live supervision API.\n",
    )
    assert violations == [(1, "The run.control surface ships in v1 as the live supervision API.")]


def test_live_control_target_spec_claim_is_exempt(tmp_path: Path) -> None:
    """Target-spec / V1D-016 language keeps Path-B live control out of Path A."""
    violations = _scan_text(
        tmp_path,
        "The run.control surface is target-spec under Path A / V1D-016.\n",
    )
    assert violations == []


def test_p031_scanner_flags_unmarked_live_control_spec_section(tmp_path: Path) -> None:
    """A normative live-control spec section must carry a Path-A/marker."""
    findings = _scan_p031_text(
        tmp_path,
        "# Constructs\n\n"
        "## Run control\n\n"
        "The run.control surface exposes pause, resume, amend, queue_ask, respond, and uninstall.\n",
    )
    assert len(findings) == 1
    assert findings[0].heading == "Run control"
    assert findings[0].evidence == "run.control"


def test_p031_scanner_allows_marked_target_spec_section(tmp_path: Path) -> None:
    """A nearby Path-A/marker exempts the full local section."""
    findings = _scan_p031_text(
        tmp_path,
        "# Constructs\n\n"
        "## Run control\n\n"
        "> **PROVISIONAL []:** Target-spec under Path A / V1D-016.\n\n"
        "The run.control surface exposes pause, resume, amend, queue_ask, respond, and uninstall.\n",
    )
    assert findings == []


def test_p031_scanner_does_not_conflate_handle_terms(tmp_path: Path) -> None:
    """handle/change-set language is not the live-supervision surface."""
    findings = _scan_p031_text(
        tmp_path,
        "# Handle surface\n\n"
        "## handles\n\n"
        "The Handle, GitRepo, May[...], run.changeset, select, apply, release, and discard "
        "surface is tracked separately by V1D-015.\n",
    )
    assert findings == []


def test_p031_scanner_marker_does_not_leak_to_next_section(tmp_path: Path) -> None:
    """A marked section does not exempt a later unmarked section."""
    findings = _scan_p031_text(
        tmp_path,
        "# Spec\n\n"
        "## Marked run control\n\n"
        "> **PROVISIONAL []:** Target-spec under Path A.\n\n"
        "The run.control surface is described here.\n\n"
        "## Unmarked state\n\n"
        "The run.state accessor exposes live lifecycle state.\n",
    )
    assert len(findings) == 1
    assert findings[0].heading == "Unmarked state"
    assert findings[0].evidence == "run.state"


def test_p031_scanner_unrelated_provisional_marker_does_not_exempt_section(tmp_path: Path) -> None:
    """Only Path-A/specific markers exempt a live-control section."""
    findings = _scan_p031_text(
        tmp_path,
        "# Spec\n\n"
        "## Run control\n\n"
        "> **PROVISIONAL [Q-999]:** This unrelated provisional note tracks a naming question.\n\n"
        "The run.control surface exposes pause and resume.\n",
    )
    assert len(findings) == 1
    assert findings[0].heading == "Run control"
    assert findings[0].evidence == "run.control"


def test_p031_scanner_parent_marker_exempts_child_sections(tmp_path: Path) -> None:
    """A Path-A marker on a parent heading covers the local subtree."""
    findings = _scan_p031_text(
        tmp_path,
        "# Spec\n\n"
        "## Live control target-spec\n\n"
        "> **PROVISIONAL []:** Target-spec under Path A / V1D-016.\n\n"
        "### Pause\n\n"
        "The run.control.pause method parks the worker at a safe point.\n\n"
        "### State\n\n"
        "The run.state.lifecycle_state accessor exposes live state.\n",
    )
    assert findings == []


def test_p031_scanner_ignores_markdown_headings_inside_code_fences(tmp_path: Path) -> None:
    """A fenced Python comment does not break heading-marker inheritance."""
    findings = _scan_p031_text(
        tmp_path,
        "# Spec\n\n"
        "## Live control target-spec\n\n"
        "> **Target-spec ( / V1D-016, Path A):** Deferred live control.\n\n"
        "```python\n"
        "# This is a comment, not a markdown heading.\n"
        "value = 1\n"
        "```\n\n"
        "### State\n\n"
        "The run.state.lifecycle_state accessor exposes live state.\n",
    )
    assert findings == []


def test_p031_scanner_default_scope_excludes_history_appendices(tmp_path: Path) -> None:
    """Default dry-run scope is the normative body plus grammar, not every appendix."""
    spec_root = tmp_path / "spec"
    spec_root.mkdir()
    (spec_root / "01-data-model.md").write_text(
        "# Data model\n\n## Run state\n\nThe run.state accessor exposes live state.\n",
        encoding="utf-8",
    )
    (spec_root / "appendix-f-open-questions.md").write_text(
        "# Questions\n\n## Old run control question\n\nThe run.control surface is discussed here.\n",
        encoding="utf-8",
    )

    default_findings = check_spec_p031_markers.scan_spec_tree(spec_root)
    all_findings = check_spec_p031_markers.scan_spec_tree(spec_root, all_spec_files=True)

    assert [finding.path.name for finding in default_findings] == ["01-data-model.md"]
    assert [finding.path.name for finding in all_findings] == [
        "01-data-model.md",
        "appendix-f-open-questions.md",
    ]
