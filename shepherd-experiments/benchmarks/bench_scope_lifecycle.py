"""Benchmark: Vers scope lifecycle latency — fork / merge / discard.

Measures the per-operation wall-clock cost of the three core Vers primitives
that the Shepherd meta-agent runtime sits on top of. These numbers correspond
to the "framework-performance microbenchmarks" section of the paper.

Metrics reported
----------------
- ``cold_startup``               : VcsCore init + activate + deactivate
- ``fork``                       : VcsCore.fork() in isolation
- ``discard``                    : VcsCore.discard() in isolation
- ``roundtrip (fork+write+merge)``: fork → small file write + git commit → merge

All times in milliseconds; table shows mean ± std and p50/p90/p99.

Usage
-----
::

    python bench_scope_lifecycle.py
    python bench_scope_lifecycle.py --reps 50
    python bench_scope_lifecycle.py --json
    python bench_scope_lifecycle.py --results-dir ../results/my-run
"""

from __future__ import annotations

import argparse
import json
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
# Workspace + VcsCore helpers
# ---------------------------------------------------------------------------

def _init_workspace(ws: Path) -> None:
    subprocess.run(["git", "init", str(ws)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "bench@shepherd"], capture_output=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "Shepherd Bench"], capture_output=True)
    (ws / "seed.txt").write_text("shepherd benchmark seed\n")
    subprocess.run(["git", "-C", str(ws), "add", "seed.txt"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-m", "bench: seed"], capture_output=True, check=True)


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


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

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
# Individual benchmarks
# ---------------------------------------------------------------------------

def bench_cold_startup(ws: Path, reps: int) -> dict[str, float]:
    times: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        vcs = _build_vcscore(ws)
        vcs.activate()
        vcs.deactivate(warn_on_open_scopes=False)
        times.append(time.perf_counter() - t0)
    return _percentiles(times)


def bench_fork(vcs: object, ground: object, reps: int) -> tuple[dict[str, float], list[object]]:
    from vcs_core import VcsCore
    assert isinstance(vcs, VcsCore)
    times: list[float] = []
    scopes: list[object] = []
    for _ in range(reps):
        name = f"bench-fork-{uuid.uuid4().hex[:6]}"
        t0 = time.perf_counter()
        scope = vcs.fork(ground, name)
        times.append(time.perf_counter() - t0)
        # Must discard immediately — VcsCore only allows one live child at a time
        vcs.discard(scope)
        scopes.append(scope)
    return _percentiles(times), scopes


def bench_discard(vcs: object, ground: object, reps: int) -> dict[str, float]:
    """Measure discard: fork then immediately discard, timing only the discard."""
    from vcs_core import VcsCore
    assert isinstance(vcs, VcsCore)
    times: list[float] = []
    for _ in range(reps):
        name = f"bench-discard-{uuid.uuid4().hex[:6]}"
        scope = vcs.fork(ground, name)
        t0 = time.perf_counter()
        vcs.discard(scope)
        times.append(time.perf_counter() - t0)
    return _percentiles(times)


def bench_roundtrip(vcs: object, ground: object, ws: Path, reps: int) -> dict[str, float]:
    """fork → write small file → git commit → merge."""
    from vcs_core import VcsCore
    assert isinstance(vcs, VcsCore)
    worktrees_root = ws.parent / ".bench-worktrees"
    worktrees_root.mkdir(exist_ok=True)
    times: list[float] = []
    for i in range(reps):
        uid = uuid.uuid4().hex[:6]
        branch = f"bench/rt/{uid}"
        wt_dir = worktrees_root / uid
        wt_dir.mkdir()
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "add", "-b", branch, str(wt_dir)],
            capture_output=True, check=True,
        )
        t0 = time.perf_counter()
        scope = vcs.fork(ground, f"bench-rt-{uid}")
        f = wt_dir / f"bench_{uid}.py"
        f.write_text(f"# roundtrip {i}\nval = {i}\n")
        subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True)
        subprocess.run(
            ["git", "-C", str(wt_dir), "commit", "-m", f"rt {i}", "--allow-empty-message"],
            capture_output=True,
        )
        vcs.merge(scope, ground)
        times.append(time.perf_counter() - t0)
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
        )
    return _percentiles(times)


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _print_table(results: dict[str, dict[str, float]]) -> None:
    try:
        from tabulate import tabulate  # type: ignore[import-untyped,import-not-found,unused-ignore]
        rows = [
            [op,
             f"{s['mean_ms']:.1f}", f"{s['stdev_ms']:.1f}",
             f"{s['p50_ms']:.1f}", f"{s['p90_ms']:.1f}", f"{s['p99_ms']:.1f}",
             f"{s['min_ms']:.1f}", f"{s['max_ms']:.1f}", s["n"]]
            for op, s in results.items()
        ]
        print(tabulate(rows,
                       headers=["operation", "mean ms", "std ms", "p50", "p90", "p99", "min", "max", "n"],
                       tablefmt="github"))
    except ImportError:
        print(f"{'operation':<32} {'mean':>8} {'std':>8} {'p50':>7} {'p90':>7} {'n':>4}")
        print("-" * 70)
        for op, s in results.items():
            print(f"{op:<32} {s['mean_ms']:>8.1f} {s['stdev_ms']:>8.1f}"
                  f" {s['p50_ms']:>7.1f} {s['p90_ms']:>7.1f} {s['n']:>4}")


def _save_result(name: str, data: dict, results_dir: str | None) -> None:
    import datetime
    if not results_dir:
        return
    rd = Path(results_dir)
    rd.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = rd / f"{name}_{ts}.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"Persisted → {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_benchmarks(reps: int, as_json: bool, results_dir: str | None) -> dict:
    import platform, datetime
    with tempfile.TemporaryDirectory(prefix="shepherd-bench-lifecycle-") as tmp:
        ws = Path(tmp)
        _init_workspace(ws)
        cold  = bench_cold_startup(ws, reps)
        vcs   = _build_vcscore(ws)
        vcs.activate()
        ground = vcs.ground
        fork_stats, _     = bench_fork(vcs, ground, reps)
        discard_stats     = bench_discard(vcs, ground, reps)
        rt_stats          = bench_roundtrip(vcs, ground, ws, min(reps, 15))
        vcs.deactivate(warn_on_open_scopes=False)

    result = {
        "benchmark": "bench_scope_lifecycle",
        "timestamp": datetime.datetime.now().isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "reps": reps,
        "cold_startup":              cold,
        "fork":                      fork_stats,
        "discard":                   discard_stats,
        "roundtrip_fork_write_merge": rt_stats,
    }

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print("\n=== Vers scope lifecycle latency ===\n")
        _print_table({
            "cold_startup":               cold,
            "fork":                       fork_stats,
            "discard":                    discard_stats,
            "roundtrip (fork+write+merge)": rt_stats,
        })
        print("\nAll times in milliseconds.")

    _save_result("bench_scope_lifecycle", result, results_dir)
    return result


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark: Vers scope lifecycle latency.")
    p.add_argument("--reps", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.add_argument("--results-dir", default=None,
                   help="Directory to persist JSON results")
    return p


def main() -> None:
    args = _parser().parse_args()
    run_benchmarks(reps=args.reps, as_json=args.json, results_dir=args.results_dir)


if __name__ == "__main__":
    main()
