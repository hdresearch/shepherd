"""Regression coverage for goal evidence tooling."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO / "scripts" / "check_goal_evidence.py"
RUNNER_PATH = REPO / "scripts" / "run_goal_evidence.py"
RENDERER_PATH = REPO / "scripts" / "render_goal_prompt.py"
GOALS_DIR = REPO / "docs" / "engineering" / "convergence" / "goals"
REAL_DEMO = REPO / "spikes" / "260610-real-sdk-demo" / "run_demo.py"
GITREPO_CONTRACT = (
    REPO / "shepherd" / "packages" / "runtime" / "tests" / "unit" / "nucleus" / ("test_git_repo_handle_contract.py")
)

sys.path.insert(0, str(REPO / "scripts"))
spec = importlib.util.spec_from_file_location("check_goal_evidence", CHECKER_PATH)
assert spec is not None
check_goal_evidence = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = check_goal_evidence
spec.loader.exec_module(check_goal_evidence)

runner_spec = importlib.util.spec_from_file_location("run_goal_evidence", RUNNER_PATH)
assert runner_spec is not None
run_goal_evidence = importlib.util.module_from_spec(runner_spec)
assert runner_spec.loader is not None
sys.modules[runner_spec.name] = run_goal_evidence
runner_spec.loader.exec_module(run_goal_evidence)

renderer_spec = importlib.util.spec_from_file_location("render_goal_prompt", RENDERER_PATH)
assert renderer_spec is not None
render_goal_prompt = importlib.util.module_from_spec(renderer_spec)
assert renderer_spec.loader is not None
sys.modules[renderer_spec.name] = render_goal_prompt
renderer_spec.loader.exec_module(render_goal_prompt)


def test_goal_evidence_manifest_is_valid() -> None:
    """The checked-in Wave 0/1 manifest satisfies the schema guard."""
    data = check_goal_evidence.load_manifest()

    assert check_goal_evidence.validate_manifest(data) == []
    assert data["index_file"] == "docs/engineering/convergence/goals/index.toml"
    assert len(data["goals"]) == 9

    g01 = next(goal for goal in data["goals"] if goal["id"] == "g01")
    assert g01["_goal_file"].endswith("g01-transition-scope-lock.toml")
    assert g01["title"] == "Transition Scope Lock"
    assert g01["wave"] == "wave0"
    assert g01["class"] == "independent-completion"
    assert "prompt" not in g01
    assert g01["_prompt_file"].endswith("g01-transition-scope-lock.md")
    assert g01["_prompt_text"].startswith("/goal Produce a documentation-only transition scope lock")
    assert g01["claims"][0]["surface"] == "docs.transition_scope_lock"
    assert g01["claims"][0]["claim_intent"] == "hardening"
    assert "goal_contracts" in g01["claims"][0]["evidence"]

    g02 = next(goal for goal in data["goals"] if goal["id"] == "g02")
    match_claim = next(claim for claim in g02["claims"] if claim["surface"] == "authority.match_plan.structural_core")

    assert g02["title"] == "Match/Plan Authority Baseline Capture"
    assert match_claim["claim_intent"] == "baseline-capture"
    assert match_claim["promoted"] is False

    g05 = next(goal for goal in data["goals"] if goal["id"] == "g05")
    retained_claim = next(claim for claim in g05["claims"] if claim["surface"] == "vcscore.retained_output_custody_spi")

    assert retained_claim["promoted"] is True
    assert retained_claim["evidence"] == ["retained_output_spi", "retained_output_witness", "baseline"]

    g07 = next(goal for goal in data["goals"] if goal["id"] == "g07")
    broker_claim = next(claim for claim in g07["claims"] if claim["surface"] == "egress.broker.core")
    platform_claim = next(claim for claim in g07["claims"] if claim["surface"] == "egress.native_jail.platform")

    assert broker_claim["promoted"] is True
    assert broker_claim["claim_intent"] == "promotion"
    assert broker_claim["evidence"] == ["broker_and_containment", "broker_witness"]
    assert platform_claim["promoted"] is False

    g09 = next(goal for goal in data["goals"] if goal["id"] == "g09")
    assert g09["completion_mode"] == "scaffold"
    assert g09["claims"][0]["surface"] == "runtime.durable_child_backend"


def test_index_references_one_file_per_goal() -> None:
    """Evidence contracts and hand-authored prompt briefs are split per goal."""
    data = check_goal_evidence.load_manifest()
    indexed = data["_index_goal_files"]
    loaded = {Path(goal["_goal_file"]).name for goal in data["goals"]}
    prompts = {Path(goal["_prompt_file"]).name for goal in data["goals"]}

    assert indexed == [
        "g01-transition-scope-lock.toml",
        "g02-match-plan-authority-core.toml",
        "g03-runoutput-schema-import-contract.toml",
        "g04-skeleton-bridge-retirement-audit.toml",
        "g05-vcscore-retained-output-custody-spi.toml",
        "g06-pure-gitrepo-handle-value-noun.toml",
        "g07-irreversible-egress-broker-productization.toml",
        "g08-real-provider-evidence-harness.toml",
        "g09-durable-child-runtime-backend-scaffold.toml",
    ]
    assert loaded == set(indexed)
    assert prompts == {Path(name).with_suffix(".md").name for name in indexed}
    assert not (GOALS_DIR.parent / "goal-evidence.toml").exists()


def test_active_goal_prompts_are_copy_pastable_and_packet_backed() -> None:
    """Every active goal prompt is self-contained and under the Codex goal limit."""
    data = check_goal_evidence.load_manifest()

    for goal in data["goals"]:
        prompt = check_goal_evidence.render_goal_prompt(goal)
        command = f"make goal-evidence GOAL={goal['id']}"

        assert prompt.startswith("/goal ")
        assert command in prompt
        assert "GOAL=gNN" not in prompt
        assert "gNN" not in prompt
        assert len(prompt) <= check_goal_evidence.GOAL_PROMPT_LIMIT
        for heading in ("Goal", "Boundaries", "Required Evidence", "Completion Bar", "Stop Conditions", "Nonclaims"):
            assert f"## {heading}" in prompt
        assert "machine source" not in prompt
        assert "Completing this unblocks" not in prompt
        assert "Completion requires summarizing" in prompt
        if goal["completion_mode"] == "scaffold":
            assert "xfail" in prompt.lower()


def test_manifest_rejects_missing_prompt_markdown() -> None:
    """A goal without hand-authored Markdown cannot be scheduled as a Codex goal."""
    data = deepcopy(check_goal_evidence.load_manifest())
    g03 = next(goal for goal in data["goals"] if goal["id"] == "g03")
    del g03["_prompt_text"]

    errors = check_goal_evidence.validate_manifest(data)

    assert any("g03: prompt markdown must be a non-empty string" in error for error in errors)


def test_manifest_rejects_overlong_rendered_prompt() -> None:
    """Rendered prompts must fit the documented Codex goal-size budget."""
    data = deepcopy(check_goal_evidence.load_manifest())
    g03 = next(goal for goal in data["goals"] if goal["id"] == "g03")
    g03["_prompt_text"] = "/goal " + ("x" * check_goal_evidence.GOAL_PROMPT_LIMIT)

    errors = check_goal_evidence.validate_manifest(data)

    assert any("rendered prompt must be <=" in error for error in errors)


def test_render_goal_prompt_cli_outputs_single_prompt() -> None:
    """The renderer emits a copy-pastable prompt for one active goal."""
    proc = subprocess.run(
        [sys.executable, str(RENDERER_PATH), "g05"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    assert proc.stdout.startswith("/goal Freeze the internal vcs-core retained-output custody SPI")
    assert "## Completion Bar" in proc.stdout
    assert "vcscore.retained_output_custody_spi" in proc.stdout
    assert "make goal-evidence GOAL=g05" in proc.stdout
    assert "GOAL=gNN" not in proc.stdout


def test_render_goal_prompt_cli_outputs_all_active_prompts() -> None:
    """The all-goals renderer makes every active prompt discoverable."""
    proc = subprocess.run(
        [sys.executable, str(RENDERER_PATH), "--all", "--show-lengths"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "## g01 — Transition Scope Lock" in proc.stdout
    assert "## g09 — Durable Child Runtime Backend Scaffold" in proc.stdout
    assert "make goal-evidence GOAL=g01" in proc.stdout
    assert "make goal-evidence GOAL=g09" in proc.stdout
    assert f"/ {check_goal_evidence.GOAL_PROMPT_LIMIT}" in proc.stdout


def test_render_goal_prompt_cli_writes_all_prompts_to_directory(tmp_path: Path) -> None:
    """The renderer can write inspection files under a temporary directory."""
    out_dir = tmp_path / "goal-prompts"

    proc = subprocess.run(
        [sys.executable, str(RENDERER_PATH), "--all", "--out-dir", str(out_dir)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    assert str(out_dir) in proc.stdout
    files = sorted(out_dir.glob("g*.goal.txt"))
    assert len(files) == 9
    assert (out_dir / "README.md").exists()
    assert files[0].name.startswith("g01-")
    assert files[-1].name.startswith("g09-")
    assert files[0].read_text(encoding="utf-8").startswith("/goal ")
    assert "make goal-evidence GOAL=g01" in files[0].read_text(encoding="utf-8")


def test_manifest_rejects_promoted_claim_that_depends_on_skippable_evidence() -> None:
    """Skipped evidence can support only non-promoted subclaims."""
    data = deepcopy(check_goal_evidence.load_manifest())
    g07 = next(goal for goal in data["goals"] if goal["id"] == "g07")
    platform_claim = next(claim for claim in g07["claims"] if claim["surface"] == "egress.native_jail.platform")
    platform_claim["promoted"] = True

    errors = check_goal_evidence.validate_manifest(data)

    assert any(
        "promoted claim evidence command 'may_lowering_spike' may not accept skipped evidence" in error
        for error in errors
    )


def test_manifest_rejects_baseline_capture_promotion() -> None:
    """Baseline-capture packets can record current evidence but cannot move claims."""
    data = deepcopy(check_goal_evidence.load_manifest())
    g02 = next(goal for goal in data["goals"] if goal["id"] == "g02")
    claim = next(claim for claim in g02["claims"] if claim["surface"] == "authority.match_plan.structural_core")
    claim["promoted"] = True

    errors = check_goal_evidence.validate_manifest(data)

    assert any("baseline-capture claims must not be promoted" in error for error in errors)


def test_manifest_rejects_unknown_dependency() -> None:
    """Per-goal dependencies must name loaded goals."""
    data = deepcopy(check_goal_evidence.load_manifest())
    g02 = next(goal for goal in data["goals"] if goal["id"] == "g02")
    g02["depends_on"] = ["g99"]

    errors = check_goal_evidence.validate_manifest(data)

    assert any("g02: depends_on references unknown goal(s): g99" in error for error in errors)


def test_goal_evidence_runner_lists_wave_zero_and_one_goals() -> None:
    """The runner advertises only the scoped pre-work goal ids."""
    proc = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--list"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "g01: Transition Scope Lock" in proc.stdout
    assert "g08: Real-Provider Evidence Harness" in proc.stdout
    assert "g06: Pure GitRepo Handle Value Noun" in proc.stdout
    assert "g09: Durable Child Runtime Backend Scaffold" in proc.stdout


def test_witness_expectations_support_domain_backpressure() -> None:
    """Witness validation checks more than required/equality fields."""
    witness = {
        "schema_version": 1,
        "status": "passed",
        "witness_kind": "demo",
        "total": 2,
        "passed": 2,
        "ok": True,
        "items": ["selected", "released", "discarded"],
    }
    ok, detail = run_goal_evidence._witness_matches_expectation(
        witness,
        {
            "required": ["schema_version", "status", "witness_kind"],
            "equals": {"schema_version": 1},
            "truthy": ["ok"],
            "at_least": {"total": 2},
            "allowed_values": {"status": ["passed", "skipped"]},
            "contains": {"items": ["selected", "discarded"]},
            "equal_fields": [["passed", "total"]],
            "if_equals": [{"field": "status", "value": "passed", "at_least": {"total": 1}}],
        },
    )

    assert ok is True
    assert detail is None


def test_witness_expectations_fail_numeric_and_conditional_mismatches() -> None:
    """Positive real-provider reports cannot pass with zero checks."""
    witness = {
        "schema_version": 1,
        "status": "passed",
        "witness_kind": "demo",
        "total": 0,
        "passed": 0,
    }

    ok, detail = run_goal_evidence._witness_matches_expectation(
        witness,
        {
            "required": ["schema_version", "status", "witness_kind"],
            "equals": {"schema_version": 1},
            "if_equals": [{"field": "status", "value": "passed", "at_least": {"total": 1}}],
        },
    )

    assert ok is False
    assert "expected >=" in str(detail)


def test_real_provider_demo_writes_credentialless_skip_report(tmp_path: Path) -> None:
    """Credentialless real-provider runs write a machine-readable skip report."""
    report = tmp_path / "real-provider.json"
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    proc = subprocess.run(
        [sys.executable, str(REAL_DEMO), "--json-report", str(report)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(report.read_text(encoding="utf-8"))

    assert "SKIP:" in proc.stdout
    assert payload["schema_version"] == 1
    assert payload["witness_kind"] == "real_provider_harness"
    assert payload["status"] == "skipped"
    assert payload["evidence_level"] == "credentialless_or_platform_skip"
    assert payload["provider"] == "claude-cli"
    assert payload["total"] == 0


def test_g08_runner_validates_credentialless_witness(tmp_path: Path) -> None:
    """The G08 packet path accepts a credentialless skip only as no-promotion evidence."""
    out_dir = tmp_path / "g08"
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    subprocess.run(
        [sys.executable, str(RUNNER_PATH), "g08", "--out", str(out_dir)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    packet = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    command = packet["commands"][0]

    assert packet["packet_status"] == "passed"
    assert all(claim["promoted"] is False for claim in packet["goal"]["claims"])
    assert command["json_report_status"] == "skipped"
    assert command["witness_status"] == "passed"
    assert command["witness_kind"] == "real_provider_harness"


def test_git_repo_contract_does_not_mask_transitive_import_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """The G06 contract does not hide import failures behind scaffold xfails."""
    spec = importlib.util.spec_from_file_location("gitrepo_contract", GITREPO_CONTRACT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    def fail_transitive_import(name: str) -> object:
        assert name == module.TARGET_MODULE
        raise ModuleNotFoundError("No module named transitive_dep", name="transitive_dep")

    monkeypatch.setattr(module.importlib, "import_module", fail_transitive_import)

    with pytest.raises(ModuleNotFoundError):
        module._git_repo_type()


def test_git_repo_contract_fails_for_missing_target_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """The promoted G06 contract fails normally when GitRepo is missing."""
    spec = importlib.util.spec_from_file_location("gitrepo_contract", GITREPO_CONTRACT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class MissingTargetSymbol:
        pass

    class BrokenModule:
        def __getattr__(self, name: str) -> object:
            assert name == "GitRepo"
            raise AttributeError("broken dependent attribute", name="not_GitRepo")

    monkeypatch.setattr(module.importlib, "import_module", lambda name: MissingTargetSymbol())
    with pytest.raises(AttributeError):
        module._git_repo_type()

    monkeypatch.setattr(module.importlib, "import_module", lambda name: BrokenModule())
    with pytest.raises(AttributeError):
        module._git_repo_type()


def test_runner_fails_closed_when_workspace_metadata_capture_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evidence packets should not silently pass when jj metadata is unavailable."""
    proc = subprocess.CompletedProcess(["jj", "st"], 2, stdout="", stderr="jj unavailable")
    monkeypatch.setattr(run_goal_evidence, "_run_capture", lambda argv: proc)

    with pytest.raises(RuntimeError, match="jj unavailable"):
        run_goal_evidence._run_required_capture(["jj", "st"])
