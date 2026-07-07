"""Experiment 2 — Overseer-directed parallel hypothesis search.

Architecture (Shepherd-native):
  1. The LOCAL overseer (Anthropic SDK) generates N implementation hypotheses
     for the given problem.
  2. For each hypothesis, a Claude sub-agent runs in a Vers VM jail
     (``workspace.run(..., runtime={"provider": "claude"})``), implementing
     that specific approach inside an isolated VcsCore scope.
  3. The overseer evaluates the N retained outputs and selects the best via
     ``output.select()``; the rest are discarded.

This matches the Shepherd paper's "parallel exploratory search" pattern:
independent workspace forks, programmable overseer, reversible substrate.

Usage
-----
::

    python exp_parallel_search.py \\
        --problem "Implement a thread-safe LRU cache" \\
        --n-hypotheses 3 \\
        --results-dir ../results/exp2/
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
from overseer import evaluate_runs, generate_hypotheses, select_best, overseer_call

# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

def implement_approach(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    approach_description: str,
    problem: str,
    output_path: str = "solution.py",
) -> None:
    """Implement the solution described in ``approach_description`` for the given ``problem``.

    The implementation must be complete, correct, and written to ``output_path``.
    Include a module-level docstring that summarises the chosen approach.
    """


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    *,
    problem: str,
    n_hypotheses: int,
    workspace_dir: Path | None,
    results_dir: Path,
    model: str,
) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "exp_parallel_search.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Experiment 2: Parallel Hypothesis Search ===")
    _log(f"problem      : {problem}")
    _log(f"n_hypotheses : {n_hypotheses}")
    _log(f"model        : {model}")

    # --- workspace setup ---
    cleanup_ws = workspace_dir is None
    _ws_tmp = None
    if workspace_dir is None:
        _ws_tmp = tempfile.TemporaryDirectory(prefix="shepherd-exp2-")
        workspace_dir = Path(_ws_tmp.name)
        seed_workspace(workspace_dir, {
            "README.md": f"# Experiment 2 workspace\n\nProblem: {problem}\n",
        })

    _log(f"workspace    : {workspace_dir}")

    # --- overseer: generate hypotheses ---
    _log("\n--- Overseer: generating hypotheses ---")
    t_hyp_start = time.perf_counter()
    hypotheses = generate_hypotheses(problem, n_hypotheses, model=model)
    t_hyp = time.perf_counter() - t_hyp_start
    for i, h in enumerate(hypotheses, 1):
        _log(f"  Hypothesis {i}: {h[:100]}...")

    ws = ensure_shepherd_workspace(workspace_dir)
    try:
        ws.tasks.register(
            implement_approach,
            task_id="exp2.implement_approach",
            may_default="ReadWrite",
        )

        runs = []
        agent_results = []
        t_agents_start = time.perf_counter()

        for i, hypothesis in enumerate(hypotheses):
            _log(f"\n--- Agent {i+1}/{n_hypotheses}: {hypothesis[:60]}... ---")
            t0 = time.perf_counter()
            try:
                run = ws.run(
                    "exp2.implement_approach",
                    repo=ws.git_repo(),
                    args={
                        "approach_description": hypothesis,
                        "problem": problem,
                        "output_path": "solution.py",
                    },
                    placement="auto",
                    runtime={"provider": "claude"},
                )
                elapsed = time.perf_counter() - t0
                changed = run.changeset().changed_paths
                _log(f"  run_ref  : {run.run_ref}")
                _log(f"  changed  : {list(changed)}")
                _log(f"  elapsed  : {elapsed:.2f}s")
                runs.append(run)
                agent_results.append({
                    "hypothesis_idx": i,
                    "hypothesis": hypothesis,
                    "run_ref": run.run_ref,
                    "status": "ok",
                    "changed_paths": list(changed),
                    "elapsed_s": round(elapsed, 3),
                })
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                _log(f"  ERROR: {exc}")
                agent_results.append({
                    "hypothesis_idx": i,
                    "hypothesis": hypothesis,
                    "status": "error",
                    "error": str(exc),
                    "elapsed_s": round(elapsed, 3),
                })

        t_agents_total = time.perf_counter() - t_agents_start

        # --- overseer: evaluate and select ---
        _log(f"\n--- Overseer: evaluating {len(runs)} implementations ---")
        t_eval_start = time.perf_counter()
        evaluated = evaluate_runs(runs, problem, primary_output="solution.py", model=model)
        for ev in evaluated:
            _log(f"  run {ev.run.run_ref[:12]}  score={ev.score:.1f}  {ev.rationale}")

        winner = select_best(evaluated, min_score=5.0)
        t_eval_total = time.perf_counter() - t_eval_start

        if winner:
            _log(f"\n✅ Selected: {winner.run.run_ref} (score={winner.score:.1f})")
        else:
            _log(f"\n⚠️  No implementation cleared the quality bar; all discarded.")

        result = {
            "experiment": "exp_parallel_search",
            "problem": problem,
            "n_hypotheses": n_hypotheses,
            "model": model,
            "workspace": str(workspace_dir),
            "hypotheses": hypotheses,
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
            "t_hypothesis_gen_s": round(t_hyp, 3),
            "t_agents_total_s": round(t_agents_total, 3),
            "t_eval_total_s": round(t_eval_total, 3),
            "status": "pass",
        }
    finally:
        ws.close()
        if cleanup_ws and _ws_tmp is not None:
            _ws_tmp.cleanup()
        log.close()

    out = results_dir / "exp_parallel_search.json"
    out.write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Experiment 2: Parallel hypothesis search")
    p.add_argument("--problem", default="Implement a thread-safe LRU cache in Python")
    p.add_argument("--n-hypotheses", type=int, default=3)
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--results-dir", type=Path, default=Path("results/exp2"))
    p.add_argument("--model", default="claude-opus-4-5")
    args = p.parse_args()

    result = run_experiment(
        problem=args.problem,
        n_hypotheses=args.n_hypotheses,
        workspace_dir=args.workspace,
        results_dir=args.results_dir,
        model=args.model,
    )
    print(f"\nResult saved → {args.results_dir}/exp_parallel_search.json")
    print(f"Status: {result['status']}")


if __name__ == "__main__":
    main()
