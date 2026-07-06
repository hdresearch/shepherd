"""Benchmark: Shepherd workspace.run() full lifecycle timing.

Measures the end-to-end latency of one ``workspace.run()`` call through the
Shepherd substrate (workspace open → task register → run fork → Claude agent →
scope merge → output settle).

Compares:
  A. ``output.select()``  — fork → agent → merge → select
  B. ``output.discard()`` — fork → agent → merge → discard

The "agent" here is a minimal static task so timing reflects substrate
overhead, not model generation time.  Use the meta-agent experiments for
real agent timing.

NOTE: requires a jail-capable host (Linux with Landlock or macOS with
Seatbelt) and the ``claude`` CLI on PATH with ANTHROPIC_API_KEY set.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean, median, stdev

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import shepherd as sp
from ws_init import ensure_shepherd_workspace, seed_workspace


# ---------------------------------------------------------------------------
# Minimal task (Claude creates a single trivial file, fast)
# ---------------------------------------------------------------------------

def noop_file(
    repo: sp.May[sp.GitRepo, sp.ReadWrite],
    content: str = "# benchmark",
    output_path: str = "bench_output.py",
) -> None:
    """Create a file at ``output_path`` containing exactly ``content``."""


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def run_benchmark(
    *,
    workspace_dir: Path,
    n_select: int = 5,
    n_discard: int = 5,
    results_dir: Path,
) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "bench_scope_lifecycle.log"
    log = open(log_path, "w")

    def _log(msg: str) -> None:
        print(msg)
        print(msg, file=log, flush=True)

    _log("=== Benchmark: Scope lifecycle ===")
    _log(f"workspace : {workspace_dir}")
    _log(f"n_select  : {n_select}   n_discard: {n_discard}")

    ws = ensure_shepherd_workspace(workspace_dir)
    try:
        ws.tasks.register(noop_file, task_id="bench.noop_file", may_default="ReadWrite")

        select_times: list[float] = []
        discard_times: list[float] = []

        _log("\n-- select path --")
        for i in range(n_select):
            t0 = time.perf_counter()
            run = ws.run(
                "bench.noop_file",
                repo=ws.git_repo(),
                args={"content": f"# select iter {i}", "output_path": "bench_output.py"},
                placement="auto",
                runtime={"provider": "claude"},
            )
            run.output().select()
            elapsed = time.perf_counter() - t0
            select_times.append(elapsed)
            _log(f"  iter {i:2d}: {elapsed*1000:.1f} ms")
            # Re-acquire after select so next run sees updated ground.
            # (workspace.git_repo() re-reads the selected state)

        _log("\n-- discard path --")
        for i in range(n_discard):
            t0 = time.perf_counter()
            run = ws.run(
                "bench.noop_file",
                repo=ws.git_repo(),
                args={"content": f"# discard iter {i}", "output_path": "bench_discard.py"},
                placement="auto",
                runtime={"provider": "claude"},
            )
            run.output().discard()
            elapsed = time.perf_counter() - t0
            discard_times.append(elapsed)
            _log(f"  iter {i:2d}: {elapsed*1000:.1f} ms")

    finally:
        ws.close()
        log.close()

    def _stats(times: list[float]) -> dict:
        if not times:
            return {}
        ms = [t * 1000 for t in times]
        return {
            "mean_ms": round(mean(ms), 1),
            "median_ms": round(median(ms), 1),
            "stdev_ms": round(stdev(ms) if len(ms) > 1 else 0.0, 1),
            "min_ms": round(min(ms), 1),
            "max_ms": round(max(ms), 1),
            "n": len(ms),
        }

    result = {
        "benchmark": "bench_scope_lifecycle",
        "workspace": str(workspace_dir),
        "select": _stats(select_times),
        "discard": _stats(discard_times),
        "raw_select_s": [round(t, 4) for t in select_times],
        "raw_discard_s": [round(t, 4) for t in discard_times],
        "status": "pass",
    }

    _log(f"\nSELECT  mean={result['select'].get('mean_ms')} ms  "
         f"median={result['select'].get('median_ms')} ms")
    _log(f"DISCARD mean={result['discard'].get('mean_ms')} ms  "
         f"median={result['discard'].get('median_ms')} ms")

    out = results_dir / "bench_scope_lifecycle.json"
    out.write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark: Shepherd scope lifecycle")
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--n-select", type=int, default=5)
    p.add_argument("--n-discard", type=int, default=5)
    p.add_argument("--results-dir", type=Path, default=Path("results/bench"))
    args = p.parse_args()

    with tempfile.TemporaryDirectory(prefix="shepherd-bench-lifecycle-") as tmp:
        ws_dir = Path(args.workspace) if args.workspace else Path(tmp)
        if not args.workspace:
            seed_workspace(ws_dir, {"README.md": "# bench workspace\n"})

        result = run_benchmark(
            workspace_dir=ws_dir,
            n_select=args.n_select,
            n_discard=args.n_discard,
            results_dir=args.results_dir,
        )
    print(f"\nResult saved → {args.results_dir}/bench_scope_lifecycle.json")
    print(f"SELECT  mean={result['select'].get('mean_ms')} ms")
    print(f"DISCARD mean={result['discard'].get('mean_ms')} ms")


if __name__ == "__main__":
    main()
