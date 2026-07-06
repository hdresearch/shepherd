"""Benchmark: N sequential Shepherd workspace.run() calls — substrate throughput.

VcsCore enforces one live child scope per parent at a time, so N agent runs are
always sequential.  This benchmark measures:

  • Per-run median latency at N = 1, 2, 4, 8
  • Total wall-clock time for a batch of N runs
  • Effective throughput (runs/minute)

The task is minimal (static file write via Claude) so the numbers reflect
substrate overhead rather than model generation time.

NOTE: requires jail-capable host + claude CLI + ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean, median

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import shepherd as sp
from ws_init import ensure_shepherd_workspace, seed_workspace


# ---------------------------------------------------------------------------
# Minimal task
# ---------------------------------------------------------------------------

def write_bench_file(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    content: str = "# bench",
    output_path: str = "bench.py",
) -> None:
    """Create a file at ``output_path`` containing ``content`` as a comment line."""


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def _run_batch(ws: object, n: int) -> tuple[float, list[float]]:
    """Run *n* sequential workspace.run() calls; return (total, per-run times)."""
    per_run: list[float] = []
    total_t0 = time.perf_counter()
    for i in range(n):
        t0 = time.perf_counter()
        run = ws.run(  # type: ignore[attr-defined]
            "bench.write_bench_file",
            repo=ws.git_repo(),  # type: ignore[attr-defined]
            args={"content": f"# batch n={n} i={i}", "output_path": f"bench_{n}_{i}.py"},
            placement="auto",
            runtime={"provider": "claude"},
        )
        # Discard so workspace state stays clean for next iteration.
        run.output().discard()
        per_run.append(time.perf_counter() - t0)
    total = time.perf_counter() - total_t0
    return total, per_run


def run_benchmark(
    *,
    workspace_dir: Path,
    batch_sizes: list[int],
    results_dir: Path,
) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "bench_parallel_agents.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Benchmark: Agent throughput ===")
    _log(f"workspace   : {workspace_dir}")
    _log(f"batch_sizes : {batch_sizes}")

    ws = ensure_shepherd_workspace(workspace_dir)
    batch_results = []

    try:
        ws.tasks.register(
            write_bench_file,
            task_id="bench.write_bench_file",
            may_default="ReadWrite",
        )

        for n in batch_sizes:
            _log(f"\n-- N={n} --")
            total, per_run = _run_batch(ws, n)
            per_ms = [t * 1000 for t in per_run]
            throughput = n / total * 60  # runs/minute
            _log(f"  total    : {total*1000:.0f} ms")
            _log(f"  per-run  : mean={mean(per_ms):.0f} ms  median={median(per_ms):.0f} ms")
            _log(f"  throughput: {throughput:.1f} runs/min")
            batch_results.append({
                "n": n,
                "total_s": round(total, 3),
                "per_run_mean_ms": round(mean(per_ms), 1),
                "per_run_median_ms": round(median(per_ms), 1),
                "throughput_per_min": round(throughput, 1),
                "raw_per_run_s": [round(t, 4) for t in per_run],
            })

    finally:
        ws.close()
        log.close()

    result = {
        "benchmark": "bench_parallel_agents",
        "workspace": str(workspace_dir),
        "batch_sizes": batch_sizes,
        "batches": batch_results,
        "note": (
            "VcsCore enforces one live child scope per parent; "
            "runs are sequential regardless of N."
        ),
        "status": "pass",
    }

    out = results_dir / "bench_parallel_agents.json"
    out.write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark: N sequential workspace.run() calls")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 2, 4])
    p.add_argument("--results-dir", type=Path, default=Path("results/bench"))
    args = p.parse_args()

    with tempfile.TemporaryDirectory(prefix="shepherd-bench-agents-") as tmp:
        ws_dir = Path(args.workspace) if args.workspace else Path(tmp)
        if not args.workspace:
            seed_workspace(ws_dir, {"README.md": "# bench agents\n"})

        result = run_benchmark(
            workspace_dir=ws_dir,
            batch_sizes=args.batch_sizes,
            results_dir=args.results_dir,
        )

    print(f"\nResult saved → {args.results_dir}/bench_parallel_agents.json")
    for b in result["batches"]:
        print(f"  N={b['n']:2d}: {b['per_run_mean_ms']:.0f} ms/run  "
              f"{b['throughput_per_min']:.1f} runs/min")


if __name__ == "__main__":
    main()
