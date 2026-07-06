# Shepherd Experiment Run

**Timestamp:** `2026-07-06T15-28-10`  
**Platform:** `Darwin 24.6.0 (arm64)`  
**Python:** `3.11.14`

## Run Status

| Experiment / Benchmark | Status | Wall time |
|---|---|---|
| `bench_scope_lifecycle` | ✅ OK | 2.6s |
| `bench_trace_overhead` | ✅ OK | 2.4s |
| `bench_parallel_agents` | ✅ OK | 4.9s |
| `exp_coding_task` | ✅ OK | 20.9s |
| `exp_parallel_search` | ✅ OK | 54.8s |
| `exp_multi_step_refactor` | ✅ OK | 22.0s |

## Benchmark Results

### Scope lifecycle latency

| Operation | mean ms | p50 ms | p90 ms | p99 ms |
|---|---|---|---|---|
| cold startup | 13.6 | 4.6 | 4.9 | 231.5 |
| fork | 5.0 | 5.1 | 6.0 | 6.9 |
| discard | 12.7 | 12.8 | 13.9 | 14.6 |
| roundtrip (fork+write+merge) | 48.6 | 47.6 | 56.2 | 56.9 |

### Trace overhead vs. plain git

Config: 1 file(s) × 1.0 KB, 25 reps

| Condition | mean ms | p50 ms | p90 ms |
|---|---|---|---|
| plain git (add+commit) | 21.7 | 21.7 | 22.2 |
| VcsCore roundtrip | 33.6 | 33.7 | 34.2 |

**Trace overhead:** +11.893 ms (54.7% above plain git)

### Parallel vs. sequential speedup

n_agents=3  stub_duration=1.0s each

| Mode | Wall time | Speedup | Efficiency |
|---|---|---|---|
| sequential | 3.302s | 1.00× | 100% |
| parallel   | 1.118s | 2.95× | 98.4% |

Scope setup overhead (mean): 21.67 ms
Overhead above critical path: 67.4 ms

## Meta-Agent Experiment Results

### exp_coding_task

**Task:** Add a fibonacci function with memoization to utils.py
**Model:** `claude-opus-4-5`  **n_agents:** 3  **wall time:** 20.54s

| Agent | Scope | File | Decision | Lines | Time |
|---|---|---|---|---|---|
| 0 | `agent-0-d4ec52` | `utils.py` | ✅ merged | 24 | 7.691s |
| 1 | `agent-1-48964e` | `utils.py` | ✅ merged | 28 | 6.663s |
| 2 | `agent-2-781b0b` | `utils.py` | ✅ merged | 24 | 6.186s |

### exp_parallel_search

**Goal:** Implement a retry strategy for an HTTP client
**Model:** `claude-opus-4-5`  **n_agents:** 3  **selected:** #0  **wall time:** 44.52s

| # | Hypothesis (truncated) | File | Decision | Lines | Time |
|---|---|---|---|---|---|
| 0 | **Exponential Backoff with Jitter (stdlib sync)**: Implement a synchro… | `retry_strategy.py` | ★ merged | 125 | 13.627s |
| 1 | **Token Bucket Rate Limiter with Fixed Retries (third-party async)**: … | `retry_strategy.py` | discarded | 145 | 14.632s |
| 2 | **Circuit Breaker Pattern with Adaptive Retry (hybrid approach)**: Imp… | `circuit_breaker_retry.py` | discarded | 130 | 13.4s |

### exp_multi_step_refactor

**Model:** `claude-opus-4-5`  **steps:** 3  **completed:** 2  **halted:** True  **wall time:** 21.58s

| Step | Instruction (truncated) | File | Decision | Lines | Time |
|---|---|---|---|---|---|
| 0 | Create utils.py with a fibonacci function (iterative, no memoizat… | `utils.py` | ✅ merged | 40 | 7.642s |
| 1 | Refactor utils.py: add type hints to both functions.… | `utils.py` | ✅ merged | 40 | 4.948s |
| 2 | Refactor utils.py: add a docstring to each function explaining it… | `utils.py` | ❌ discarded | 60 | 8.841s |

## Files in This Run

- `bench_parallel_agents.log` (502 bytes)
- `bench_parallel_agents_2026-07-06T15-28-20.json` (581 bytes)
- `bench_scope_lifecycle.log` (632 bytes)
- `bench_scope_lifecycle_2026-07-06T15-28-13.json` (979 bytes)
- `bench_trace_overhead.log` (257 bytes)
- `bench_trace_overhead_2026-07-06T15-28-15.json` (747 bytes)
- `exp_coding_task.log` (5,455 bytes)
- `exp_coding_task_2026-07-06T15-28-41.json` (3,912 bytes)
- `exp_multi_step_refactor.log` (6,055 bytes)
- `exp_multi_step_refactor_2026-07-06T15-29-58.json` (5,673 bytes)
- `exp_parallel_search.log` (4,925 bytes)
- `exp_parallel_search_2026-07-06T15-29-36.json` (16,972 bytes)
