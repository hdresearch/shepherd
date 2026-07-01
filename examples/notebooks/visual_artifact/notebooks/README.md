# Running the Visual Artifact Notebooks

Three standalone, runnable guides to Shepherd's core patterns. Each stands on its own, so
start with any of them.

These notebooks are Shepherd-facing examples over the current Shepherd Python API. Until the
separate product rename lands, code cells still import `shepherd`/`shepherd_dialect`-backed
helpers.

| Notebook | What it shows |
|---|---|
| `visual_variant_studio.ipynb` | Run several attempts at one task, let an agent critic judge each, keep the strongest, and keep a record of the rest. |
| `model_right_sizing_lab.ipynb` | Run one step across model scales and keep the cheapest that still clears a quality bar. |
| `visual_pipeline_recovery.ipynb` | Recover a multi-step run when one step drifts: keep the good prefix, diagnose read-only, retry from the right boundary. |

## Launch

You need [uv](https://docs.astral.sh/uv/). From the **repository root**:

```bash
make notebooks        # equivalently: uv run --group notebook jupyter lab
```

Open a notebook from `examples/notebooks/visual_artifact/notebooks/` and run the cells top
to bottom. The setup cells locate this example bundle, validate the kernel, create a
temporary `ShepherdWorkspace`, open a `Flow`, and run deterministic static provider tasks
through normal retained-output custody. No API key is needed.

## Requirements: a copy-on-write overlay backend (Linux)

Each run forks an **isolated, reversible workspace scope**, which needs a copy-on-write
overlay backend under it:

- **macOS** — the APFS clonefile carrier is used automatically. Nothing to install.
- **Linux** — a kernel overlay (root + `CAP_SYS_ADMIN`) or a FUSE overlay. On an
  unprivileged host or container, install **fuse-overlayfs**:

  ```bash
  sudo apt-get install -y fuse-overlayfs   # Debian/Ubuntu
  ```

`make notebooks` runs a preflight that checks for a usable backend and installs
fuse-overlayfs for you on Debian/Ubuntu Linux. If none is available, the Setup cell fails
fast with this instruction rather than a low-level error mid-run.

`visual_variant_studio.ipynb` includes an optional live Claude section. It is disabled by
default. Turn it on only when the local Claude CLI, credentials, and native jail support
are available. The jailed runtime redirects Claude home/config/scratch into the run, so
credentials must be visible to that redirected CLI environment; it uses the same
workspace-control retained-output path as the static run. The release evidence target is:

```bash
SHEPHERD_LIVE_CLAUDE=1 make test-dialect-v011-claude-evidence
```

## What is real?

The notebooks use the real workspace-control surface, real VcsCore retained-output custody,
real artifact refs, real flow trace projection, and explicit output settlement.

The generated tile contents and model-tier outcomes are deterministic fixtures. The optional
live Claude UC1 path is release evidence; UC2 and UC3 stay deterministic in this slice.

## Which Python / kernel?

The notebooks run on **Python 3.11+** and are validated against the repo's own uv environment.
Launching with `make notebooks` uses that environment and sets the kernel for you, so the Setup
cell is a no-op.

If you launch JupyterLab from another environment instead, such as another project's virtualenv
or a Python version that some dependencies do not support yet, the Setup cell stops with an
actionable error. Fix it by launching as above, or register the repo environment as a named
kernel and select it for the notebook:

```bash
uv sync --group notebook
uv run python -m ipykernel install --user --name agent-workflows --display-name "agent-workflows"
```

## Editing the notebooks

The guides are generated from `_build_notebooks.py`, so the three stay in lockstep with the
shared template (`_usecase_template.ipynb`). Cells use `launch` for the workspace-control
helper and `viz` for display-only rendering. Edit the prose and code there,
then rebuild from this directory:

```bash
python _build_notebooks.py        # all notebooks; or pass uc1 / uc1-internals / uc2 / uc2-internals / uc3 / uc3-internals / template
```
