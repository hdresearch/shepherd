"""Experiment 1 — N sub-agents on the same coding task; overseer selects best.

Architecture (Shepherd-native):
  - Sub-agents run inside Vers VMs via ``workspace.run(..., runtime={"provider": "claude"})``
  - Each run forks a VcsCore scope; Claude writes code to the scope's working_path
  - VcsCore merges the scope on success, retaining the output pending settle
  - The overseer (local Anthropic SDK call) reads each changeset, scores, and
    calls ``output.select()`` on the best / ``output.discard()`` on the rest
  - The full trace (fork → Claude → merge → select) is recorded in the
    Shepherd substrate at ``.vcscore``

Sub-agents never see each other's work; the overseer is the only component
allowed to read and settle outputs.

Usage
-----
::

    # Run 3 agents on a fibonacci task (needs a jail-capable host, e.g. Linux VM):
    python exp_coding_task.py \\
        --task "Add a fibonacci function with memoization to utils.py" \\
        --n-agents 3 \\
        --results-dir ../results/exp1/

    # Use an existing repository as the workspace:
    python exp_coding_task.py \\
        --task "Add input validation to the register() function in auth.py" \\
        --workspace /path/to/repo \\
        --n-agents 2
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
from overseer import evaluate_runs, select_best

# ---------------------------------------------------------------------------
# Task definition (empty body — Claude fills it in)
# ---------------------------------------------------------------------------

def write_solution(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    task_description: str,
    output_path: str = "solution.py",
) -> None:
    """Read the task description and produce a complete Python implementation.

    Write the solution to ``output_path``.  The file must be valid Python,
    include a docstring, and be self-contained (no dependencies not already
    present in the repository).
    """


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    *,
    task: str,
    n_agents: int,
    workspace_dir: Path | None,
    results_dir: Path,
    model: str,
) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "exp_coding_task.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log(f"=== Experiment 1: Coding Task ===")
    _log(f"task       : {task}")
    _log(f"n_agents   : {n_agents}")
    _log(f"model      : {model}")

    # --- workspace setup ---
    cleanup_ws = workspace_dir is None
    _ws_tmp = None
    if workspace_dir is None:
        _ws_tmp = tempfile.TemporaryDirectory(prefix="shepherd-exp1-")
        workspace_dir = Path(_ws_tmp.name)
        seed_workspace(workspace_dir, {
            "utils.py": "# utility module\n",
            "README.md": f"# Experiment workspace\n\nTask: {task}\n",
        })

    _log(f"workspace  : {workspace_dir}")

    ws = ensure_shepherd_workspace(workspace_dir)
    try:
        ws.tasks.register(
            write_solution,
            task_id="exp1.write_solution",
            may_default="ReadWrite",
        )

        runs = []
        agent_results = []
        t_agents_start = time.perf_counter()

        for i in range(n_agents):
            _log(f"\n--- Agent {i+1}/{n_agents} ---")
            t0 = time.perf_counter()
            try:
                run = ws.run(
                    "exp1.write_solution",
                    repo=ws.git_repo(),
                    args={"task_description": task, "output_path": "solution.py"},
                    placement="auto",
                    runtime={"provider": "claude"},
                )
                elapsed = time.perf_counter() - t0
                changed = run.changeset().changed_paths
                _log(f"  run_ref    : {run.run_ref}")
                _log(f"  changed    : {list(changed)}")
                _log(f"  elapsed    : {elapsed:.2f}s")
                runs.append(run)
                agent_results.append({
                    "agent_idx": i,
                    "run_ref": run.run_ref,
                    "status": "ok",
                    "changed_paths": list(changed),
                    "elapsed_s": round(elapsed, 3),
                })
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                _log(f"  ERROR: {exc}")
                agent_results.append({
                    "agent_idx": i,
                    "status": "error",
                    "error": str(exc),
                    "elapsed_s": round(elapsed, 3),
                })

        t_agents_total = time.perf_counter() - t_agents_start

        # --- overseer evaluation ---
        _log(f"\n--- Overseer evaluation ({len(runs)} runs) ---")
        t_eval_start = time.perf_counter()
        evaluated = evaluate_runs(runs, task, primary_output="solution.py", model=model)
        for ev in evaluated:
            _log(f"  run {ev.run.run_ref[:12]}  score={ev.score:.1f}  {ev.rationale}")

        winner = select_best(evaluated, min_score=5.0)
        t_eval_total = time.perf_counter() - t_eval_start

        if winner:
            _log(f"\n✅ Selected: {winner.run.run_ref} (score={winner.score:.1f})")
            _log(f"   {winner.rationale}")
        else:
            _log(f"\n⚠️  No run cleared the quality bar; all discarded.")

        result = {
            "experiment": "exp_coding_task",
            "task": task,
            "n_agents": n_agents,
            "model": model,
            "workspace": str(workspace_dir),
            "agent_results": agent_results,
            "evaluation": [
                {
                    "run_ref": ev.run.run_ref,
                    "score": ev.score,
                    "rationale": ev.rationale,
                    "changed_paths": list(ev.changed_paths),
                }
                for ev in evaluated
            ],
            "winner_run_ref": winner.run.run_ref if winner else None,
            "winner_score": winner.score if winner else None,
            "t_agents_total_s": round(t_agents_total, 3),
            "t_eval_total_s": round(t_eval_total, 3),
            "status": "pass",
        }
    finally:
        ws.close()
        if cleanup_ws and _ws_tmp is not None:
            _ws_tmp.cleanup()
        log.close()

    out = results_dir / "exp_coding_task.json"
    out.write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Experiment 1: N agents on a coding task")
    p.add_argument("--task", default="Add a fibonacci function with memoization to solution.py")
    p.add_argument("--n-agents", type=int, default=3)
    p.add_argument("--workspace", type=Path, default=None,
                   help="Existing workspace dir (default: fresh temp dir)")
    p.add_argument("--results-dir", type=Path, default=Path("results/exp1"))
    p.add_argument("--model", default="claude-opus-4-5")
    args = p.parse_args()

    result = run_experiment(
        task=args.task,
        n_agents=args.n_agents,
        workspace_dir=args.workspace,
        results_dir=args.results_dir,
        model=args.model,
    )
    print(f"\nResult saved → {args.results_dir}/exp_coding_task.json")
    print(f"Status: {result['status']}")


if __name__ == "__main__":
    main()
