# Visual Artifact Notebook Examples

Small runnable Shepherd notebook examples over the current Shepherd workspace-control API.
The product rename is landing in a separate workstream;
these examples use the launch-facing Shepherd name while the checked-in Python
imports still use `shepherd` package names.

The artifact is a single self-contained HTML infographic tile explaining
gradient descent.

The shared teaching case is intentionally concrete: one branch draws the update
path downhill toward the minimum, and the planted failure draws it uphill. That
gives all three notebooks a deterministic, visual, mechanically gateable defect.

## What Runs

The notebooks run deterministic offline provider tasks through `ShepherdWorkspace`,
`Flow.fork(...)`, retained run outputs, artifact refs, trace projection, and
explicit settlement. No provider credentials are needed.

The Variant Studio notebook also includes an optional live Claude path for UC1.
It is disabled by default and requires a configured local Claude CLI, credentials,
and native jail support. The live lane redirects Claude home/config/scratch into
the per-run jail, so credentials must be visible to that redirected CLI
environment rather than only to an unrelated desktop session. It uses the same
`Flow.fork(...)`, retained-output, trace, and settlement path as the deterministic
static run.

The `shepherd_usecases.visual_artifact` package is local helper code for these
examples. The product surface being demonstrated is the workspace, flow, retained
output, artifact ref, trace, and settlement API underneath it.

Real in this package:

- Workspace and flow control through the current Shepherd API.
- VcsCore custody for retained outputs.
- Artifact refs for review, selection, and retry dataflow.
- Flow trace projection.
- Explicit select, discard, and release settlement.

Fixture-backed in this package:

- Static provider output generation.
- Model-tier cost and quality labels for the right-sizing notebook.

Not included in this public slice:

- Live UC2/UC3 provider paths.
- Codex notebooks.
- Provider plugin authoring.
- Sessions/replay.
- Retained-output writable branching.

## Module layout

| File | Role |
|---|---|
| `shepherd_usecases/visual_artifact/launch.py` | Local notebook harness over Shepherd workspace-control APIs. |
| `shepherd_usecases/visual_artifact/tasks.py` | Provider-owned static task declaration used by the example harness. |
| `shepherd_usecases/visual_artifact/tile.py` | Gradient-tile brief, fixtures, wrong-direction injection, mechanical gate, critic schema. |
| `shepherd_usecases/visual_artifact/recovery_core.py` | UC3 logical retry classification and plan helpers. |
| `shepherd_usecases/visual_artifact/viz.py` | Display-only helpers for tables, artifacts, comparisons, source, and traces. |
| `notebooks/_build_notebooks.py` | Source for the generated notebooks. |

## Run it

```bash
make test-dialect-v011-static
```

For the optional live Claude release lane, run the focused evidence target on a
jail-capable host:

```bash
make test-dialect-v011-claude-evidence
SHEPHERD_LIVE_CLAUDE=1 make test-dialect-v011-claude-evidence
```

For notebooks:

```bash
make notebooks
```

Open a notebook under `examples/notebooks/visual_artifact/notebooks/`.

## Use Cases

- UC1, Variant Studio: fork `contour-map` and `uphill-path`, record a critic run, select the clean tile, discard the wrong-direction tile.
- UC2, Model Right-Sizing: run the same evaluator under `high`/`mid`/`cheap`; `cheap` misses the wrong-direction divergence, so `mid` is selected.
- UC3, Pipeline Recovery: plan, draft a wrong-direction tile, inspect the failure, retry from the post-plan boundary, select the corrected retry.

## Current Gaps

- Live Claude UC1 is release evidence gated by `make test-dialect-v011-claude-evidence`
  with `SHEPHERD_LIVE_CLAUDE=1`; normal notebook checks remain deterministic.
