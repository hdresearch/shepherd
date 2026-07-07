# Shepherd Experiment Results — Vers Substrate

**Timestamp:** `2026-07-07T00-55-57`  
**Platform:** macOS (arm64), Seatbelt jail  
**Substrate:** Vers VM (VcsCore) — toplevel Shepherd running locally  
**Model:** `claude-opus-4-5`  
**Passed:** 6/6  
**Total time:** 682.3s

## Benchmark Results

### Scope Lifecycle Latency (static provider, no LLM)

Full `workspace.run()` → settle cycle through the Vers substrate.

| Operation | mean (ms) | median (ms) | stdev (ms) | min (ms) | max (ms) |
|---|---|---|---|---|---|
| **select** (fork → write → merge → select) | 1700.2 | 1692.1 | 185.1 | 1519.3 | 1889.3 |
| **discard** (fork → write → merge → discard) | 1566.9 | 1564.6 | 53.4 | 1514.6 | 1621.4 |

### Trace Overhead vs. Plain Git

| Condition | mean (ms) | median (ms) | stdev (ms) |
|---|---|---|---|
| Shepherd substrate roundtrip | 1251.2 | 1257.2 | 57.9 |
| Plain git (worktree add + commit + remove) | 61.5 | 60.7 | 1.6 |

**Substrate overhead:** +1189.7 ms (+1934.5% above plain git)

The overhead reflects the full Vers VM substrate stack: scope fork, jail
confinement, execution recording, trace append, and scope merge/discard.
This is the real cost of the reversible execution substrate.

### Agent Throughput (sequential, static provider)

| N | Total (s) | Per-run mean (ms) | Per-run median (ms) | Throughput (runs/min) |
|---|---|---|---|---|
| 1 | 1.19 | 1190.4 | 1190.4 | 50.4 |
| 2 | 2.56 | 1279.4 | 1279.4 | 46.9 |
| 4 | 5.69 | 1422.8 | 1412.8 | 42.2 |

Linear scaling (sequential by VcsCore design); slight per-run overhead increase
at higher N from accumulated scope DAG state.

## Meta-Agent Experiment Results

### Experiment 1 — N Agents, Same Task

**Task:** Add a fibonacci function with memoization to solution.py  
**n_agents:** 3 | **wall time:** 73.2s | **winner:** ✅ run-c96a9619ac15 (score 10.0)

| Agent | Run ref | Changed | Score | Time | Decision |
|---|---|---|---|---|---|
| 0 | `run-c96a9619ac15` | `solution.py` | 10.0 | 23.2s | ★ selected |
| 1 | `run-8297b6f0a338` | `solution.py` | 10.0 | 24.5s | discarded |
| 2 | `run-9c77e8e48b61` | `solution.py` | 10.0 | 16.2s | discarded |

All three agents produced perfect solutions; the overseer selected the first
(tie-breaking by order).

### Experiment 2 — Parallel Hypothesis Search

**Problem:** Implement a thread-safe LRU cache in Python  
**n_hypotheses:** 3 | **wall time:** 232.0s | **winner:** none (truncation)

| # | Hypothesis | Run ref | Score | Time | Decision |
|---|---|---|---|---|---|
| 0 | Threading Lock + OrderedDict | `run-57f109fb497e` | 3.0 | 62.8s | discarded |
| 1 | Read-Write Lock + DLL + HashMap | `run-303e3a066382` | 3.0 | 85.2s | discarded |
| 2 | Segmented/Sharded + Fine-Grained Locking | `run-e1cad15cff47` | 2.0 | 61.5s | discarded |

All implementations were truncated (7–15 KB files cut off mid-code).  This is
a retained-output read preview artifact — the changeset contains the full file
but the 4 KB evaluation preview truncates complex implementations.  The overseer
correctly identified the truncation and declined to select any incomplete output.

### Experiment 3 — Multi-Step Refactor Pipeline

**Function:** `parse_csv` — Parse a CSV string into a list of dicts  
**Pipeline:** 3 steps | **completed:** 3/3 ✅ | **wall time:** 351.4s

| Step | Task | Run ref | Score | Time | Decision |
|---|---|---|---|---|---|
| 1 | write_tests | `run-f5bb58bb0cab` | 9.0 | 48.2s | ✅ selected |
| 2 | implement_function | `run-2af34d831000` | 9.0 | 120.8s | ✅ selected |
| 3 | add_docstrings | `run-f1aa511948ae` | 10.0 | 171.2s | ✅ selected |

Full pipeline completed: tests → implementation → docstrings.  Each step
built on the previous step's selected output via the VcsCore scope chain.

## Architecture

```
workspace.run(task_id, repo=ws.git_repo(),
    placement="auto",              # → Seatbelt jail on macOS
    runtime={"provider": "claude"})  # Claude CLI in the jail
```

Execution path:
```
workspace.run()
  → ShepherdRunDriver.prepare_bound()     ← Shepherd substrate driver
    → VcsCore.execute_recorded(           ← reversible execution trace
          "runtime", "run", ...)
      → fork scope
      → ClaudeWorkspaceRuntimeProvider.execute(
            ..., execution.working_path)  ← agent writes here (jailed)
      → merge (success) / discard (failure)
```

The overseer runs **locally** on the host (outside the jail) via the Anthropic
SDK. It reads retained outputs through `run.changeset().read_file(path)`
(read-only) and settles via `run.output().select()` / `run.output().discard()`.

## Comparison with Previous Run (simulated substrate)

| Metric | Previous (simulated) | Current (Vers) | Notes |
|---|---|---|---|
| Scope select latency | ~48.6 ms | ~1700 ms | Full Vers VM substrate |
| Scope discard latency | ~12.7 ms | ~1567 ms | Jail + trace overhead |
| Trace overhead vs git | +11.9 ms (+55%) | +1190 ms (+1935%) | Real confinement cost |
| Throughput (N=1) | — | 50.4 runs/min | Sequential by design |
| Exp 1 winner score | 10.0 | 10.0 | Consistent quality |
| Exp 3 pipeline complete | 2/3 steps | 3/3 steps | Full pipeline now |

The substrate overhead is ~35× higher than the simulated path, reflecting the
real cost of Vers VM jailing, execution recording, and scope management.
