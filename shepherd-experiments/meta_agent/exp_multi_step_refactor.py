"""Experiment 3 — Sequential multi-step refactor pipeline.

Architecture (Shepherd-native):
  Each pipeline step runs as a separate ``workspace.run()`` call against the
  *current selected workspace state*: step N's output is selected before step
  N+1 begins, so every step builds on the previous one's merged result.

  The full history of scope forks, merges, and selections is recorded in the
  VcsCore substrate (``shepherd run list`` shows every step as a settled run).

Pipeline
--------
  Step 1 — write_tests      : Write unit tests for a target function
  Step 2 — implement         : Implement the function to pass the tests
  Step 3 — add_docstrings    : Add PEP 257 docstrings to all public symbols

Each step uses ``runtime={"provider": "claude"}`` inside a Vers VM jail.
The local overseer decides whether to accept each step (score ≥ 5) or abort.

Usage
-----
::

    python exp_multi_step_refactor.py \\
        --function "parse_csv" \\
        --spec "Parse a CSV string into a list of dicts" \\
        --results-dir ../results/exp3/
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import shepherd as sp
from ws_init import ensure_shepherd_workspace, seed_workspace
from overseer import evaluate_runs, overseer_call, EvaluatedRun, _parse_score, _parse_rationale

# ---------------------------------------------------------------------------
# Task definitions (empty bodies — Claude implements each)
# ---------------------------------------------------------------------------

def write_tests(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    function_name: str,
    spec: str,
    test_path: str = "test_solution.py",
) -> None:
    """Write comprehensive pytest unit tests for ``function_name`` based on ``spec``.

    Save them to ``test_path``.  Tests must be runnable with ``pytest`` without
    modification.  Cover normal cases, edge cases, and error cases.
    """


def implement_function(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    function_name: str,
    spec: str,
    test_path: str = "test_solution.py",
    impl_path: str = "solution.py",
) -> None:
    """Implement ``function_name`` so that all tests in ``test_path`` pass.

    Write the implementation to ``impl_path``.  Read the existing tests first
    and make sure every test assertion is satisfied.
    """


def add_docstrings(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    impl_path: str = "solution.py",
) -> None:
    """Add PEP 257-compliant docstrings to every public function and class in ``impl_path``.

    Edit the file in place; do not change any logic.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evaluate_single(run: object, task_description: str, path: str, model: str) -> EvaluatedRun:
    """Score a single run with the local overseer."""
    cs = run.changeset()  # type: ignore[attr-defined]
    changed = cs.changed_paths
    raw_result = cs.read_file(path)
    if raw_result is None and changed:
        raw_result = cs.read_file(changed[0])
        path = changed[0] if changed else path
    raw, _ = raw_result if raw_result else (b"", 0)
    full_content = raw.decode("utf-8", errors="replace")
    preview = full_content[:4000]
    truncated = len(full_content) > 4000

    prompt = (
        f"Evaluate this output for the task: {task_description}\n\n"
        f"FILE: {path} ({len(full_content)} bytes{', truncated' if truncated else ''}):\n```\n{preview}\n```\n\n"
        f"Score 0-10. Reply: SCORE: <n>\nRATIONALE: <one sentence>"
    )
    reply = overseer_call(prompt, model=model)
    return EvaluatedRun(
        run=run,
        score=_parse_score(reply),
        rationale=_parse_rationale(reply),
        changed_paths=tuple(changed),
        code_preview=preview,
    )


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    *,
    function_name: str,
    spec: str,
    workspace_dir: Path | None,
    results_dir: Path,
    model: str,
) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "exp_multi_step_refactor.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Experiment 3: Multi-Step Refactor Pipeline ===")
    _log(f"function_name : {function_name}")
    _log(f"spec          : {spec}")
    _log(f"model         : {model}")

    # --- workspace setup ---
    cleanup_ws = workspace_dir is None
    _ws_tmp = None
    if workspace_dir is None:
        _ws_tmp = tempfile.TemporaryDirectory(prefix="shepherd-exp3-")
        workspace_dir = Path(_ws_tmp.name)
        seed_workspace(workspace_dir, {
            "README.md": f"# Experiment 3: {function_name}\n\nSpec: {spec}\n",
        })

    _log(f"workspace     : {workspace_dir}")

    ws = ensure_shepherd_workspace(workspace_dir)
    steps_results = []

    try:
        ws.tasks.register(write_tests, task_id="exp3.write_tests", may_default="ReadWrite")
        ws.tasks.register(implement_function, task_id="exp3.implement_function", may_default="ReadWrite")
        ws.tasks.register(add_docstrings, task_id="exp3.add_docstrings", may_default="ReadWrite")

        pipeline = [
            {
                "step": 1,
                "name": "write_tests",
                "task_id": "exp3.write_tests",
                "args": {"function_name": function_name, "spec": spec, "test_path": "test_solution.py"},
                "primary_output": "test_solution.py",
                "description": f"Write tests for {function_name}: {spec}",
            },
            {
                "step": 2,
                "name": "implement_function",
                "task_id": "exp3.implement_function",
                "args": {"function_name": function_name, "spec": spec,
                         "test_path": "test_solution.py", "impl_path": "solution.py"},
                "primary_output": "solution.py",
                "description": f"Implement {function_name} passing all tests",
            },
            {
                "step": 3,
                "name": "add_docstrings",
                "task_id": "exp3.add_docstrings",
                "args": {"impl_path": "solution.py"},
                "primary_output": "solution.py",
                "description": "Add PEP 257 docstrings to all public symbols",
            },
        ]

        overall_t0 = time.perf_counter()

        for step in pipeline:
            _log(f"\n--- Step {step['step']}: {step['name']} ---")
            t0 = time.perf_counter()
            try:
                run = ws.run(
                    step["task_id"],
                    repo=ws.git_repo(),
                    args=step["args"],
                    placement="auto",
                    runtime={"provider": "claude"},
                )
                elapsed = time.perf_counter() - t0
                changed = run.changeset().changed_paths
                _log(f"  run_ref  : {run.run_ref}")
                _log(f"  changed  : {list(changed)}")
                _log(f"  elapsed  : {elapsed:.2f}s")

                # Overseer evaluation
                ev = _evaluate_single(run, step["description"], step["primary_output"], model)
                _log(f"  score    : {ev.score:.1f} — {ev.rationale}")

                if ev.score >= 5.0:
                    run.output().select()
                    _log(f"  → Selected ✅")
                    steps_results.append({
                        "step": step["step"],
                        "name": step["name"],
                        "run_ref": run.run_ref,
                        "status": "selected",
                        "score": ev.score,
                        "rationale": ev.rationale,
                        "changed_paths": list(changed),
                        "elapsed_s": round(elapsed, 3),
                    })
                else:
                    run.output().discard()
                    _log(f"  → Discarded ❌ (score too low); aborting pipeline.")
                    steps_results.append({
                        "step": step["step"],
                        "name": step["name"],
                        "run_ref": run.run_ref,
                        "status": "discarded",
                        "score": ev.score,
                        "rationale": ev.rationale,
                        "changed_paths": list(changed),
                        "elapsed_s": round(elapsed, 3),
                    })
                    break

            except Exception as exc:
                elapsed = time.perf_counter() - t0
                _log(f"  ERROR: {exc}")
                steps_results.append({
                    "step": step["step"],
                    "name": step["name"],
                    "status": "error",
                    "error": str(exc),
                    "elapsed_s": round(elapsed, 3),
                })
                break

        total_elapsed = time.perf_counter() - overall_t0
        n_selected = sum(1 for s in steps_results if s.get("status") == "selected")
        pipeline_complete = n_selected == len(pipeline)
        _log(f"\nPipeline: {n_selected}/{len(pipeline)} steps selected, "
             f"total {total_elapsed:.2f}s")

        result = {
            "experiment": "exp_multi_step_refactor",
            "function_name": function_name,
            "spec": spec,
            "model": model,
            "workspace": str(workspace_dir),
            "steps": steps_results,
            "n_steps_total": len(pipeline),
            "n_steps_selected": n_selected,
            "pipeline_complete": pipeline_complete,
            "total_elapsed_s": round(total_elapsed, 3),
            "status": "pass",
        }
    finally:
        ws.close()
        if cleanup_ws and _ws_tmp is not None:
            _ws_tmp.cleanup()
        log.close()

    out = results_dir / "exp_multi_step_refactor.json"
    out.write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Experiment 3: Multi-step refactor pipeline")
    p.add_argument("--function", dest="function_name", default="parse_csv")
    p.add_argument("--spec", default="Parse a CSV string into a list of dicts, one per row")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--results-dir", type=Path, default=Path("results/exp3"))
    p.add_argument("--model", default="claude-opus-4-5")
    args = p.parse_args()

    result = run_experiment(
        function_name=args.function_name,
        spec=args.spec,
        workspace_dir=args.workspace,
        results_dir=args.results_dir,
        model=args.model,
    )
    print(f"\nResult saved → {args.results_dir}/exp_multi_step_refactor.json")
    print(f"Pipeline complete: {result['pipeline_complete']}")


if __name__ == "__main__":
    main()
