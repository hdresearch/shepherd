# Shepherd Experiments

Reproducible experiments and benchmarks for the Shepherd paper (arXiv 2605.10913),
using the **Shepherd-native substrate architecture**.

## Architecture

Sub-agents run inside **Vers VM jails** via the full Shepherd substrate stack:

```
workspace.run(task_id, repo=ws.git_repo(),
    placement="auto",             # resolves to "jail" on Linux+Landlock
    runtime={"provider": "claude"})
```

Execution path:
```
workspace.run()
  → ShepherdRunDriver.prepare_bound()     ← Shepherd substrate driver
    → VcsCore.execute_recorded(           ← reversible execution trace
          "runtime", "run", ...)
      → fork scope
      → ClaudeWorkspaceRuntimeProvider.execute(
            ..., execution.working_path)  ← agent writes here
      → merge (success) / discard (failure)
```

The **overseer** runs **locally** (on the host, outside the jail) using the
Anthropic SDK directly.  It reads retained outputs via
`run.changeset().read_file(path)` (read-only, no jail required) and settles
them via `run.output().select()` / `run.output().discard()`.

## Requirements

| Requirement | Detail |
|---|---|
| Jail backend | Linux with Landlock (Vers VMs) **or** macOS with Seatbelt |
| `claude` CLI | On `PATH`; authenticated |
| `ANTHROPIC_API_KEY` | Set in environment (VMs have this automatically) |
| Python | 3.11+ inside the `uv` venv |

## Experiments

### Experiment 1 — N agents, same task (`meta_agent/exp_coding_task.py`)

N Claude agents run the same coding task in independent VcsCore scopes.
The overseer evaluates each retained output and selects the best.

```bash
uv run python3 meta_agent/exp_coding_task.py \
    --task "Add a fibonacci function with memoization to utils.py" \
    --n-agents 3
```

### Experiment 2 — Parallel hypothesis search (`meta_agent/exp_parallel_search.py`)

The overseer generates N implementation hypotheses; each is handed to a Claude
agent running in its own scope.  The best implementation is selected.

```bash
uv run python3 meta_agent/exp_parallel_search.py \
    --problem "Implement a thread-safe LRU cache" \
    --n-hypotheses 3
```

### Experiment 3 — Multi-step refactor pipeline (`meta_agent/exp_multi_step_refactor.py`)

A sequential pipeline: write tests → implement → add docstrings.  Each step
runs in a fresh scope; the overseer must accept (select) before the next step
starts.  A low-scoring step aborts the pipeline.

```bash
uv run python3 meta_agent/exp_multi_step_refactor.py \
    --function parse_csv \
    --spec "Parse a CSV string into a list of dicts"
```

## Benchmarks

### `bench_scope_lifecycle` — select vs discard latency
### `bench_trace_overhead` — Shepherd substrate overhead vs plain git
### `bench_parallel_agents` — per-run latency at N = 1, 2, 4

## Run everything

```bash
cd shepherd-experiments
uv run python3 run_all.py
# Results written to results/<timestamp>/
```

## Results

Each run produces:
```
results/<timestamp>/
  summary.md                         # human-readable table
  summary.json                       # machine-readable
  exp_coding_task/
    exp_coding_task.json
    exp_coding_task.log
  exp_parallel_search/...
  exp_multi_step_refactor/...
  bench_scope_lifecycle/...
  bench_trace_overhead/...
  bench_parallel_agents/...
```

The `.vcscore` directory in each experiment's temporary workspace records the
full VcsCore trace (run ledger, task ledger, scope DAG) and can be inspected
with `sp run list` / `sp run show <run-ref>`.
