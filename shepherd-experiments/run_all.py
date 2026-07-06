"""Run all Shepherd experiments and benchmarks; persist results to a timestamped directory.

Each experiment uses the Shepherd-native substrate:
  - Sub-agents run inside Vers VM jails via ``workspace.run(..., runtime={"provider": "claude"})``
  - ShepherdRunDriver handles fork/merge/discard through VcsCore
  - The local overseer uses the Anthropic SDK (ANTHROPIC_API_KEY must be set)

Requirements
------------
- Jail-capable host: Linux with Landlock (Vers VMs) or macOS with Seatbelt
- ``claude`` CLI on PATH
- ``ANTHROPIC_API_KEY`` in the environment
- ``uv run python3 run_all.py`` (or ``python3 run_all.py`` inside the uv venv)

Usage
-----
::

    uv run python3 run_all.py
    uv run python3 run_all.py --results-dir my_results/ --model claude-opus-4-5
    uv run python3 run_all.py --skip-benchmarks   # experiments only
    uv run python3 run_all.py --skip-experiments  # benchmarks only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Guard: jail capability check
# ---------------------------------------------------------------------------

def _check_jail() -> bool:
    """Return True if the current host can run jailed workspace.run() calls."""
    try:
        from vcs_core.runtime_api import native_jail_available
        return native_jail_available()
    except Exception:
        return False


def _check_claude_cli() -> bool:
    """Return True if the claude CLI is available on PATH."""
    import shutil
    return shutil.which("claude") is not None


def _check_anthropic_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------
# Experiment runners (imported lazily to keep startup fast)
# ---------------------------------------------------------------------------

def _run_exp_coding_task(results_dir: Path, model: str) -> dict:
    from meta_agent.exp_coding_task import run_experiment
    return run_experiment(
        task="Add a fibonacci function with memoization to utils.py",
        n_agents=3,
        workspace_dir=None,
        results_dir=results_dir / "exp_coding_task",
        model=model,
    )


def _run_exp_parallel_search(results_dir: Path, model: str) -> dict:
    from meta_agent.exp_parallel_search import run_experiment
    return run_experiment(
        problem="Implement a thread-safe LRU cache in Python",
        n_hypotheses=3,
        workspace_dir=None,
        results_dir=results_dir / "exp_parallel_search",
        model=model,
    )


def _run_exp_multi_step(results_dir: Path, model: str) -> dict:
    from meta_agent.exp_multi_step_refactor import run_experiment
    return run_experiment(
        function_name="parse_csv",
        spec="Parse a CSV string into a list of dicts, one per row",
        workspace_dir=None,
        results_dir=results_dir / "exp_multi_step_refactor",
        model=model,
    )


def _run_bench_lifecycle(results_dir: Path) -> dict:
    import tempfile
    from benchmarks.bench_scope_lifecycle import run_benchmark
    from ws_init import seed_workspace
    with tempfile.TemporaryDirectory(prefix="shepherd-bench-lc-") as tmp:
        ws_dir = Path(tmp)
        seed_workspace(ws_dir, {"README.md": "# bench\n"})
        return run_benchmark(
            workspace_dir=ws_dir,
            n_select=3,
            n_discard=3,
            results_dir=results_dir / "bench_scope_lifecycle",
        )


def _run_bench_overhead(results_dir: Path) -> dict:
    import tempfile
    from benchmarks.bench_trace_overhead import run_benchmark
    from ws_init import seed_workspace
    with tempfile.TemporaryDirectory(prefix="shepherd-bench-oh-") as tmp:
        ws_dir = Path(tmp)
        seed_workspace(ws_dir, {"README.md": "# bench overhead\n"})
        return run_benchmark(
            workspace_dir=ws_dir,
            n_iters=3,
            results_dir=results_dir / "bench_trace_overhead",
        )


def _run_bench_agents(results_dir: Path) -> dict:
    import tempfile
    from benchmarks.bench_parallel_agents import run_benchmark
    from ws_init import seed_workspace
    with tempfile.TemporaryDirectory(prefix="shepherd-bench-ag-") as tmp:
        ws_dir = Path(tmp)
        seed_workspace(ws_dir, {"README.md": "# bench agents\n"})
        return run_benchmark(
            workspace_dir=ws_dir,
            batch_sizes=[1, 2, 4],
            results_dir=results_dir / "bench_parallel_agents",
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

EXPERIMENTS = [
    ("exp_coding_task",       _run_exp_coding_task,   True),   # (name, fn, is_experiment)
    ("exp_parallel_search",   _run_exp_parallel_search, True),
    ("exp_multi_step_refactor", _run_exp_multi_step,  True),
    ("bench_scope_lifecycle", _run_bench_lifecycle,   False),
    ("bench_trace_overhead",  _run_bench_overhead,    False),
    ("bench_parallel_agents", _run_bench_agents,      False),
]


def main() -> None:
    p = argparse.ArgumentParser(description="Run all Shepherd experiments and benchmarks")
    p.add_argument("--results-dir", type=Path, default=None,
                   help="Output directory (default: results/<timestamp>)")
    p.add_argument("--model", default="claude-opus-4-5",
                   help="Anthropic model for the local overseer")
    p.add_argument("--skip-experiments", action="store_true")
    p.add_argument("--skip-benchmarks", action="store_true")
    p.add_argument("--no-jail-check", action="store_true",
                   help="Skip preflight jail capability check (for CI/testing)")
    args = p.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    results_dir = args.results_dir or (Path("results") / ts)
    results_dir.mkdir(parents=True, exist_ok=True)

    print(f"Results directory: {results_dir}")
    print()

    # --- preflight checks ---
    preflight_ok = True

    if not _check_anthropic_key():
        print("❌ ANTHROPIC_API_KEY is not set")
        preflight_ok = False
    else:
        print("✅ ANTHROPIC_API_KEY set")

    if not _check_claude_cli():
        print("❌ claude CLI not found on PATH")
        preflight_ok = False
    else:
        print("✅ claude CLI found")

    if not args.no_jail_check:
        if not _check_jail():
            print("❌ No jail backend available (need Linux+Landlock or macOS+Seatbelt)")
            print("   Run on a Vers VM, or pass --no-jail-check to skip this check.")
            preflight_ok = False
        else:
            print("✅ Native jail backend available")

    print()
    if not preflight_ok:
        print("Preflight checks failed. Aborting.")
        sys.exit(1)

    # --- run suite ---
    summary_rows = []
    all_t0 = time.perf_counter()

    for name, fn, is_experiment in EXPERIMENTS:
        if is_experiment and args.skip_experiments:
            print(f"  SKIP  {name}")
            continue
        if not is_experiment and args.skip_benchmarks:
            print(f"  SKIP  {name}")
            continue

        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
        t0 = time.perf_counter()
        try:
            if is_experiment:
                result = fn(results_dir, args.model)
            else:
                result = fn(results_dir)
            elapsed = time.perf_counter() - t0
            status = result.get("status", "unknown")
            print(f"\n  → {status.upper()}  ({elapsed:.1f}s)")
            summary_rows.append({"name": name, "status": status, "elapsed_s": round(elapsed, 1)})
        except Exception:
            elapsed = time.perf_counter() - t0
            tb = traceback.format_exc()
            print(f"\n  → ERROR  ({elapsed:.1f}s)")
            print(tb)
            summary_rows.append({
                "name": name,
                "status": "error",
                "elapsed_s": round(elapsed, 1),
                "traceback": tb,
            })

    total_elapsed = time.perf_counter() - all_t0

    # --- summary ---
    n_pass = sum(1 for r in summary_rows if r["status"] == "pass")
    n_total = len(summary_rows)

    summary = {
        "timestamp": ts,
        "model": args.model,
        "results_dir": str(results_dir),
        "total_elapsed_s": round(total_elapsed, 1),
        "passed": n_pass,
        "total": n_total,
        "rows": summary_rows,
    }
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Human-readable summary.md
    md_lines = [
        f"# Shepherd Experiment Results",
        f"",
        f"**Timestamp:** {ts}  ",
        f"**Model:** {args.model}  ",
        f"**Passed:** {n_pass}/{n_total}  ",
        f"**Total time:** {total_elapsed:.1f}s",
        f"",
        f"## Results",
        f"",
        f"| Experiment | Status | Time |",
        f"|---|---|---|",
    ]
    for r in summary_rows:
        icon = "✅" if r["status"] == "pass" else "❌"
        md_lines.append(f"| {r['name']} | {icon} {r['status']} | {r['elapsed_s']}s |")

    md_lines += [
        f"",
        f"## Architecture",
        f"",
        f"Sub-agents run inside **Vers VM jails** via:",
        f"```",
        f"workspace.run(task_id, repo=ws.git_repo(),",
        f"    placement='auto',           # → 'jail' on Linux+Landlock",
        f"    runtime={{'provider': 'claude'}})  # Claude CLI in the jail",
        f"```",
        f"",
        f"Execution path: `workspace.run()` → `ShepherdRunDriver.prepare_bound()`",
        f"→ `VcsCore.execute_recorded('runtime', 'run', ...)` → fork scope →",
        f"Claude writes to `execution.working_path` → merge (success) / discard (failure).",
        f"",
        f"The overseer runs locally via the Anthropic SDK and reads retained",
        f"outputs through `run.changeset().read_file(path)` before settling.",
    ]
    (results_dir / "summary.md").write_text("\n".join(md_lines) + "\n")

    print(f"\n{'='*60}")
    print(f"  {n_pass}/{n_total} passed  ({total_elapsed:.1f}s)")
    print(f"  Results: {results_dir}/")
    print(f"{'='*60}")

    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
