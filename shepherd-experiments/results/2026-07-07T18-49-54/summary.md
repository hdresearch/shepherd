# Shepherd Experiment Results

**Timestamp:** 2026-07-07T18-49-54  
**Model:** claude-opus-4-5  
**Passed:** 6/6  
**Total time:** 983.9s

## Results

| Experiment | Status | Time |
|---|---|---|
| exp_coding_task | ✅ pass | 144.0s |
| exp_parallel_search | ✅ pass | 303.0s |
| exp_multi_step_refactor | ✅ pass | 208.8s |
| bench_scope_lifecycle | ✅ pass | 123.1s |
| bench_trace_overhead | ✅ pass | 60.6s |
| bench_parallel_agents | ✅ pass | 144.3s |

## Architecture

Sub-agents run inside **Vers VM jails** via:
```
workspace.run(task_id, repo=ws.git_repo(),
    placement='auto',           # → 'jail' on Linux+Landlock
    runtime={'provider': 'claude'})  # Claude CLI in the jail
```

Execution path: `workspace.run()` → `ShepherdRunDriver.prepare_bound()`
→ `VcsCore.execute_recorded('runtime', 'run', ...)` → fork scope →
Claude writes to `execution.working_path` → merge (success) / discard (failure).

The overseer runs locally via the Anthropic SDK and reads retained
outputs through `run.changeset().read_file(path)` before settling.
