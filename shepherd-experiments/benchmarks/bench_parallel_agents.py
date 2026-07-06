"""Benchmark: parallel vs. sequential agent wall-clock speedup.

Each "agent" is a sleep stub (--dry-run) or real litellm call, running inside
an isolated Vers scope. Reports speedup, efficiency, and scope-setup overhead.

Usage
-----
::

    python bench_parallel_agents.py --dry-run --n-agents 4 --agent-duration 2
    python bench_parallel_agents.py --dry-run --json
    python bench_parallel_agents.py --dry-run --results-dir ../results/my-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("bench_parallel_agents")


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


def _scope_setup_latency(ws: Path, n: int) -> list[float]:
    """Time just fork + worktree-add, no agent work."""
    from vcs_core import VcsCore
    vcs = _build_vcscore(ws)
    vcs.activate()
    assert isinstance(vcs, VcsCore)
    ground = vcs.ground
    wt_root = ws.parent / ".bench-setup-wt"
    wt_root.mkdir(exist_ok=True)
    times: list[float] = []
    for _ in range(n):
        uid = uuid.uuid4().hex[:6]
        wt_dir = wt_root / uid
        wt_dir.mkdir()
        t0 = time.perf_counter()
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "add", "-b", f"bench/setup/{uid}", str(wt_dir)],
            capture_output=True, check=True,
        )
        scope = vcs.fork(ground, f"setup-{uid}")
        times.append(time.perf_counter() - t0)
        vcs.discard(scope)
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
        )
    vcs.deactivate(warn_on_open_scopes=False)
    return times


# ---------------------------------------------------------------------------
# Stub agent
# ---------------------------------------------------------------------------

def _stub_agent(wt_dir: Path, idx: int, duration_s: float) -> float:
    t0 = time.perf_counter()
    time.sleep(duration_s)
    (wt_dir / f"result_{idx}.py").write_text(f"result = {idx}\n")
    subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt_dir), "commit", "-m", f"stub {idx}", "--allow-empty-message"],
        capture_output=True,
    )
    return time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Sequential mode: fork → work → merge, one at a time
# ---------------------------------------------------------------------------

def _run_sequential(ws: Path, n: int, duration_s: float) -> tuple[float, list[float]]:
    from vcs_core import VcsCore
    vcs = _build_vcscore(ws)
    vcs.activate()
    assert isinstance(vcs, VcsCore)
    ground = vcs.ground
    wt_root = ws.parent / ".bench-seq-wt"
    wt_root.mkdir(exist_ok=True)
    per_agent: list[float] = []
    wall_t0 = time.perf_counter()
    for i in range(n):
        uid = uuid.uuid4().hex[:6]
        wt_dir = wt_root / uid
        wt_dir.mkdir()
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "add", "-b", f"bench/seq/{uid}", str(wt_dir)],
            capture_output=True, check=True,
        )
        scope = vcs.fork(ground, f"seq-{uid}")
        elapsed = _stub_agent(wt_dir, i, duration_s)
        vcs.merge(scope, ground)
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
        )
        per_agent.append(elapsed)
    wall = time.perf_counter() - wall_t0
    vcs.deactivate(warn_on_open_scopes=False)
    return wall, per_agent


# ---------------------------------------------------------------------------
# Parallel mode: all agents work concurrently; merge sequentially after
# ---------------------------------------------------------------------------

def _run_parallel(ws: Path, n: int, duration_s: float) -> tuple[float, list[float]]:
    from vcs_core import VcsCore
    vcs = _build_vcscore(ws)
    vcs.activate()
    assert isinstance(vcs, VcsCore)
    ground = vcs.ground
    wt_root = ws.parent / ".bench-par-wt"
    wt_root.mkdir(exist_ok=True)

    # Pre-create all worktrees (sequential, fast)
    worktrees: list[tuple[Path, str]] = []
    for i in range(n):
        uid = uuid.uuid4().hex[:6]
        wt_dir = wt_root / uid
        wt_dir.mkdir()
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "add", "-b", f"bench/par/{uid}", str(wt_dir)],
            capture_output=True, check=True,
        )
        worktrees.append((wt_dir, uid))

    per_agent: list[float] = [0.0] * n
    wall_t0 = time.perf_counter()

    # Run agents in parallel threads (no VcsCore calls inside threads)
    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = {pool.submit(_stub_agent, wt_dir, i, duration_s): i
                for i, (wt_dir, _) in enumerate(worktrees)}
        for fut in as_completed(futs):
            per_agent[futs[fut]] = fut.result()

    # Merge sequentially (VcsCore requires it)
    for i, (wt_dir, uid) in enumerate(worktrees):
        scope = vcs.fork(ground, f"par-{uid}")
        vcs.merge(scope, ground)
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
        )

    wall = time.perf_counter() - wall_t0
    vcs.deactivate(warn_on_open_scopes=False)
    return wall, per_agent


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


def run_benchmarks(n_agents: int, duration_s: float, as_json: bool,
                   results_dir: str | None) -> dict:
    with tempfile.TemporaryDirectory(prefix="shepherd-bench-par-") as tmp:
        ws = Path(tmp)
        _init_workspace(ws)
        setup_times = _scope_setup_latency(ws, n_agents)
        seq_wall, seq_per = _run_sequential(ws, n_agents, duration_s)
        par_wall, par_per = _run_parallel(ws, n_agents, duration_s)

    setup_mean_ms = sum(setup_times) / len(setup_times) * 1000
    speedup       = seq_wall / par_wall if par_wall else 0.0
    ideal         = float(n_agents)
    efficiency    = speedup / ideal * 100
    overhead_s    = par_wall - max(par_per) if par_per else 0.0

    result = {
        "benchmark": "bench_parallel_agents",
        "timestamp": datetime.datetime.now().isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "n_agents": n_agents,
        "stub_agent_duration_s": duration_s,
        "sequential_wall_s":  round(seq_wall, 3),
        "parallel_wall_s":    round(par_wall, 3),
        "speedup":            round(speedup, 2),
        "ideal_speedup":      ideal,
        "efficiency_pct":     round(efficiency, 1),
        "overhead_above_critical_path_ms": round(overhead_s * 1000, 1),
        "scope_setup_mean_ms": round(setup_mean_ms, 2),
        "per_agent_sequential_s": [round(t, 3) for t in seq_per],
        "per_agent_parallel_s":   [round(t, 3) for t in par_per],
    }

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n=== Parallel vs. sequential agent speedup ===")
        print(f"  n_agents={n_agents}  stub_duration={duration_s}s each\n")
        print(f"  sequential wall  : {seq_wall:.3f}s")
        print(f"  parallel wall    : {par_wall:.3f}s")
        print(f"  speedup          : {speedup:.2f}x  (ideal {ideal:.0f}x)")
        print(f"  efficiency       : {efficiency:.1f}%")
        print(f"  overhead (crit.) : {overhead_s*1000:.1f}ms")
        print(f"  scope setup mean : {setup_mean_ms:.1f}ms")
        print(f"  per-agent seq    : {[f'{t:.3f}s' for t in seq_per]}")
        print(f"  per-agent par    : {[f'{t:.3f}s' for t in par_per]}")

    _save_result("bench_parallel_agents", result, results_dir)
    return result


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark: parallel vs. sequential agents.")
    p.add_argument("--n-agents", type=int, default=4)
    p.add_argument("--agent-duration", type=float, default=1.0,
                   help="Stub sleep duration per agent in seconds (default: 1.0)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--results-dir", default=None)
    return p


def main() -> None:
    args = _parser().parse_args()
    run_benchmarks(n_agents=args.n_agents, duration_s=args.agent_duration,
                   as_json=args.json, results_dir=args.results_dir)


if __name__ == "__main__":
    main()
