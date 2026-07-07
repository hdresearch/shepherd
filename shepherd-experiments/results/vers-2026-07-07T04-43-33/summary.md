# Shepherd Experiment Results — Vers VM Infrastructure

**Timestamp:** `2026-07-07T04-43-33`  
**Infrastructure:** Vers VMs (Ubuntu 24.04, Linux 6.12, x86_64)  
**Golden image:** `shepherd-agent:latest` (`54e730b4-1a21-4495-b638-c9183d8bde26`)  
**Model:** `claude-opus-4-5`  
**Passed:** 5/5  
**Total time:** 836.5s

## Benchmark Results

### VM Lifecycle Latency

| Operation | mean (ms) | median (ms) |
|---|---|---|
| **Branch** (fork from golden image → running) | 10216 | 10122 |
| **Exec** (SSH, simple command) | 2893 | 2862 |
| **Delete** | 6614 | 6651 |
| **Total roundtrip** (branch + exec + delete) | 19723 | 19456 |

### Full Agent Roundtrip

Branch VM → push workspace → Claude exec → collect output → delete VM

| Metric | Value |
|---|---|
| Mean roundtrip | 32.6s |
| Median roundtrip | 32.4s |
| Breakdown: branch | ~10s |
| Breakdown: workspace push | ~1.9s |
| Breakdown: Claude exec (trivial task) | ~10.4s |
| Breakdown: file collection | ~1.5s |
| Breakdown: VM delete | ~6.5s |

## Meta-Agent Experiment Results

### Experiment 1 — N Agents, Same Task

**Task:** Add a fibonacci function with memoization to solution.py  
**n_agents:** 3 | **wall time:** 132.4s | **winner:** ✅ agent 0 (score 10.0)

| Agent | VM ID | Score | Time | Decision |
|---|---|---|---|---|
| 0 | `00775682-3eb` | 10.0 | 34.2s | ★ selected |
| 1 | `e97bb5e1-626` | 10.0 | 37.4s | discarded |
| 2 | `52a61a4b-276` | 10.0 | 33.2s | discarded |

All three agents produced perfect solutions running inside isolated Vers VMs.

### Experiment 2 — Parallel Hypothesis Search

**Problem:** Implement a thread-safe LRU cache in Python  
**n_hypotheses:** 3 | **wall time:** 294.7s | **winner:** ✅ hypothesis 0 (score 9.0)

| # | Hypothesis | VM ID | Score | Time | Decision |
|---|---|---|---|---|---|
| 0 | Threading Lock + OrderedDict | `8b857a44-41f` | 9.0 | 64.0s | ★ selected |
| 1 | Read-Write Lock + DLL + HashMap | `d38742af-c96` | 9.0 | 104.0s | discarded |
| 2 | Thread-Local + Eventual Consistency | `667f7664-cf0` | 8.0 | 85.3s | discarded |

All three agents produced complete, high-quality implementations. The overseer
selected the simplest approach (OrderedDict + Lock) for its clarity and correctness.

### Experiment 3 — Multi-Step Refactor Pipeline

**Function:** `parse_csv` — Parse a CSV string into a list of dicts  
**Pipeline:** 3 steps | **completed:** 2/3 | **wall time:** 252.2s

| Step | Task | VM ID | Score | Time | Decision |
|---|---|---|---|---|---|
| 1 | write_tests | `46e0f5c7-358` | 9.0 | 140.3s | ✅ selected |
| 2 | implement_function | `de44e4d8-a90` | 9.0 | 49.1s | ✅ selected |
| 3 | add_docstrings | `42101ad4-947` | — | 34.5s | ❌ no output |

Steps 1 and 2 completed successfully with high scores. Step 3 (add docstrings)
ran but the modified file wasn't detected as changed in the workspace diff.

## Architecture

Each sub-agent runs inside a **dedicated Vers VM** (Ubuntu 24.04, Linux 6.12, x86_64):

```
local overseer (macOS)
  ├─ Anthropic SDK for overseer LLM calls
  └─ For each sub-agent task:
       1. vers branch shepherd-agent:latest  → fork VM from golden image (~10s)
       2. vers exec --ssh <vm>               → push workspace via git bundle
       3. vers exec --ssh <vm>               → Claude CLI writes files inside VM
       4. vers exec <vm>                     → collect output via base64
       5. vers delete <vm>                   → cleanup ephemeral VM
```

**Key properties:**
- Each agent runs in a **fully isolated Linux VM** (not a container, not a sandbox)
- VMs are **ephemeral** — branched from a golden image, used once, deleted
- Agent code has **no access** to the host, other agents, or the network (beyond Anthropic API)
- The overseer **never runs inside the VM** — it stays on the local host
- Workspace state is transferred via **git bundles** (complete, versioned)
- Output is collected via **base64-encoded file reads** through `vers exec`

## Comparison: Local Seatbelt vs Vers VMs

| Metric | Local (Seatbelt) | Vers VM |
|---|---|---|
| Isolation | macOS Seatbelt sandbox | Linux VM (full hypervisor) |
| Overhead per run | ~1.7s (substrate) | ~32s (VM lifecycle + SSH) |
| Exp 1 winner score | 10.0 | 10.0 |
| Exp 2 winner score | 6.0 (truncated) | 9.0 (complete) |
| Exp 3 pipeline | 3/3 steps | 2/3 steps |
| Architecture | Linux x86_64 | Linux x86_64 |
| Agent execution | Local process | Remote VM process |

The Vers VM path has higher per-run overhead (~32s vs ~1.7s) but provides
**stronger isolation** (full VM boundary vs process sandbox) and **real Linux
infrastructure** (Landlock-capable, no macOS compatibility shims).
