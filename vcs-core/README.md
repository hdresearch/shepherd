# vcs-core

Provenance-native version control for executable worlds.

## Start Here

- [packages/core/README.md](packages/core/README.md) — current CLI/runtime/store surface
- [design/README.md](design/README.md) — roadmap bundles and design references
- [packages/core/tests/](packages/core/tests/) — unit, integration, and container-backed coverage

## Workspace Boundary

vcs-core manages workspace state under the initialized workspace root.
Host environment state outside that workspace is pass-through,
untracked, and not reversible by vcs-core. See
[packages/core/README.md](packages/core/README.md#workspace-boundary)
for the durable release contract.

## Project Layout

- `packages/core/` — the current `vcs-core` package, CLI, runtime, and substrate framework
- `design/` — project-specific architecture, reference notes, roadmap bundles, and history
- `extras/` — reserved for future vcs-core-specific extensions

## Readiness

| Package | Location | Status | Notes |
|---|---|---|---|
| `core` | `packages` | alpha | SPI v0 remains experimental; coordinate before extending major surfaces |

## Working Agreement

Use [CONTRIBUTING.md](CONTRIBUTING.md) for the authoritative support
matrix, collaborator validation loop, and prelaunch handoff/release
bar. Formal review routing still lives in the parent workspace today, so
standalone handoff consumers should coordinate reviewers directly.

For the package-local command loop layered on top of the workspace sync,
see [packages/core/README.md](packages/core/README.md) for `make smoke`,
the current `make test_unit` behavior, and the installed-mode command
summary.

## Installed CLI Handoff Gate

For prelaunch handoff/release candidates, and for packaging,
installed-mode, or public-boundary changes, run:

```bash
cd packages/core
make test_installed
```

That smoke builds a wheel, installs it into an isolated virtual
environment, runs the installed `vcs-core` subprocess flow, then reads
back through `checkout ground --dest ...` and `status`.

It intentionally builds against the already-synced local test
environment rather than treating build-backend resolution as part of the
smoke itself. For the full collaborator checklist and support matrix,
see [CONTRIBUTING.md](CONTRIBUTING.md).
