"""Claim and guardrail coverage for the v0.1.1 static workflow slice."""

from __future__ import annotations

import inspect
from pathlib import Path

import tomllib
from shepherd_dialect import workspace_control

REPO = Path(__file__).resolve().parents[1]
CLAIM_SCOPE = REPO / "docs" / "engineering" / "convergence" / "goals" / "v011-claim-scope.toml"
READINESS_DOC = REPO / "docs" / "engineering" / "convergence" / "v011-static-readiness.md"
V01_READINESS_DOC = REPO / "docs" / "engineering" / "convergence" / "v01-release-readiness.md"
V012_PLAN = REPO / "260701-v012-getting-started-plan.md"
PROVIDER_EVENTS_DOC = REPO / "docs" / "engineering" / "convergence" / "v011-provider-event-ownership.md"
VISUAL_ARTIFACT_EXAMPLE = REPO / "examples" / "notebooks" / "visual_artifact"
NOTEBOOK_DIR = VISUAL_ARTIFACT_EXAMPLE / "notebooks"
STATIC_RENDER_DIR = VISUAL_ARTIFACT_EXAMPLE / "sample_outputs" / "static-render"
VISUAL_ARTIFACT_SPIKE = REPO / "design" / "usecases" / "visual_artifact_spike"
SUPERSEDED_LAUNCH_DOCS = [
    VISUAL_ARTIFACT_SPIKE / "MIGRATION-PLAN.md",
    VISUAL_ARTIFACT_SPIKE / "EXECUTION-MIGRATION-SCOPE.md",
]
NATIVE_NOTEBOOK_SURFACE = [
    NOTEBOOK_DIR / "_build_notebooks.py",
    NOTEBOOK_DIR / "_usecase_template.ipynb",
    NOTEBOOK_DIR / "visual_variant_studio.ipynb",
    NOTEBOOK_DIR / "model_right_sizing_lab.ipynb",
    NOTEBOOK_DIR / "visual_pipeline_recovery.ipynb",
    NOTEBOOK_DIR / "visual_variant_studio_internals.ipynb",
    NOTEBOOK_DIR / "README.md",
    VISUAL_ARTIFACT_EXAMPLE / "README.md",
    VISUAL_ARTIFACT_EXAMPLE / "shepherd_usecases" / "visual_artifact" / "launch.py",
    VISUAL_ARTIFACT_EXAMPLE / "shepherd_usecases" / "visual_artifact" / "render.py",
    VISUAL_ARTIFACT_EXAMPLE / "shepherd_usecases" / "visual_artifact" / "tasks.py",
    VISUAL_ARTIFACT_EXAMPLE / "shepherd_usecases" / "visual_artifact" / "viz.py",
    VISUAL_ARTIFACT_EXAMPLE / "shepherd_usecases" / "visual_artifact" / "__init__.py",
]
PUBLIC_RELEASE_TEXT = [
    VISUAL_ARTIFACT_EXAMPLE / "README.md",
    NOTEBOOK_DIR / "README.md",
    NOTEBOOK_DIR / "_build_notebooks.py",
    NOTEBOOK_DIR / "_usecase_template.ipynb",
    NOTEBOOK_DIR / "visual_variant_studio.ipynb",
    NOTEBOOK_DIR / "model_right_sizing_lab.ipynb",
    NOTEBOOK_DIR / "visual_pipeline_recovery.ipynb",
    VISUAL_ARTIFACT_EXAMPLE / "render.py",
    STATIC_RENDER_DIR / "README.md",
]
STATIC_RENDER_SCRIPTS = [
    STATIC_RENDER_DIR / "scripts" / "nb_shot.py",
    STATIC_RENDER_DIR / "scripts" / "render_artifacts.py",
    STATIC_RENDER_DIR / "scripts" / "render_uc3.py",
]
RETIRED_LAUNCH_RUNTIME_SURFACES = [
    VISUAL_ARTIFACT_SPIKE / "shepherd.py",
    VISUAL_ARTIFACT_SPIKE / "visual_demo.py",
    VISUAL_ARTIFACT_SPIKE / "rightsizing_demo.py",
    VISUAL_ARTIFACT_SPIKE / "recovery_demo.py",
    VISUAL_ARTIFACT_SPIKE / "live_smoke.py",
    VISUAL_ARTIFACT_SPIKE / "notebook_helpers.py",
    VISUAL_ARTIFACT_SPIKE / "notebooks",
    VISUAL_ARTIFACT_SPIKE / "render.py",
    VISUAL_ARTIFACT_SPIKE / "rightsizing_nb.py",
    VISUAL_ARTIFACT_SPIKE / "recovery_nb.py",
    VISUAL_ARTIFACT_SPIKE / "hero_genre.py",
    VISUAL_ARTIFACT_SPIKE / "tile_genre.py",
    VISUAL_ARTIFACT_SPIKE / "viz.py",
    VISUAL_ARTIFACT_SPIKE / "rightsizing_genre.py",
    VISUAL_ARTIFACT_SPIKE / "recovery_genre.py",
    VISUAL_ARTIFACT_SPIKE / "sample_outputs" / "static-render",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "launch.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "tasks.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "runtime.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "variant_studio.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "right_sizing_lab.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "pipeline_recovery.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "variant_demo.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "right_sizing_demo.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "recovery_demo.py",
    VISUAL_ARTIFACT_SPIKE / "shepherd_usecases" / "visual_artifact" / "live_smoke.py",
]


def test_v011_static_claim_rows_are_explicitly_bounded() -> None:
    """The static gate and conditional/deferred rows stay separate."""
    rows = tomllib.loads(CLAIM_SCOPE.read_text(encoding="utf-8"))["claims"]

    assert rows["static_workflow_notebooks"] == "required"
    assert rows["recovery_boundary_semantics"] == "required"
    assert rows["claude_uc1_live_lane"] == "required_live_smoke"
    assert {name for name, state in rows.items() if state == "deferred"} == {
        "codex_provider",
        "provider_plugins",
        "provider_sessions_replay",
        "retained_output_writable_branching",
        "v02_per_binding_grants",
    }


def test_v011_public_surface_exposes_static_workflow_primitives_not_deferred_platform() -> None:
    """The public module exports v0.1.1 primitives without deferred platform APIs."""
    assert hasattr(workspace_control.ShepherdWorkspace, "run")
    assert hasattr(workspace_control.ShepherdWorkspace, "git_repo")
    assert hasattr(workspace_control.RunOutput, "artifact")
    assert hasattr(workspace_control.RunOutput, "read_text")
    assert hasattr(workspace_control.RunOutput, "read_json")
    assert workspace_control.RUN_ARTIFACT_INPUT_SCHEMA == "skeleton.run_artifact_input.v1"
    assert workspace_control.FLOW_SCHEMA == "shepherd.workspace_control.flow.v1"
    run_parameters = inspect.signature(workspace_control.ShepherdWorkspace.run).parameters
    assert "_flow_context" not in run_parameters
    assert "flow_context" not in run_parameters

    assert not hasattr(workspace_control.ShepherdWorkspace, "apply")
    assert not hasattr(workspace_control.ShepherdWorkspace, "best_of_n")
    assert not hasattr(workspace_control.ShepherdWorkspace, "gather")
    assert not hasattr(workspace_control.RunOutput, "branch")
    assert not hasattr(workspace_control.RunOutput, "branch_from")
    assert not hasattr(workspace_control, "Session")
    assert not hasattr(workspace_control, "ProviderPlugin")


def test_v011_launch_notebooks_do_not_expose_codex_or_unqualified_replay_claims() -> None:
    """Launch-facing notebook text avoids deferred Codex and retained-branching claims."""
    checked = PUBLIC_RELEASE_TEXT
    joined = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert 'GENERATOR = "codex"' not in joined
    assert 'GENERATOR = "claude"' not in joined
    assert 'GENERATOR = "static"' not in joined
    assert "GENERATOR =" not in joined
    assert 'choices=("claude", "codex", "static")' not in joined
    assert "Static or live?" not in joined
    assert "RUN_LIVE_CLAUDE = False" in (NOTEBOOK_DIR / "visual_variant_studio.ipynb").read_text(encoding="utf-8")
    assert "launch.require_claude()" in (NOTEBOOK_DIR / "visual_variant_studio.ipynb").read_text(encoding="utf-8")
    assert "new fork from retained boundary" not in joined
    assert "replay_scope" not in joined
    assert "for the replay" not in joined
    assert "Now rewind to" not in joined


def test_v011_public_launch_notebooks_use_release_friendly_names() -> None:
    """Launch-facing notebooks should not expose protocol shorthand as the helper API."""
    checked = PUBLIC_RELEASE_TEXT
    joined = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert "launch" not in joined
    assert "tasks" not in joined
    assert "skeleton." not in joined
    assert "Native" not in joined
    assert "Visual Artifact Spike" not in joined
    assert "visual_artifact_spike" not in joined
    assert "open_flow(" not in joined
    assert "artifact_input(" not in joined


def test_public_notebooks_explain_shepherd_shepherd_transition() -> None:
    """Public notebooks can use Shepherd branding, but must explain the current Shepherd API names."""
    joined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            VISUAL_ARTIFACT_EXAMPLE / "README.md",
            NOTEBOOK_DIR / "README.md",
            NOTEBOOK_DIR / "_build_notebooks.py",
        ]
    )

    assert "Shepherd notebook examples over the current Shepherd workspace-control API" in joined
    assert "Shepherd-facing examples over the current Shepherd Python API" in joined
    assert "Visual artifact notebook template" in joined
    assert "Shepherd launch notebook template" not in joined


def test_v011_public_example_files_do_not_use_prelaunch_compatibility_framing() -> None:
    """Public examples should read as current examples, not migration shims."""
    checked = [
        VISUAL_ARTIFACT_EXAMPLE / "README.md",
        VISUAL_ARTIFACT_EXAMPLE / "render.py",
        VISUAL_ARTIFACT_EXAMPLE / "shepherd_usecases" / "visual_artifact" / "launch.py",
        VISUAL_ARTIFACT_EXAMPLE / "shepherd_usecases" / "visual_artifact" / "tasks.py",
        NOTEBOOK_DIR / "README.md",
    ]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert "Compatibility CLI" not in joined
    assert "compatibility shim" not in joined
    assert "temporary runtime" not in joined
    assert "cannot provision into it" not in joined


def test_v011_static_render_scripts_use_current_example_surface() -> None:
    """Release asset helpers should not depend on removed shims or absolute workspace paths."""
    joined = "\n".join(path.read_text(encoding="utf-8") for path in STATIC_RENDER_SCRIPTS)

    assert "/workspaces/" not in joined
    assert "design/usecases/visual_artifact_spike" not in joined
    assert "tile_genre" not in joined
    assert "shepherd_usecases.visual_artifact.tile" in joined


def test_v011_launch_notebooks_use_native_surface_not_temporary_runtime() -> None:
    """Launch notebooks and their native helper do not depend on the temporary runtime."""
    joined = "\n".join(path.read_text(encoding="utf-8") for path in NATIVE_NOTEBOOK_SURFACE)

    forbidden = [
        "session.fork",
        "open_session",
        "CompletedRun",
        "GitRepoHandle",
        "RealPathCaptureBackend",
        "runtime as sh",
        "from. import runtime",
        "from shepherd_usecases.visual_artifact import runtime",
    ]
    for needle in forbidden:
        assert needle not in joined


def test_v011_retired_launch_runtime_surfaces_stay_removed() -> None:
    """The pre-notebook runtime and compatibility shims must not re-enter the launch path."""
    assert [path for path in RETIRED_LAUNCH_RUNTIME_SURFACES if path.exists()] == []


def test_v011_public_notebook_builder_preserves_artifact_input_refs() -> None:
    """Public notebook flows cite retained artifacts for review, selection, and retry dataflow."""
    builder = (NOTEBOOK_DIR / "_build_notebooks.py").read_text(encoding="utf-8")

    assert "candidate_refs = {" in builder
    assert "verdict_refs = {" in builder
    assert "draft_ref = launch.artifact_ref(draft_v1" in builder
    assert "review_ref = launch.artifact_ref(reviewer" in builder
    assert "diagnosis_ref = launch.artifact_ref(inspector" in builder
    assert "run_with_artifact_refs(" in builder


def test_v011_static_readiness_doc_is_claim_bounded() -> None:
    """The in-repo readiness note keeps static, live-release, and deferred claims separate."""
    readiness = READINESS_DOC.read_text(encoding="utf-8")

    assert "make test-dialect-v011-static" in readiness
    assert "v0.1.1-static" in readiness
    assert "required v0.1.1 live release lane" in readiness
    assert "make test-dialect-v011-claude-evidence" in readiness
    assert "SHEPHERD_LIVE_CLAUDE=1 make test-dialect-v011-claude-evidence" in readiness
    assert "Codex provider support" in readiness
    assert "Provider provenance is diagnostics, not placement or enforcement evidence" in readiness
    assert "Flow` is not `Session" in readiness


def test_v012_plan_tracks_landed_facade_without_overclaiming_core() -> None:
    """The v0.1.2 plan reflects the current stack: WS-A landed, core/live gates still open."""
    plan = V012_PLAN.read_text(encoding="utf-8")

    assert "WS-A facade pass 1 has landed" in plan
    assert "active; WS-A pass 1 landed, core/live gates open" in plan
    assert "### WS-A" in plan
    assert "[LANDED" in plan
    assert "`sp.open(..., backend=...)` forwards carrier selection" in plan
    assert "`sp.open()` works on **both** macOS and\n Linux" not in plan
    assert "CLI, install, and tour work remain before promotion" in plan
    assert "v0.1.2-live promotes only after the live retained-custody smoke" in plan


def test_v012_plan_keeps_per_binding_public_surface_deferred() -> None:
    """Internal per-binding jail lowering must not be described as the public v0.2 surface."""
    plan = V012_PLAN.read_text(encoding="utf-8")
    readiness = V01_READINESS_DOC.read_text(encoding="utf-8")

    assert "per-binding grant-to-`ConfinementSpec` lowering floor now exists" in plan
    assert "public signature/grant\nsurface is still outside this cut" in plan
    assert "per-binding grant surface is v0.2" in plan
    assert "getting-started surface, not an expansion of\nthe v0.1 release claim" in readiness
    assert "the public v0.1/v0.1.1 surface still stays on whole-run `may=`" in readiness


def test_v011_superseded_launch_docs_are_tombstones_not_dead_runbooks() -> None:
    """Historical launch plans should not publish commands to removed runtime shims."""
    for path in SUPERSEDED_LAUNCH_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "Historical Tombstone" in text
        assert "make test-dialect-v011-static" in text
        assert "uv run python design/usecases/visual_artifact_spike/visual_demo.py" not in text
        assert "uv run python design/usecases/visual_artifact_spike/rightsizing_demo.py" not in text
        assert "uv run python design/usecases/visual_artifact_spike/recovery_demo.py" not in text
        assert "uv run python design/usecases/visual_artifact_spike/live_smoke.py" not in text


def test_v011_provider_event_ownership_doc_preserves_static_boundary() -> None:
    """Provider events are owned as provenance and projected, not treated as enforcement evidence."""
    text = PROVIDER_EVENTS_DOC.read_text(encoding="utf-8")

    assert "canonical owner" in text
    assert "Flow.trace()" in text
    assert "Provider provenance is not placement or enforcement evidence" in text
    assert "Claude" in text
