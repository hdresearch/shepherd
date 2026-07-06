"""Benchmark: Vers trace write overhead vs. plain git baseline.

Compares:
  plain git   — file writes + git add + git commit (raw git cost only)
  VcsCore RT  — fork() + file writes + git commit inside scope + merge()

Overhead = VcsCore_roundtrip_ms - git_baseline_ms.

Usage
-----
::

    python bench_trace_overhead.py
    python bench_trace_overhead.py --files 10 --file-size-kb 4 --reps 30
    python bench_trace_overhead.py --json
    python bench_trace_overhead.py --results-dir ../results/my-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_workspace(ws: Path) -> None:
    subprocess.run(["git", "init", str(ws)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "bench@shepherd"], capture_output=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "Shepherd Bench"], capture_output=True)
    (ws / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(ws), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-m", "seed"], capture_output=True, check=True)


def _build_vcscore(ws: Path) -> object:
    from vcs_core import (
        DeclarativeFilesystemSubstrate,
        Store,
        VcsCore,
        build_builtin_substrate_context,
    )
    store = Store(str(ws / ".vcscore"))
    if store.is_empty:
        store.create_root_commit()
    ctx = build_builtin_substrate_context(store, workspace=ws)
    fs = DeclarativeFilesystemSubstrate(ctx)
    return VcsCore(str(ws), substrates=[fs], store=store)


def _write_files(target: Path, n: int, size_bytes: int, prefix: str) -> None:
    payload = "x" * size_bytes
    for i in range(n):
        (target / f"{prefix}file_{i:04d}.py").write_text(payload)


def _git_stage_commit(ws: Path, msg: str) -> None:
    subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(ws), "commit", "-m", msg, "--allow-empty-message"],
        capture_output=True, check=True,
    )


def _percentiles(samples: list[float]) -> dict[str, float]:
    s = sorted(samples)
    n = len(s)

    def _p(pct: float) -> float:
        return round(s[min(int(pct / 100.0 * n), n - 1)] * 1000, 3)

    return {
        "mean_ms":  round(statistics.mean(s) * 1000, 3),
        "stdev_ms": round(statistics.stdev(s) * 1000, 3) if n > 1 else 0.0,
        "p50_ms":   _p(50),
        "p90_ms":   _p(90),
        "p99_ms":   _p(99),
        "min_ms":   round(min(s) * 1000, 3),
        "max_ms":   round(max(s) * 1000, 3),
        "n":        n,
    }


# ---------------------------------------------------------------------------
# Baseline: plain git add + commit
# ---------------------------------------------------------------------------

def bench_plain_git(ws: Path, n_files: int, size_bytes: int, reps: int) -> dict[str, float]:
    times: list[float] = []
    for rep in range(reps):
        t0 = time.perf_counter()
        _write_files(ws, n_files, size_bytes, prefix=f"g{rep}_")
        _git_stage_commit(ws, f"baseline {rep}")
        times.append(time.perf_counter() - t0)
    return _percentiles(times)


# ---------------------------------------------------------------------------
# VcsCore roundtrip: fork → write inside worktree → commit → merge
# ---------------------------------------------------------------------------

def bench_vcscore_roundtrip(ws: Path, n_files: int, size_bytes: int, reps: int) -> dict[str, float]:
    from vcs_core import VcsCore
    vcs = _build_vcscore(ws)
    vcs.activate()
    assert isinstance(vcs, VcsCore)
    ground = vcs.ground
    worktrees_root = ws.parent / ".bench-trace-wt"
    worktrees_root.mkdir(exist_ok=True)
    times: list[float] = []
    for rep in range(reps):
        uid = uuid.uuid4().hex[:6]
        branch = f"bench/trace/{uid}"
        wt_dir = worktrees_root / uid
        wt_dir.mkdir()
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "add", "-b", branch, str(wt_dir)],
            capture_output=True, check=True,
        )
        t0 = time.perf_counter()
        scope = vcs.fork(ground, f"bench-trace-{uid}")
        _write_files(wt_dir, n_files, size_bytes, prefix=f"t{rep}_")
        _git_stage_commit(wt_dir, f"trace {rep}")
        vcs.merge(scope, ground)
        times.append(time.perf_counter() - t0)
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
        )
    vcs.deactivate(warn_on_open_scopes=False)
    return _percentiles(times)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _save_result(name: str, data: dict, results_dir: str | None) -> None:
    if not results_dir:
        return
    rd = Path(results_dir)
    rd.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = rd / f"{name}_{ts}.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"Persisted → {out}")


def run_benchmarks(n_files: int, file_size_kb: float, reps: int,
                   as_json: bool, results_dir: str | None) -> dict:
    size_bytes = int(file_size_kb * 1024)
    with tempfile.TemporaryDirectory(prefix="shepherd-bench-trace-") as tmp:
        ws = Path(tmp)
        _init_workspace(ws)
        git_stats = bench_plain_git(ws, n_files, size_bytes, reps)
        vcs_stats = bench_vcscore_roundtrip(ws, n_files, size_bytes, reps)

    overhead_ms  = vcs_stats["mean_ms"] - git_stats["mean_ms"]
    overhead_pct = (overhead_ms / git_stats["mean_ms"] * 100) if git_stats["mean_ms"] else 0.0

    result = {
        "benchmark": "bench_trace_overhead",
        "timestamp": datetime.datetime.now().isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "config": {
            "n_files": n_files,
            "file_size_kb": file_size_kb,
            "payload_kb_total": n_files * file_size_kb,
            "reps": reps,
        },
        "plain_git":         git_stats,
        "vcscore_roundtrip": vcs_stats,
        "overhead_mean_ms":  round(overhead_ms, 3),
        "overhead_pct":      round(overhead_pct, 1),
    }

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        try:
            from tabulate import tabulate  # type: ignore[import-untyped,import-not-found,unused-ignore]
            rows = [
                ["plain git (add+commit)",
                 f"{git_stats['mean_ms']:.1f}", f"{git_stats['stdev_ms']:.1f}",
                 f"{git_stats['p50_ms']:.1f}", f"{git_stats['p90_ms']:.1f}", f"{git_stats['p99_ms']:.1f}"],
                ["vcscore roundtrip",
                 f"{vcs_stats['mean_ms']:.1f}", f"{vcs_stats['stdev_ms']:.1f}",
                 f"{vcs_stats['p50_ms']:.1f}", f"{vcs_stats['p90_ms']:.1f}", f"{vcs_stats['p99_ms']:.1f}"],
            ]
            print(f"\n=== Vers trace overhead vs. plain git ===")
            print(f"  {n_files} file(s) × {file_size_kb:.1f} KB = {n_files*file_size_kb:.1f} KB/run  |  {reps} reps\n")
            print(tabulate(rows, headers=["condition","mean ms","std ms","p50","p90","p99"], tablefmt="github"))
        except ImportError:
            print(f"\n  plain git  mean={git_stats['mean_ms']:.1f}ms  p50={git_stats['p50_ms']:.1f}")
            print(f"  vcscore    mean={vcs_stats['mean_ms']:.1f}ms  p50={vcs_stats['p50_ms']:.1f}")
        print(f"\n  trace overhead : +{overhead_ms:.1f}ms  ({overhead_pct:.1f}% above plain git)")

    _save_result("bench_trace_overhead", result, results_dir)
    return result


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark: Vers trace overhead vs. plain git.")
    p.add_argument("--files", type=int, default=1)
    p.add_argument("--file-size-kb", type=float, default=1.0)
    p.add_argument("--reps", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.add_argument("--results-dir", default=None)
    return p


def main() -> None:
    args = _parser().parse_args()
    run_benchmarks(n_files=args.files, file_size_kb=args.file_size_kb,
                   reps=args.reps, as_json=args.json, results_dir=args.results_dir)


if __name__ == "__main__":
    main()
