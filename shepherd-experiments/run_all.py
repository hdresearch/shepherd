"""Run all shepherd-experiments and persist results to results/<timestamp>/.

Produces:
  results/<timestamp>/
    bench_scope_lifecycle.json
    bench_trace_overhead.json
    bench_parallel_agents.json
    exp_coding_task.json
    exp_parallel_search.json
    exp_multi_step_refactor.json
    summary.md               ← human-readable overview of every run
    <name>.log               ← full stdout+stderr for each script

Usage
-----
::

    # Run everything with defaults:
    uv run python shepherd-experiments/run_all.py

    # Custom model / agent count:
    uv run python shepherd-experiments/run_all.py \\
        --model claude-opus-4-5 \\
        --n-agents 3 \\
        --bench-reps 30

    # Skip the meta-agent experiments (benchmarks only):
    uv run python shepherd-experiments/run_all.py --benchmarks-only

    # Skip benchmarks (experiments only):
    uv run python shepherd-experiments/run_all.py --experiments-only
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_RESULTS_ROOT = _HERE / "results"


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------

def _run(
    label: str,
    cmd: list[str],
    run_dir: Path,
) -> tuple[bool, float]:
    """Run a subprocess, tee output to console + log file. Return (ok, elapsed)."""
    log_path = run_dir / f"{label}.log"
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    t0 = time.perf_counter()
    with log_path.open("w") as log:
        proc = subprocess.run(
            cmd,
            cwd=str(_HERE),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log.write(proc.stdout)
        # Also print to terminal
        for line in proc.stdout.splitlines():
            print(f"  {line}")
    elapsed = time.perf_counter() - t0
    ok = proc.returncode == 0
    status = "✓ OK" if ok else f"✗ FAILED (exit {proc.returncode})"
    print(f"\n  [{status}]  {elapsed:.1f}s  →  log: {log_path.relative_to(_HERE)}")
    return ok, elapsed


def _load_json(run_dir: Path, name: str) -> dict | None:
    candidates = sorted(run_dir.glob(f"{name}_*.json"))
    if not candidates:
        # Also try without timestamp suffix (direct writes)
        direct = run_dir / f"{name}.json"
        if direct.exists():
            return json.loads(direct.read_text())
        return None
    return json.loads(candidates[-1].read_text())


# ---------------------------------------------------------------------------
# Summary generator
# ---------------------------------------------------------------------------

def _make_summary(run_dir: Path, run_ts: str, timings: dict[str, tuple[bool, float]]) -> str:
    lines: list[str] = []

    def h(text: str, level: int = 2) -> None:
        lines.append(("#" * level) + " " + text)

    def p(text: str) -> None:
        lines.append(text)

    def blank() -> None:
        lines.append("")

    h("Shepherd Experiment Run", 1)
    blank()
    p(f"**Timestamp:** `{run_ts}`  ")
    p(f"**Platform:** `{_platform_str()}`  ")
    p(f"**Python:** `{sys.version.split()[0]}`")
    blank()

    # ── Status table ──
    h("Run Status")
    blank()
    p("| Experiment / Benchmark | Status | Wall time |")
    p("|---|---|---|")
    for name, (ok, elapsed) in timings.items():
        status = "✅ OK" if ok else "❌ FAILED"
        p(f"| `{name}` | {status} | {elapsed:.1f}s |")
    blank()

    # ── Benchmark results ──
    h("Benchmark Results")
    blank()

    scope = _load_json(run_dir, "bench_scope_lifecycle")
    if scope:
        h("Scope lifecycle latency", 3)
        blank()
        p("| Operation | mean ms | p50 ms | p90 ms | p99 ms |")
        p("|---|---|---|---|---|")
        for key, label in [
            ("cold_startup", "cold startup"),
            ("fork", "fork"),
            ("discard", "discard"),
            ("roundtrip_fork_write_merge", "roundtrip (fork+write+merge)"),
        ]:
            s = scope.get(key, {})
            if s:
                p(f"| {label} | {s['mean_ms']:.1f} | {s['p50_ms']:.1f} | {s['p90_ms']:.1f} | {s['p99_ms']:.1f} |")
        blank()

    trace = _load_json(run_dir, "bench_trace_overhead")
    if trace:
        h("Trace overhead vs. plain git", 3)
        blank()
        cfg = trace.get("config", {})
        p(f"Config: {cfg.get('n_files')} file(s) × {cfg.get('file_size_kb')} KB, {cfg.get('reps')} reps")
        blank()
        p("| Condition | mean ms | p50 ms | p90 ms |")
        p("|---|---|---|---|")
        for key, label in [("plain_git", "plain git (add+commit)"),
                            ("vcscore_roundtrip", "VcsCore roundtrip")]:
            s = trace.get(key, {})
            if s:
                p(f"| {label} | {s['mean_ms']:.1f} | {s['p50_ms']:.1f} | {s['p90_ms']:.1f} |")
        p(f"\n**Trace overhead:** +{trace.get('overhead_mean_ms', '?')} ms "
          f"({trace.get('overhead_pct', '?')}% above plain git)")
        blank()

    par = _load_json(run_dir, "bench_parallel_agents")
    if par:
        h("Parallel vs. sequential speedup", 3)
        blank()
        p(f"n_agents={par.get('n_agents')}  stub_duration={par.get('stub_agent_duration_s')}s each")
        blank()
        p("| Mode | Wall time | Speedup | Efficiency |")
        p("|---|---|---|---|")
        p(f"| sequential | {par.get('sequential_wall_s')}s | 1.00× | 100% |")
        p(f"| parallel   | {par.get('parallel_wall_s')}s | {par.get('speedup')}× | {par.get('efficiency_pct')}% |")
        p(f"\nScope setup overhead (mean): {par.get('scope_setup_mean_ms')} ms")
        p(f"Overhead above critical path: {par.get('overhead_above_critical_path_ms')} ms")
        blank()

    # ── Meta-agent experiment results ──
    h("Meta-Agent Experiment Results")
    blank()

    coding = _load_json(run_dir, "exp_coding_task")
    if coding:
        h("exp_coding_task", 3)
        blank()
        p(f"**Task:** {coding.get('task')}")
        p(f"**Model:** `{coding.get('model')}`  "
          f"**n_agents:** {coding.get('n_agents')}  "
          f"**wall time:** {coding.get('wall_time_s')}s")
        blank()
        p("| Agent | Scope | File | Decision | Lines | Time |")
        p("|---|---|---|---|---|---|")
        for r in coding.get("results", []):
            decision = "✅ merged" if r.get("merged") else "❌ discarded"
            p(f"| {r.get('agent_index')} | `{r.get('scope_name')}` | `{r.get('file_written')}` "
              f"| {decision} | {r.get('code_lines')} | {r.get('elapsed_s')}s |")
        blank()

    search = _load_json(run_dir, "exp_parallel_search")
    if search:
        h("exp_parallel_search", 3)
        blank()
        p(f"**Goal:** {search.get('goal')}")
        p(f"**Model:** `{search.get('model')}`  "
          f"**n_agents:** {search.get('n_agents')}  "
          f"**selected:** #{search.get('selected_index')}  "
          f"**wall time:** {search.get('wall_time_s')}s")
        blank()
        p("| # | Hypothesis (truncated) | File | Decision | Lines | Time |")
        p("|---|---|---|---|---|---|")
        for r in search.get("results", []):
            hyp = r.get("hypothesis", "")[:70].replace("|", "\\|")
            decision = "★ merged" if r.get("merged") else "discarded"
            p(f"| {r.get('index')} | {hyp}… | `{r.get('file_written')}` "
              f"| {decision} | {r.get('code_lines')} | {r.get('elapsed_s')}s |")
        blank()

    pipeline = _load_json(run_dir, "exp_multi_step_refactor")
    if pipeline:
        h("exp_multi_step_refactor", 3)
        blank()
        p(f"**Model:** `{pipeline.get('model')}`  "
          f"**steps:** {len(pipeline.get('steps', []))}  "
          f"**completed:** {pipeline.get('completed')}  "
          f"**halted:** {pipeline.get('halted')}  "
          f"**wall time:** {pipeline.get('total_elapsed_s')}s")
        blank()
        p("| Step | Instruction (truncated) | File | Decision | Lines | Time |")
        p("|---|---|---|---|---|---|")
        for r in pipeline.get("step_results", []):
            instr = r.get("instruction", "")[:65].replace("|", "\\|")
            decision = "✅ merged" if r.get("merged") else "❌ discarded"
            p(f"| {r.get('step_index')} | {instr}… | `{r.get('file_written')}` "
              f"| {decision} | {r.get('code_lines')} | {r.get('elapsed_s')}s |")
        blank()

    h("Files in This Run")
    blank()
    for f in sorted(run_dir.iterdir()):
        size = f.stat().st_size
        p(f"- `{f.name}` ({size:,} bytes)")
    blank()

    return "\n".join(lines)


def _platform_str() -> str:
    import platform
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Run all shepherd-experiments and persist results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--model", default="claude-opus-4-5",
                   help="LLM model for meta-agent experiments (default: claude-opus-4-5)")
    p.add_argument("--n-agents", type=int, default=3,
                   help="Sub-agents per meta-agent experiment (default: 3)")
    p.add_argument("--bench-reps", type=int, default=20,
                   help="Repetitions for benchmarks (default: 20)")
    p.add_argument("--agent-duration", type=float, default=1.0,
                   help="Stub agent sleep duration for parallel bench (default: 1.0s)")
    p.add_argument("--benchmarks-only", action="store_true")
    p.add_argument("--experiments-only", action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = _RESULTS_ROOT / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Results directory: {run_dir}")

    py = sys.executable
    rd = str(run_dir)
    timings: dict[str, tuple[bool, float]] = {}

    # ── Benchmarks ──
    if not args.experiments_only:
        ok, t = _run("bench_scope_lifecycle", [
            py, "benchmarks/bench_scope_lifecycle.py",
            "--reps", str(args.bench_reps),
            "--results-dir", rd,
        ], run_dir)
        timings["bench_scope_lifecycle"] = (ok, t)

        ok, t = _run("bench_trace_overhead", [
            py, "benchmarks/bench_trace_overhead.py",
            "--reps", str(args.bench_reps),
            "--results-dir", rd,
        ], run_dir)
        timings["bench_trace_overhead"] = (ok, t)

        ok, t = _run("bench_parallel_agents", [
            py, "benchmarks/bench_parallel_agents.py",
            "--n-agents", str(args.n_agents),
            "--agent-duration", str(args.agent_duration),
            "--results-dir", rd,
        ], run_dir)
        timings["bench_parallel_agents"] = (ok, t)

    # ── Meta-agent experiments ──
    if not args.benchmarks_only:
        ok, t = _run("exp_coding_task", [
            py, "meta_agent/exp_coding_task.py",
            "--task", "Add a fibonacci function with memoization to utils.py",
            "--n-agents", str(args.n_agents),
            "--model", args.model,
            "--results-dir", rd,
        ], run_dir)
        timings["exp_coding_task"] = (ok, t)

        ok, t = _run("exp_parallel_search", [
            py, "meta_agent/exp_parallel_search.py",
            "--goal", "Implement a retry strategy for an HTTP client",
            "--n-agents", str(args.n_agents),
            "--model", args.model,
            "--results-dir", rd,
        ], run_dir)
        timings["exp_parallel_search"] = (ok, t)

        ok, t = _run("exp_multi_step_refactor", [
            py, "meta_agent/exp_multi_step_refactor.py",
            "--model", args.model,
            "--results-dir", rd,
        ], run_dir)
        timings["exp_multi_step_refactor"] = (ok, t)

    # ── Summary ──
    summary_md = _make_summary(run_dir, ts, timings)
    summary_path = run_dir / "summary.md"
    summary_path.write_text(summary_md)

    total = sum(t for _, t in timings.values())
    passed = sum(1 for ok, _ in timings.values() if ok)
    failed = len(timings) - passed

    print(f"\n{'='*70}")
    print(f"  ALL DONE  —  {passed}/{len(timings)} passed  |  {total:.1f}s total")
    print(f"  Run dir  :  {run_dir}")
    print(f"  Summary  :  {summary_path}")
    if failed:
        print(f"\n  FAILED:")
        for name, (ok, _) in timings.items():
            if not ok:
                print(f"    ✗  {name}  (see {run_dir / name}.log)")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
