# Shepherd

Effect-based framework for building AI agents with multi-provider support.

> **[re-aim notice 2026-06-09]** The architecture described in this tree predates the **v1.0 re-aim**
> (2026-06-02/03): execution, containment, carriers, and the trace substrate are now committed to
> **vcs-core as the substrate layer**, with Shepherd as the **dialect** that composes vcs-core's
> execution-mechanism verbs. See
> [`docs/engineering/convergence/architectural-commitment.md`](../docs/engineering/convergence/architectural-commitment.md),
> [`runtime-as-kernel.md`](../docs/engineering/convergence/runtime-as-kernel.md), and
> [`execution-boundary.md`](../docs/engineering/convergence/execution-boundary.md) (the dialect-composes boundary). This tree remains accurate
> for the current production packages — read it as the pre-re-aim baseline (the rewrite-target endpoint is
> a fenced Horizon item, not this codebase's present state).

## Start Here

- [packages/meta/README.md](packages/meta/README.md) — public facade and sync-first entry points
- [docs/README.md](docs/README.md) — user and architecture docs
- [examples/README.md](examples/README.md) — tutorials and scenarios
- [design/README.md](design/README.md) — roadmap bundles, implemented plans, and design history
- [integration-tests/](integration-tests/) — cross-package contract and import-boundary coverage

## Project Layout

- `packages/` — core surfaces and shared runtime/framework packages
- `extras/` — batteries and domain-specific packages
- `docs/`, `examples/`, `eval/` — user-facing material and evaluation assets
- `design/` — architecture, proposals, research tracks, and historical implementation notes
- `integration-tests/` — project-level cross-package checks

## Readiness

| Package | Location | Status | Notes |
|---|---|---|---|
| `core` | `packages` | stable | kernel primitives and foundational types |
| `runtime` | `packages` | active | owner-path extraction and runtime layering are still evolving |
| `providers` | `packages` | stable | add provider integrations carefully, but routine work is straightforward |
| `contexts` | `packages` | stable | broadly open for context implementations and maintenance |
| `transform` | `packages` | active | transformation and grounding APIs are still settling |
| `sandboxes` | `packages` | active | execution backends are still changing |
| `export` | `packages` | stable | trajectory/export surface is comparatively settled |
| `authoring` | `packages` | please-ask | workflow and roadmap ownership is still concentrated |
| `tests` | `packages` | stable | shared testing helpers and mocks |
| `meta` | `packages` | stable | convenience facade; keep changes coherent with downstream packages |
| `kernel-v3-reference` | `packages` | experimental/reference | proof-adjacent reference interpreter for the v3 kernel draft; not production API |
| `banking` | `extras` | stable | domain battery |
| `coding` | `extras` | stable | domain battery |

## Working Agreement

Use [CONTRIBUTING.md](CONTRIBUTING.md) for the expected local commands, review bar, and package-selection guidance. Review ownership is routed by [.github/CODEOWNERS](../.github/CODEOWNERS).
