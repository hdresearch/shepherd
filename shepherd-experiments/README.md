# shepherd-experiments

Experiment code for reproducing results similar to those in:

> **Shepherd: Enabling Programmable Meta-Agents via Reversible Agentic Execution Traces**  
> Simon Yu et al., 2026 — https://arxiv.org/abs/2605.10913

This repository targets the **modified Shepherd** architecture where:

- **Sub-agents** are isolated inside **Vers infra (vcs-core)** scopes — each
  agent gets a git worktree forked from ground; its changes are captured
  implicitly at merge / discarded atomically.
- **The top-level overseer** runs **locally** — it decomposes tasks, launches
  sub-agents in parallel Vers scopes, reads their diffs, and uses a local or
  API-accessible LLM to decide merge vs. discard.

The entry point is `shepherd.vers_backend.VersShepherd` (already in the
main workspace at `shepherd/packages/meta/src/shepherd/vers_backend.py`).

---

## Directory layout

```
shepherd-experiments/
├── README.md                     ← you are here
├── requirements.txt              ← pinned experiment deps (litellm, etc.)
├── meta_agent/
│   ├── exp_coding_task.py        ← N sub-agents tackle a coding task; overseer picks best
│   ├── exp_parallel_search.py    ← parallel hypothesis exploration + selection
│   └── exp_multi_step_refactor.py← multi-step refactor with per-step scope isolation
└── benchmarks/
    ├── bench_scope_lifecycle.py  ← fork / merge / discard latency (µs)
    ├── bench_parallel_agents.py  ← parallel vs. sequential wall-clock speedup
    └── bench_trace_overhead.py   ← trace write throughput vs. plain git baseline
```

---

## Relation to the paper's experiments

The paper reports two families of results:

| Paper section | What it measures | Experiment here |
|---|---|---|
| Meta-agent applications | Overseer selects the best sub-agent solution across N parallel attempts | `meta_agent/exp_coding_task.py`, `exp_parallel_search.py` |
| Multi-step agentic work | Reversible scopes across a pipeline of sequential agent steps | `meta_agent/exp_multi_step_refactor.py` |
| Framework-perf microbenchmarks | fork/merge/discard latency; trace overhead vs. baseline | `benchmarks/` |

The paper used the upstream Shepherd + Seatbelt sandbox. Here the isolation is
git worktrees (cross-platform, no FUSE required), so wall-clock latency numbers
will differ — what stays comparable is the **relative overhead** of the trace
layer and the **merge/discard decision** quality.

---

## Prerequisites

```bash
# From the repo root — install the full local development closure
pip install -r requirements-dev.txt

# Extra experiment deps
pip install -r shepherd-experiments/requirements.txt

# For the real-agent experiments: Claude CLI or ANTHROPIC_API_KEY
# For the overseer + planning steps: any litellm-compatible model, e.g.:
#   export ANTHROPIC_API_KEY=...       # Claude as overseer
#   ollama pull mistral               # local overseer (no key needed)
```

---

## Quickstart

### Offline / deterministic smoke (no API key needed)

```bash
python shepherd-experiments/benchmarks/bench_scope_lifecycle.py
python shepherd-experiments/benchmarks/bench_trace_overhead.py
```

Both benchmarks create a temporary git repo, run the Vers scope lifecycle, and
print a timing table — no LLM needed.

### Parallel agent experiment (needs ANTHROPIC_API_KEY or claude CLI)

```bash
# 3 sub-agents tackle the same coding task in parallel; overseer merges the best:
python shepherd-experiments/meta_agent/exp_coding_task.py \
    --task "Add a fibonacci function with memoization to utils.py" \
    --n-agents 3 \
    --overseer-model claude-opus-4-5

# Parallel hypothesis search (generates and selects the best approach):
python shepherd-experiments/meta_agent/exp_parallel_search.py \
    --goal "Design a retry strategy for an HTTP client" \
    --n-agents 4
```

### Parallel vs. sequential speedup benchmark

```bash
python shepherd-experiments/benchmarks/bench_parallel_agents.py --dry-run
```

`--dry-run` replaces the real agent with a `sleep()` stub so you can measure
pure scheduler + Vers-scope overhead without API calls.

---

## Extending

To swap the overseer model:

```python
from shepherd.vers_backend import VersShepherd

with VersShepherd("my-repo", overseer_model="ollama/llama3") as shepherd:
    results = shepherd.run("Refactor the auth module", n_agents=3, parallel=True)
    for r in results:
        print(r.scope_name, "merged" if r.merged else "discarded", r.evaluation[:120])
```

The `VersShepherd` / `VersAgentScope` API is the stable integration seam.
Substrate internals (`VcsCore.fork`, `.merge`, `.discard`) are vcs-core's
supported `runtime_api` surface.
