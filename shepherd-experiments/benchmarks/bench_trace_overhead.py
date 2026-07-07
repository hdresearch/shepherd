"""Benchmark: Shepherd substrate trace overhead vs plain git.

Compares the latency of:
  A. Shepherd workspace.run() — full substrate path (ShepherdRunDriver +
     VcsCore execute_recorded + scope fork/merge + trace append)
  B. Plain git operations — git worktree add + file write + git commit +
     git worktree remove (equivalent work, no Shepherd overhead)

This quantifies the substrate cost described in §5 of the Shepherd paper.

Uses the static provider (no LLM call) so timing reflects substrate
overhead only, not model generation time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean, median, stdev

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import shepherd as sp
from ws_init import ensure_shepherd_workspace, seed_workspace


# ── Shepherd path ────────────────────────────────────────────────────────────

def write_marker(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    marker: str = "bench",
    output_path: str = "bench_marker.py",
) -> None:
    """Create a file at ``output_path`` containing only ``marker`` as a comment."""


def _shepherd_roundtrip(ws: object, iteration: int) -> float:
    """One full workspace.run() + discard round-trip; return elapsed seconds."""
    t0 = time.perf_counter()
    run = ws.run(  # type: ignore[attr-defined]
        "bench.write_marker",
        repo=ws.git_repo(),  # type: ignore[attr-defined]
        args={"marker": f"iter_{iteration}", "output_path": f"bench_{iteration}.py"},
        placement="auto",
        runtime={"provider": "static"},
    )
    run.output().discard()
    return time.perf_counter() - t0


# ── Plain git path ───────────────────────────────────────────────────────────

def _git_roundtrip(ws_dir: Path, iteration: int) -> float:
    """Equivalent work via plain git worktree; return elapsed seconds."""
    branch = f"bench-plain-{iteration}"
    wt_dir = ws_dir / f".bench_wt_{iteration}"
    t0 = time.perf_counter()
    try:
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(wt_dir)],
            cwd=ws_dir, check=True, capture_output=True,
        )
        (wt_dir / f"bench_{iteration}.py").write_text(f"# iter_{iteration}\n")
        subprocess.run(["git", "add", "-A"], cwd=wt_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"bench {iteration}"],
            cwd=wt_dir, check=True, capture_output=True,
        )
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_dir)],
            cwd=ws_dir, check=False, capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=ws_dir, check=False, capture_output=True,
        )
    return time.perf_counter() - t0


# ── Benchmark ────────────────────────────────────────────────────────────────

def run_benchmark(
    *,
    workspace_dir: Path,
    n_iters: int = 5,
    results_dir: Path,
) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "bench_trace_overhead.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Benchmark: Trace overhead ===")
    _log(f"workspace : {workspace_dir}  n_iters: {n_iters}")

    shepherd_times: list[float] = []
    git_times: list[float] = []

    ws = ensure_shepherd_workspace(workspace_dir)
    try:
        ws.tasks.register(write_marker, task_id="bench.write_marker", may_default="ReadWrite")

        _log("\n-- Shepherd substrate path --")
        for i in range(n_iters):
            t = _shepherd_roundtrip(ws, i)
            shepherd_times.append(t)
            _log(f"  iter {i:2d}: {t*1000:.1f} ms")

    finally:
        ws.close()
        log.close()

    # Re-open log for git path (ws already closed)
    log = open(log_path, "a")
    _log("\n-- Plain git path --")
    for i in range(n_iters):
        t = _git_roundtrip(workspace_dir, i)
        git_times.append(t)
        _log(f"  iter {i:2d}: {t*1000:.1f} ms")
    log.close()

    def _stats(times: list[float]) -> dict:
        ms = [t * 1000 for t in times]
        return {
            "mean_ms": round(mean(ms), 1),
            "median_ms": round(median(ms), 1),
            "stdev_ms": round(stdev(ms) if len(ms) > 1 else 0.0, 1),
            "min_ms": round(min(ms), 1),
            "max_ms": round(max(ms), 1),
        }

    s_stats = _stats(shepherd_times)
    g_stats = _stats(git_times)
    overhead_ms = s_stats["mean_ms"] - g_stats["mean_ms"]
    overhead_pct = (overhead_ms / g_stats["mean_ms"] * 100) if g_stats["mean_ms"] > 0 else 0

    result = {
        "benchmark": "bench_trace_overhead",
        "workspace": str(workspace_dir),
        "n_iters": n_iters,
        "shepherd": s_stats,
        "git_baseline": g_stats,
        "overhead_ms": round(overhead_ms, 1),
        "overhead_pct": round(overhead_pct, 1),
        "raw_shepherd_s": [round(t, 4) for t in shepherd_times],
        "raw_git_s": [round(t, 4) for t in git_times],
        "status": "pass",
    }

    print(f"\nShepherd mean : {s_stats['mean_ms']} ms")
    print(f"Git baseline  : {g_stats['mean_ms']} ms")
    print(f"Overhead      : +{overhead_ms:.1f} ms (+{overhead_pct:.1f}%)")

    out = results_dir / "bench_trace_overhead.json"
    out.write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark: Shepherd trace overhead vs git")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--n-iters", type=int, default=5)
    p.add_argument("--results-dir", type=Path, default=Path("results/bench"))
    args = p.parse_args()

    with tempfile.TemporaryDirectory(prefix="shepherd-bench-overhead-") as tmp:
        ws_dir = Path(args.workspace) if args.workspace else Path(tmp)
        if not args.workspace:
            seed_workspace(ws_dir, {"README.md": "# bench overhead\n"})

        result = run_benchmark(
            workspace_dir=ws_dir,
            n_iters=args.n_iters,
            results_dir=args.results_dir,
        )
    print(f"\nResult saved → {args.results_dir}/bench_trace_overhead.json")


if __name__ == "__main__":
    main()
