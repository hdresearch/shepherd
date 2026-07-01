# Shepherd Examples

This directory contains the current syntax nucleus tutorial plus legacy and
advanced examples retained as migration inventory.

The current first-run path is the syntax nucleus tutorial:

```bash
uv run python shepherd/examples/tutorials/syntax_nucleus.py
```

The older numbered tutorials and scenarios are migration inventory while the
function-form surface lands. Their paths are stable, but many are not expected
to run unchanged against the current top-level facade; read them as
legacy/advanced references unless their local README marks them current.

## Tutorials

Start with the syntax nucleus. The numbered tutorials remain legacy/advanced
material pending rewrite and may need owner-path import updates before they run.

| File | Description |
|------|-------------|
| `tutorials/syntax_nucleus.py` | Current function-form `@task`, `workspace`, `deliver`, and `Run` |
| `tutorials/01_simple.py` | Basic task definition, execution, outputs, and effects |
| `tutorials/02_contexts.py` | Contexts, scopes, binding styles, and session state |
| `tutorials/03_steps.py` | Multi-step tasks with `@step` |
| `tutorials/04_workspaces.py` | Git-backed workspaces and isolation patterns |
| `tutorials/05_artifacts.py` | File artifacts from task execution |
| `tutorials/06_debugging.py` | Debugging tools and troubleshooting |
| `tutorials/07_devices.py` | Devices, fluent `Pipeline`, and error handling |

See [tutorials/README.md](tutorials/README.md) for the full tutorial inventory.

Run the current offline tutorial from the repository root:

```bash
uv run python shepherd/examples/tutorials/syntax_nucleus.py
```

## Scenarios

Scenarios are complete, realistic workflows. Today they are legacy class-form
or advanced provider-backed demonstrations, not first-run examples or
deterministic tests. Some may need migration before they run on the current
facade. Check the final `Outcome` section to see what a run actually
demonstrated.

| File | Status | Description |
|------|--------|-------------|
| `scenarios/simple_tasks.py` | legacy-class-form | Basic task patterns without dependencies |
| `scenarios/fix_bug.py` | legacy-class-form | Writable bug-fix demonstration |
| `scenarios/review_code.py` | legacy-class-form | Read-only code review demonstration |
| `scenarios/readonly_analysis.py` | legacy-class-form | Read-only codebase analysis |
| `scenarios/combined_chaining.py` | legacy-class-form | Advanced continuity across chained tasks |

Do not treat these scripts as current command recipes. Use
[scenarios/README.md](scenarios/README.md) and the
[examples migration audit](../../docs/design/proposed/260505-plans/00-syntax-nucleus/EXAMPLES_AUDIT.md)
before running or porting them.

## Requirements

Legacy examples often require:

- `shepherd[all]` - the meta-package with optional dependencies.
- `ANTHROPIC_API_KEY` - unless the example supports mock mode.

For repository development:

```bash
uv sync --all-packages
```

## Utilities

`utils.py` contains helper functions for examples, such as temporary git
workspace setup and formatted output. It is not part of the public API.
