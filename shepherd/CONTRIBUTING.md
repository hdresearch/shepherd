# Contributing to Shepherd

## Pick a task

- Start at [design/README.md](design/README.md) for active roadmap bundles and implemented history.
- Use the readiness table in [README.md](README.md) to decide whether a package is `stable`, `active`, or `please-ask`.

## Run it locally

- `uv sync --all-packages --all-groups`
- `uv run pytest shepherd/packages/ shepherd/extras/`
- `uv run pytest shepherd/integration-tests/`
- `uv run ruff check shepherd/packages shepherd/extras shepherd/integration-tests scripts`

## What review looks like

- [.github/CODEOWNERS](../.github/CODEOWNERS) routes review by path.
- Keep PR titles in Conventional Commits form.
- Include motivation plus a concrete tested-by list in the PR description.
- If you touch project structure, docs paths, or import boundaries, rerun the corresponding repo-level checks.

## Which packages are open to PRs

- `stable` means routine PRs are fine.
- `active` means coordinate on large or architectural changes.
- `please-ask` means open an issue or start a design discussion first.
