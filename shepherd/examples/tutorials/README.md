# Shepherd Tutorials

> Status: migration reference. Only `syntax_nucleus.py` is current first-run
> callable-spine material; the numbered tutorials remain migration inventory
> unless their row says `current`.

Progressive executable examples from the syntax nucleus to advanced workflows.

## Recommended Path

Most users should start with the syntax nucleus tutorial:

```bash
uv run python shepherd/examples/tutorials/syntax_nucleus.py
```

The older numbered tutorials cover legacy class-form and advanced runtime
surfaces that are still useful internally while the new syntax lands. Many are
not expected to run unchanged against the current top-level facade; treat them
as migration inventory unless the status column says `current`.

| Tutorial | File | Status | What You Learn |
|----------|------|--------|----------------|
| nucleus | `syntax_nucleus.py` | current | Function-form `@task`, `workspace`, `deliver`, `Run` |
| 01 | `01_simple.py` | legacy-class-form | Define and run tasks, access outputs and effects |
| 02 | `02_contexts.py` | legacy-class-form | Contexts, nested scopes, binding styles, session state |
| 03 | `03_steps.py` | advanced-runtime | Multi-step tasks with `@step` |
| 04 | `04_workspaces.py` | legacy-class-form | Workspace contexts and file effects |
| 05 | `05_artifacts.py` | advanced-runtime | Artifacts and file outputs |
| 06 | `06_debugging.py` | advanced-runtime | Debugging and effect inspection |
| 07 | `07_devices.py` | advanced-runtime | Devices, fluent `Pipeline`, retry, gate, recover |

## Advanced And Specialized Tutorials

| Tutorial | File | Status | What You Learn |
|----------|------|--------|----------------|
| 07a | `07a_device_stacking.py` | advanced-runtime | Stacked device and workspace patterns |
| 08 | `08_advanced.py` | advanced-runtime | Functional style and custom combinators |
| 09 | `09_meta_tasks.py` | deferred-bridge | Tasks that operate on other tasks |
| 10 | `10_completed_task.py` | deferred-bridge | Completed task introspection |
| 11 | `11_export_import.py` | advanced-runtime | Trajectory export and import |
| 12 | `12_async_steps.py` | advanced-runtime | Async steps with asyncio |
| 13 | `13_checks.py` | advanced-runtime | Declarative checks on fields |
| 13 remote | `13_remote_sandboxes.py` | advanced-runtime | Remote sandbox support |
| 14 | `14_pipeline_tasks.py` | advanced-runtime | Staged `@task` workflows with `run_stage` |
| 15 | `15_profiling.py` | advanced-runtime | Profiling and execution timing |
| 16 | `16_autoconfig.py` | deferred-bridge | Autoconfiguration |

## Topic Map

- First run: nucleus.
- Contexts and stateful resources: 02, 04, 05.
- Inspection and operations: 06, 10, 11, 15.
- Environment isolation: 07, 07a, 13 remote.
- Composition: 07, 08, 14.
- Configuration ergonomics: 16.

## Running Tutorials

Run the nucleus tutorial from the repository root:

```bash
uv run python shepherd/examples/tutorials/syntax_nucleus.py
```

Most provider-backed tutorials require `ANTHROPIC_API_KEY`. Numbered tutorials
whose status is not `current` may also need import/API migration before they
run. Some later tutorials provide mock fallback paths when noted in the script.

## Utility Module

Tutorials 04 and later use helpers from `shepherd/examples/utils.py` for
workspace setup and cleanup. This module is only for example convenience.

## See Also

- [Examples migration audit](../../../docs/design/proposed/260505-plans/00-syntax-nucleus/EXAMPLES_AUDIT.md)
- [Guide Index](../../docs/guides/README.md)
- [Contexts and Workspaces Guide](../../docs/guides/contexts-and-workspaces.md)
- [Devices Guide](../../docs/guides/devices.md)
- [Debugging Guide](../../docs/guides/debugging.md)
- [Pipelines and Combinators Guide](../../docs/guides/pipelines-and-combinators.md)
- [Functional Style Guide](../../docs/guides/functional-style.md)
- [Scenarios Guide](../../docs/guides/scenarios.md)
