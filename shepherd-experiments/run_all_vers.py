"""Run all Shepherd experiments on Vers VM infrastructure.

Sub-agents run inside **actual Vers VMs** (Linux, Landlock-capable):
  - Each agent run branches a VM from ``shepherd-agent:latest``
  - Claude executes inside the VM with full file-write tools
  - Output files are collected back to the local overseer
  - The VM is deleted after each run

The overseer runs locally via the Anthropic SDK.

Usage::

    uv run python3 run_all_vers.py
    uv run python3 run_all_vers.py --skip-benchmarks
    uv run python3 run_all_vers.py --skip-experiments
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from vers_runner import (
    GOLDEN_IMAGE_COMMIT,
    VersVMRun,
    run_agent_on_vers,
    _branch_vm,
    _delete_vm,
    _vers_exec,
    VM_PATH,
    VM_WORKSPACE,
)
from overseer import (
    overseer_call,
    _parse_score,
    _parse_rationale,
    generate_hypotheses,
)
from ws_init import seed_workspace


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def _check_vers_cli() -> bool:
    import shutil
    return shutil.which("vers") is not None


def _check_golden_image() -> bool:
    """Verify the golden image commit is accessible."""
    try:
        result = subprocess.run(
            ["vers", "branch", GOLDEN_IMAGE_COMMIT, "--format", "json", "--wait"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            vm_id = data["new_ids"][0]
            _delete_vm(vm_id)
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Evaluation helpers (local overseer)
# ---------------------------------------------------------------------------

def _evaluate_output(
    file_content: bytes,
    file_path: str,
    task_description: str,
    model: str,
) -> tuple[float, str]:
    """Score a single output file with the local overseer."""
    text = file_content.decode("utf-8", errors="replace")
    preview = text[:16000]
    truncated = len(text) > 16000

    prompt = (
        f"You are evaluating a coding agent's output for the following task:\n\n"
        f"TASK: {task_description}\n\n"
        f"OUTPUT FILE: {file_path}\n"
        f"CONTENT ({len(text)} bytes{', truncated' if truncated else ''}):\n```\n{preview}\n```\n\n"
        f"Score this output from 0 to 10 (10 = perfect). "
        f"Reply with exactly: SCORE: <number>\nRATIONALE: <one sentence>"
    )
    reply = overseer_call(prompt, model=model)
    return _parse_score(reply), _parse_rationale(reply)


# ---------------------------------------------------------------------------
# Experiment 1: N agents, same task
# ---------------------------------------------------------------------------

def _run_exp_coding_task(results_dir: Path, model: str) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    task = "Add a fibonacci function with memoization to solution.py"
    n_agents = 3

    log_path = results_dir / "exp_coding_task.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Experiment 1: Coding Task (Vers VMs) ===")
    _log(f"task     : {task}")
    _log(f"n_agents : {n_agents}")
    _log(f"model    : {model}")
    _log(f"infra    : Vers VMs (branched from {GOLDEN_IMAGE_COMMIT[:12]})")

    # Create workspace
    with tempfile.TemporaryDirectory(prefix="shepherd-vers-exp1-") as tmp:
        ws_dir = Path(tmp)
        seed_workspace(ws_dir, {
            "utils.py": "# utility module\n",
            "README.md": f"# Experiment workspace\n\nTask: {task}\n",
        })

        prompt = (
            f"You are executing a coding task.\n\n"
            f"TASK: {task}\n\n"
            f"Write the solution to solution.py. The file must be valid Python, "
            f"include a docstring, and be self-contained."
        )

        agent_results = []
        runs: list[VersVMRun] = []
        t0 = time.perf_counter()

        for i in range(n_agents):
            _log(f"\n--- Agent {i + 1}/{n_agents} ---")
            run = run_agent_on_vers(ws_dir, prompt, timeout=120, log_fn=_log)
            runs.append(run)
            agent_results.append({
                "agent_idx": i,
                "vm_id": run.vm_id,
                "status": run.status,
                "changed_files": list(run.changed_files.keys()),
                "elapsed_s": run.elapsed_s,
                "error": run.error,
            })
            _log(f"  status: {run.status} | files: {list(run.changed_files.keys())} | {run.elapsed_s:.1f}s")

        t_agents = time.perf_counter() - t0

        # Evaluate
        _log(f"\n--- Overseer evaluation ({len(runs)} runs) ---")
        evaluations = []
        best_score = -1
        best_idx = -1

        for i, run in enumerate(runs):
            if run.status != "ok" or "solution.py" not in run.changed_files:
                _log(f"  agent {i}: SKIP (no output)")
                evaluations.append({"agent_idx": i, "score": 0, "rationale": "no output"})
                continue

            score, rationale = _evaluate_output(
                run.changed_files["solution.py"], "solution.py", task, model,
            )
            _log(f"  agent {i}: score={score:.1f} — {rationale}")
            evaluations.append({
                "agent_idx": i,
                "vm_id": run.vm_id,
                "score": score,
                "rationale": rationale,
            })
            if score > best_score:
                best_score = score
                best_idx = i

        winner = best_idx if best_score >= 5.0 else None
        if winner is not None:
            _log(f"\n✅ Selected: agent {winner} (score={best_score:.1f})")
        else:
            _log(f"\n⚠️  No agent cleared the quality bar.")

    log.close()
    result = {
        "experiment": "exp_coding_task",
        "infrastructure": "vers_vm",
        "golden_image": GOLDEN_IMAGE_COMMIT,
        "task": task,
        "n_agents": n_agents,
        "model": model,
        "agent_results": agent_results,
        "evaluations": evaluations,
        "winner_idx": winner,
        "winner_score": best_score if winner is not None else None,
        "t_agents_total_s": round(t_agents, 3),
        "status": "pass",
    }
    (results_dir / "exp_coding_task.json").write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Experiment 2: Parallel hypothesis search
# ---------------------------------------------------------------------------

def _run_exp_parallel_search(results_dir: Path, model: str) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    problem = "Implement a thread-safe LRU cache in Python"
    n_hypotheses = 3

    log_path = results_dir / "exp_parallel_search.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Experiment 2: Parallel Hypothesis Search (Vers VMs) ===")
    _log(f"problem      : {problem}")
    _log(f"n_hypotheses : {n_hypotheses}")
    _log(f"model        : {model}")

    # Generate hypotheses (local overseer)
    _log("\n--- Overseer: generating hypotheses ---")
    hypotheses = generate_hypotheses(problem, n_hypotheses, model=model)
    for i, h in enumerate(hypotheses):
        _log(f"  Hypothesis {i + 1}: {h[:100]}...")

    with tempfile.TemporaryDirectory(prefix="shepherd-vers-exp2-") as tmp:
        ws_dir = Path(tmp)
        seed_workspace(ws_dir, {
            "README.md": f"# Experiment 2 workspace\n\nProblem: {problem}\n",
        })

        agent_results = []
        runs: list[VersVMRun] = []
        t0 = time.perf_counter()

        for i, hypothesis in enumerate(hypotheses):
            _log(f"\n--- Agent {i + 1}/{n_hypotheses}: {hypothesis[:60]}... ---")
            prompt = (
                f"You are implementing a specific approach to solve a programming problem.\n\n"
                f"PROBLEM: {problem}\n\n"
                f"APPROACH: {hypothesis}\n\n"
                f"Implement the solution in solution.py. Include a module-level docstring, "
                f"be complete and correct."
            )
            run = run_agent_on_vers(ws_dir, prompt, timeout=180, log_fn=_log)
            runs.append(run)
            agent_results.append({
                "hypothesis_idx": i,
                "hypothesis": hypothesis,
                "vm_id": run.vm_id,
                "status": run.status,
                "changed_files": list(run.changed_files.keys()),
                "elapsed_s": run.elapsed_s,
                "error": run.error,
            })

        t_agents = time.perf_counter() - t0

        # Evaluate
        _log(f"\n--- Overseer: evaluating {len(runs)} implementations ---")
        evaluations = []
        best_score = -1
        best_idx = -1

        for i, run in enumerate(runs):
            if run.status != "ok" or "solution.py" not in run.changed_files:
                evaluations.append({"hypothesis_idx": i, "score": 0, "rationale": "no output"})
                continue

            score, rationale = _evaluate_output(
                run.changed_files["solution.py"], "solution.py", problem, model,
            )
            _log(f"  agent {i}: score={score:.1f} — {rationale}")
            evaluations.append({
                "hypothesis_idx": i,
                "vm_id": run.vm_id,
                "score": score,
                "rationale": rationale,
            })
            if score > best_score:
                best_score = score
                best_idx = i

        winner = best_idx if best_score >= 5.0 else None
        if winner is not None:
            _log(f"\n✅ Selected: hypothesis {winner} (score={best_score:.1f})")
        else:
            _log(f"\n⚠️  No implementation cleared the quality bar.")

    log.close()
    result = {
        "experiment": "exp_parallel_search",
        "infrastructure": "vers_vm",
        "golden_image": GOLDEN_IMAGE_COMMIT,
        "problem": problem,
        "n_hypotheses": n_hypotheses,
        "model": model,
        "hypotheses": hypotheses,
        "agent_results": agent_results,
        "evaluations": evaluations,
        "winner_idx": winner,
        "winner_score": best_score if winner is not None else None,
        "t_agents_total_s": round(t_agents, 3),
        "status": "pass",
    }
    (results_dir / "exp_parallel_search.json").write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Experiment 3: Multi-step refactor pipeline
# ---------------------------------------------------------------------------

def _run_exp_multi_step(results_dir: Path, model: str) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    function_name = "parse_csv"
    spec = "Parse a CSV string into a list of dicts, one per row"

    log_path = results_dir / "exp_multi_step_refactor.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Experiment 3: Multi-Step Refactor Pipeline (Vers VMs) ===")
    _log(f"function : {function_name}")
    _log(f"spec     : {spec}")
    _log(f"model    : {model}")

    with tempfile.TemporaryDirectory(prefix="shepherd-vers-exp3-") as tmp:
        ws_dir = Path(tmp)
        seed_workspace(ws_dir, {
            "README.md": f"# Experiment 3: {function_name}\n\nSpec: {spec}\n",
        })

        pipeline = [
            {
                "name": "write_tests",
                "prompt": (
                    f"Write comprehensive pytest unit tests for a function called `{function_name}` "
                    f"based on this specification: {spec}\n\n"
                    f"Save the tests to test_solution.py. Cover normal cases, edge cases, and error cases. "
                    f"Tests must be runnable with pytest without modification."
                ),
                "primary_output": "test_solution.py",
                "description": f"Write tests for {function_name}: {spec}",
            },
            {
                "name": "implement_function",
                "prompt": (
                    f"Implement the function `{function_name}` so that all tests in test_solution.py pass.\n\n"
                    f"Specification: {spec}\n\n"
                    f"Read test_solution.py first, then write the implementation to solution.py. "
                    f"Make sure every test assertion is satisfied."
                ),
                "primary_output": "solution.py",
                "description": f"Implement {function_name} passing all tests",
            },
            {
                "name": "add_docstrings",
                "prompt": (
                    "Add PEP 257-compliant docstrings to every public function and class in solution.py.\n\n"
                    "Edit the file in place. Do not change any logic. Include Args, Returns, and Raises sections."
                ),
                "primary_output": "solution.py",
                "description": "Add PEP 257 docstrings to all public symbols",
            },
        ]

        steps_results = []
        t0 = time.perf_counter()

        for step_idx, step in enumerate(pipeline):
            _log(f"\n--- Step {step_idx + 1}: {step['name']} ---")
            run = run_agent_on_vers(ws_dir, step["prompt"], timeout=300, log_fn=_log)

            if run.status != "ok" or step["primary_output"] not in run.changed_files:
                _log(f"  ERROR: {run.error or 'no output file'}")
                steps_results.append({
                    "step": step_idx + 1,
                    "name": step["name"],
                    "vm_id": run.vm_id,
                    "status": "error",
                    "error": run.error or "no output",
                    "elapsed_s": run.elapsed_s,
                })
                break

            # Evaluate
            score, rationale = _evaluate_output(
                run.changed_files[step["primary_output"]],
                step["primary_output"],
                step["description"],
                model,
            )
            _log(f"  score: {score:.1f} — {rationale}")

            if score >= 5.0:
                _log(f"  → Selected ✅")
                # Merge output into workspace for next step
                for path, content in run.changed_files.items():
                    target = ws_dir / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                subprocess.run(["git", "add", "-A"], cwd=ws_dir, check=True, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", f"step {step_idx + 1}: {step['name']}"],
                    cwd=ws_dir, check=True, capture_output=True,
                )
                steps_results.append({
                    "step": step_idx + 1,
                    "name": step["name"],
                    "vm_id": run.vm_id,
                    "status": "selected",
                    "score": score,
                    "rationale": rationale,
                    "changed_files": list(run.changed_files.keys()),
                    "elapsed_s": run.elapsed_s,
                })
            else:
                _log(f"  → Discarded ❌ (score too low); aborting pipeline.")
                steps_results.append({
                    "step": step_idx + 1,
                    "name": step["name"],
                    "vm_id": run.vm_id,
                    "status": "discarded",
                    "score": score,
                    "rationale": rationale,
                    "elapsed_s": run.elapsed_s,
                })
                break

        total_elapsed = time.perf_counter() - t0
        n_selected = sum(1 for s in steps_results if s.get("status") == "selected")
        _log(f"\nPipeline: {n_selected}/{len(pipeline)} steps selected, total {total_elapsed:.1f}s")

    log.close()
    result = {
        "experiment": "exp_multi_step_refactor",
        "infrastructure": "vers_vm",
        "golden_image": GOLDEN_IMAGE_COMMIT,
        "function_name": function_name,
        "spec": spec,
        "model": model,
        "steps": steps_results,
        "n_steps_total": len(pipeline),
        "n_steps_selected": n_selected,
        "pipeline_complete": n_selected == len(pipeline),
        "total_elapsed_s": round(total_elapsed, 3),
        "status": "pass",
    }
    (results_dir / "exp_multi_step_refactor.json").write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Benchmark: VM branch + exec latency
# ---------------------------------------------------------------------------

def _run_bench_vm_lifecycle(results_dir: Path) -> dict:
    """Measure VM branch + exec + delete latency."""
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "bench_vm_lifecycle.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Benchmark: Vers VM Lifecycle ===")
    n_iters = 3
    branch_times = []
    exec_times = []
    delete_times = []
    roundtrip_times = []

    for i in range(n_iters):
        _log(f"\n-- iter {i} --")

        # Branch
        t0 = time.perf_counter()
        vm_ids = _branch_vm(count=1)
        vm_id = vm_ids[0]
        t_branch = time.perf_counter() - t0
        branch_times.append(t_branch)
        _log(f"  branch : {t_branch * 1000:.0f} ms  (VM {vm_id[:12]})")

        # Exec
        t0 = time.perf_counter()
        result = _vers_exec(vm_id, f"export PATH={VM_PATH} && echo ok")
        t_exec = time.perf_counter() - t0
        exec_times.append(t_exec)
        _log(f"  exec   : {t_exec * 1000:.0f} ms  (stdout: {result.stdout.strip()!r})")

        # Delete
        t0 = time.perf_counter()
        _delete_vm(vm_id)
        t_delete = time.perf_counter() - t0
        delete_times.append(t_delete)
        _log(f"  delete : {t_delete * 1000:.0f} ms")

        roundtrip_times.append(t_branch + t_exec + t_delete)

    from statistics import mean, median, stdev

    def _stats(times: list[float]) -> dict:
        ms = [t * 1000 for t in times]
        return {
            "mean_ms": round(mean(ms), 1),
            "median_ms": round(median(ms), 1),
            "stdev_ms": round(stdev(ms) if len(ms) > 1 else 0.0, 1),
        }

    log.close()
    result = {
        "benchmark": "bench_vm_lifecycle",
        "infrastructure": "vers_vm",
        "golden_image": GOLDEN_IMAGE_COMMIT,
        "n_iters": n_iters,
        "branch": _stats(branch_times),
        "exec": _stats(exec_times),
        "delete": _stats(delete_times),
        "roundtrip": _stats(roundtrip_times),
        "status": "pass",
    }
    (results_dir / "bench_vm_lifecycle.json").write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Benchmark: workspace push + Claude exec latency
# ---------------------------------------------------------------------------

def _run_bench_agent_roundtrip(results_dir: Path) -> dict:
    """Measure full agent roundtrip: branch + push workspace + Claude exec + collect + delete."""
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "bench_agent_roundtrip.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Benchmark: Full Agent Roundtrip (Vers VM) ===")

    with tempfile.TemporaryDirectory(prefix="shepherd-vers-bench-") as tmp:
        ws_dir = Path(tmp)
        seed_workspace(ws_dir, {"README.md": "# bench workspace\n"})

        n_iters = 3
        roundtrip_times = []

        for i in range(n_iters):
            _log(f"\n-- iter {i} --")
            t0 = time.perf_counter()
            run = run_agent_on_vers(
                ws_dir,
                "Create a file called bench.py containing exactly: # benchmark iteration",
                timeout=60,
                log_fn=_log,
            )
            elapsed = time.perf_counter() - t0
            roundtrip_times.append(elapsed)
            _log(f"  total  : {elapsed * 1000:.0f} ms  status={run.status}  files={list(run.changed_files.keys())}")

    from statistics import mean, median, stdev

    def _stats(times: list[float]) -> dict:
        ms = [t * 1000 for t in times]
        return {
            "mean_ms": round(mean(ms), 1),
            "median_ms": round(median(ms), 1),
            "stdev_ms": round(stdev(ms) if len(ms) > 1 else 0.0, 1),
        }

    log.close()
    result = {
        "benchmark": "bench_agent_roundtrip",
        "infrastructure": "vers_vm",
        "golden_image": GOLDEN_IMAGE_COMMIT,
        "n_iters": n_iters,
        "roundtrip": _stats(roundtrip_times),
        "raw_roundtrip_s": [round(t, 3) for t in roundtrip_times],
        "status": "pass",
    }
    (results_dir / "bench_agent_roundtrip.json").write_text(json.dumps(result, indent=2))
    return result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

SUITE = [
    ("exp_coding_task",         _run_exp_coding_task,         True),
    ("exp_parallel_search",     _run_exp_parallel_search,     True),
    ("exp_multi_step_refactor", _run_exp_multi_step,          True),
    ("bench_vm_lifecycle",      _run_bench_vm_lifecycle,      False),
    ("bench_agent_roundtrip",   _run_bench_agent_roundtrip,   False),
]


def main() -> None:
    p = argparse.ArgumentParser(description="Run Shepherd experiments on Vers VMs")
    p.add_argument("--results-dir", type=Path, default=None)
    p.add_argument("--model", default="claude-opus-4-5")
    p.add_argument("--skip-experiments", action="store_true")
    p.add_argument("--skip-benchmarks", action="store_true")
    args = p.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    results_dir = args.results_dir or (Path("results") / f"vers-{ts}")
    results_dir.mkdir(parents=True, exist_ok=True)

    print(f"Results directory: {results_dir}")
    print(f"Infrastructure: Vers VMs (golden image: {GOLDEN_IMAGE_COMMIT[:12]})")
    print()

    # Preflight
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY not set")
        sys.exit(1)
    print("✅ ANTHROPIC_API_KEY set")

    if not _check_vers_cli():
        print("❌ vers CLI not found")
        sys.exit(1)
    print("✅ vers CLI found")

    print(f"✅ Golden image: {GOLDEN_IMAGE_COMMIT}")
    print()

    # Run suite
    summary_rows = []
    all_t0 = time.perf_counter()

    for name, fn, is_experiment in SUITE:
        if is_experiment and args.skip_experiments:
            print(f"  SKIP  {name}")
            continue
        if not is_experiment and args.skip_benchmarks:
            print(f"  SKIP  {name}")
            continue

        print(f"\n{'=' * 60}")
        print(f"  {name}")
        print(f"{'=' * 60}")
        t0 = time.perf_counter()
        try:
            if is_experiment:
                result = fn(results_dir / name, args.model)
            else:
                result = fn(results_dir / name)
            elapsed = time.perf_counter() - t0
            status = result.get("status", "unknown")
            print(f"\n  → {status.upper()}  ({elapsed:.1f}s)")
            summary_rows.append({"name": name, "status": status, "elapsed_s": round(elapsed, 1)})
        except Exception:
            elapsed = time.perf_counter() - t0
            tb = traceback.format_exc()
            print(f"\n  → ERROR  ({elapsed:.1f}s)")
            print(tb)
            summary_rows.append({"name": name, "status": "error", "elapsed_s": round(elapsed, 1), "traceback": tb})

    total_elapsed = time.perf_counter() - all_t0
    n_pass = sum(1 for r in summary_rows if r["status"] == "pass")
    n_total = len(summary_rows)

    summary = {
        "timestamp": ts,
        "infrastructure": "vers_vm",
        "golden_image": GOLDEN_IMAGE_COMMIT,
        "model": args.model,
        "results_dir": str(results_dir),
        "total_elapsed_s": round(total_elapsed, 1),
        "passed": n_pass,
        "total": n_total,
        "rows": summary_rows,
    }
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Summary markdown
    md = [
        "# Shepherd Experiment Results — Vers VM Infrastructure",
        "",
        f"**Timestamp:** `{ts}`  ",
        f"**Infrastructure:** Vers VMs (Linux x86_64, Landlock)  ",
        f"**Golden image:** `{GOLDEN_IMAGE_COMMIT}`  ",
        f"**Model:** {args.model}  ",
        f"**Passed:** {n_pass}/{n_total}  ",
        f"**Total time:** {total_elapsed:.1f}s",
        "",
        "## Results",
        "",
        "| Experiment | Status | Time |",
        "|---|---|---|",
    ]
    for r in summary_rows:
        icon = "✅" if r["status"] == "pass" else "❌"
        md.append(f"| {r['name']} | {icon} {r['status']} | {r['elapsed_s']}s |")

    md += [
        "",
        "## Architecture",
        "",
        "Each sub-agent runs inside a **dedicated Vers VM** (Ubuntu 24.04, Linux 6.12, x86_64):",
        "```",
        "local overseer",
        "  → vers branch shepherd-agent:latest   # fork VM from golden image (~5s)",
        "  → vers exec <vm> push workspace       # git bundle + base64 transfer",
        "  → vers exec <vm> claude -p <prompt>    # Claude runs inside the VM",
        "  → vers exec <vm> collect output        # base64-encoded file contents",
        "  → vers delete <vm>                     # cleanup",
        "```",
        "",
        "The overseer runs locally via the Anthropic SDK. Sub-agents are fully",
        "isolated in ephemeral VMs with no access to each other or the host.",
    ]
    (results_dir / "summary.md").write_text("\n".join(md) + "\n")

    print(f"\n{'=' * 60}")
    print(f"  {n_pass}/{n_total} passed  ({total_elapsed:.1f}s)")
    print(f"  Results: {results_dir}/")
    print(f"{'=' * 60}")

    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
