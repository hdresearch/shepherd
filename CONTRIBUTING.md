# Contributing to Shepherd

Thanks for your interest in contributing to Shepherd! This guide covers how to
set up the repository, run the tests, and open a pull request. By participating
in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** for dependency and workspace management

## Set up the workspace

Shepherd is a [uv](https://docs.astral.sh/uv/) workspace spanning several
packages (see the [repository layout](README.md#repository-layout)). Clone the
repo and sync every package together with its dev and test dependencies:

```bash
git clone https://github.com/shepherd-agents/shepherd.git
cd shepherd
uv sync --all-packages --all-groups
```

## Run the tests

Tests are organized per package, so the most reliable way to run a suite is to
target its package directory:

```bash
# unit tests for the framework spine
uv run --directory shepherd/packages/meta pytest tests/unit

# the execution/store runtime
uv run --directory vcs-core/packages/core pytest tests/unit
```

Some suites need optional extras (for example `--with openai` for provider
tests), and some integration tests need a container runtime such as Podman;
those skip cleanly when the runtime is unavailable.

The top-level `Makefile` bundles the common entry points as a convenience:

```bash
make test         # unit tests across the packages
make lint         # ruff lint
make format       # ruff format
make typecheck    # static type checks
```

## Open a pull request

- Keep PR titles in [Conventional Commits](https://www.conventionalcommits.org/)
  form (`feat:`, `fix:`, `docs:`, `refactor:`, …).
- Describe the motivation, and list what you ran to verify the change.
- Run `make lint` and the relevant tests before pushing.
- If you change project structure, import boundaries, or docs paths, rerun the
  corresponding repo-level checks.

## Package-specific notes

Some packages carry their own contributing notes with local command summaries
and validation loops:

- [`shepherd/CONTRIBUTING.md`](shepherd/CONTRIBUTING.md) — the framework packages
- [`vcs-core/CONTRIBUTING.md`](vcs-core/CONTRIBUTING.md) — the execution/store runtime

## Documentation

User-facing documentation lives at
[docs.shepherd-agents.ai](https://docs.shepherd-agents.ai/) and is authored in
this repo under [`docs/shepherd/`](docs/shepherd/).

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE) that covers this project.
