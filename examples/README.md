# Examples

This directory contains tested examples for the current public Shepherd surfaces.

Run examples from the repository root with `uv run python...`. The examples are
small executable programs, not framework APIs; integration tests keep them from
drifting as the pre-launch surface changes.

## Five-Minute Quickstart

- [`quickstart/offline_task.py`](quickstart/offline_task.py)
- [`quickstart/world_channel.py`](quickstart/world_channel.py)
- [`quickstart/claude_readme.py`](quickstart/claude_readme.py)

The quickstart examples use the public `import shepherd as sp` facade and the
`sp` CLI. `offline_task.py` is pure Python. `world_channel.py` runs from an
initialized workspace created with `sp init`. `claude_readme.py` is optional and
skips unless `sp doctor claude` is green.

## Workspace Handles

- [`workspace-handles/best_of_n.py`](workspace-handles/best_of_n.py)
- [`workspace-handles/retry_until_acceptable.py`](workspace-handles/retry_until_acceptable.py)

These demonstrate the current workspace-only floor: selected `GitRepo`
values, `WorkspaceTask.run(...)`, retained `RunOutput` inspection, authority
read-model inspection, and explicit `select` / `release` / `discard`
settlement.

## Visual Artifact Notebooks

- [`notebooks/visual_artifact/`](notebooks/visual_artifact/)

These notebooks demonstrate Shepherd workspace control, VcsCore retained-output
custody, artifact refs, flow traces, and explicit settlement through a fully
offline visual-artifact workflow.
