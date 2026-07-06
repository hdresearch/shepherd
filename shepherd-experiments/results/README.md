# Experiment Results

Each subdirectory here is one full run of `run_all.py`, named by timestamp:

```
results/
└── 2026-07-06T15-30-00/
    ├── summary.md                    ← human-readable overview
    ├── exp_coding_task.json          ← meta-agent: N agents, one task
    ├── exp_parallel_search.json      ← meta-agent: hypothesis search
    ├── exp_multi_step_refactor.json  ← meta-agent: sequential pipeline
    ├── bench_scope_lifecycle.json    ← fork / merge / discard latency
    ├── bench_trace_overhead.json     ← Vers trace overhead vs plain git
    ├── bench_parallel_agents.json    ← parallel vs sequential speedup
    ├── exp_coding_task.log           ← full stdout/stderr for each run
    ├── exp_parallel_search.log
    └── ...
```

Individual experiment scripts also accept `--results-dir` so they can be run
standalone and still persist output:

```bash
python meta_agent/exp_coding_task.py --results-dir results/adhoc
python benchmarks/bench_scope_lifecycle.py --results-dir results/adhoc
```

To run everything at once and capture a full dated snapshot:

```bash
python run_all.py
```
