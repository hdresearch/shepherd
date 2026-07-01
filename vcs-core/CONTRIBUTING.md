# Contributing to vcs-core

This file owns the prelaunch collaborator loop: support matrix,
quick-check vs release-check expectations, and when installed-mode
verification becomes mandatory.

## Start Here

- Start at [design/README.md](design/README.md) for the active roadmap and subsystem-specific design notes.
- Treat [README.md](README.md) as the project entry point and status page.
- Treat [packages/core/README.md](packages/core/README.md) as the package-local command summary.

## Sync Once

- From `packages/core`, run `uv sync --all-groups` once to sync the
  workspace dependencies used by the package-local loop.
- Commands below are written as `make -C packages/core <target>` so they
  can be run from the `vcs-core` repo root.

## Support Matrix

| Environment | Status | Primary adoption path? | Expected local validation | Key caveat |
|---|---|---|---|---|
| macOS store-first | `supported` | yes | `smoke`, `guide_check`, `test_unit` | This is the normal prelaunch path; session/overlay work is not the default onboarding story here. |
| Linux store-first | `supported` | yes | `smoke`, `guide_check`, `test_unit` | Same endorsed path as macOS for normal store/coordinator work. |
| macOS + Podman overlay/container path | `supported` | no | `test_container` plus the same non-container targets that cover the touched code | Requires a working Podman setup; use it when touching overlay or container-backed flows. |
| Linux overlay path | `supported` | no | `test_unit`, `test_container` | Native overlay coverage assumes Linux with the required overlayfs/FUSE prerequisites. |
| Linux direct-capture path | `experimental` | no | `test_unit`, `test_container`, and any targeted Linux-only capture tests you touch | Linux-only and currently needs a working `cc` on `PATH` the first time the shim is compiled into `.vcscore/`. |
| Other host/platform combinations | `sharp edge` | no | No standalone prelaunch support promise yet | Do not widen the support claim without adding the corresponding validation path. |

## Static Checks

- `make -C packages/core lint`
- `make -C packages/core typecheck`
- Both are now truthful package-local collaborator checks and should
  stay green for normal code changes.
- They are not part of the mandatory prelaunch handoff gate yet; keep
  that as a separate policy decision from the type-baseline rollout.

## Quick Checks

- Docs, entry-point wording, or checklist edits:
  `make -C packages/core smoke`
  and
  `uv run --directory packages/core pytest tests/unit/test_docs_contract.py -q`
- Store-first guide or package-root helper changes:
  `make -C packages/core guide_check`
- Typical Python/package changes:
  `make -C packages/core smoke`
  and
  `make -C packages/core test_unit`
- Packaging, installed-mode, or public-boundary changes:
  add `make -C packages/core test_installed`
- Overlay, session, container, or direct-capture changes:
  add `make -C packages/core test_container`

## Release / Handoff Bar

- Prelaunch handoff/release candidates must pass:
  `make -C packages/core smoke`,
  `make -C packages/core guide_check`,
  `make -C packages/core test_unit`,
  and
  `make -C packages/core test_installed`
- Add `make -C packages/core test_container` when touching
  overlay, session, container, or direct-capture flows.
- The installed-mode smoke builds against the already-synced local test
  environment. It is a packaging/runtime gate, not a fresh-machine
  package-index-resolution check.
- `make lint` and `make typecheck` are now normal collaborator gates,
  but they remain outside the mandatory handoff gate for now.

## Change-Type Checklist

| Change type | Expected local validation loop | Notes |
|---|---|---|
| Docs, README, CONTRIBUTING, or design-index wording only | `make -C packages/core smoke`; `uv run --directory packages/core pytest tests/unit/test_docs_contract.py -q` | Keep the root README, package README, and this file aligned. |
| Store-first guide or onboarding-path wording | `make -C packages/core guide_check` | The guide-check target protects the endorsed onboarding path and the package-root helper surface it teaches. |
| Public package-root exports or the `vcs_core.spi` surface (incl. `vcs_core.spi.testing`) | `make -C packages/core lint`; `make -C packages/core typecheck`; `make -C packages/core smoke`; `make -C packages/core test_unit`; `make -C packages/core test_installed`; `uv run --directory packages/core pytest tests/contract/test_public_surface_baseline.py tests/contract/test_spi_conformance.py -q` | Treat public-boundary work as handoff-sensitive even before external users exist. `vcs_core.spi` is the stable SPI home; prelaunch hard cuts should remove retired aliases instead of preserving compatibility debt. |
| Installed-mode or package-boundary behavior | `make -C packages/core lint`; `make -C packages/core typecheck`; `make -C packages/core smoke`; `make -C packages/core test_unit`; `make -C packages/core test_installed`; `uv run --directory packages/core pytest tests/unit/test_packaging_contract.py tests/integration/cli/test_installed_cli.py -q` | `test_installed` verifies installed runtime behavior, not build-backend resolution from a clean machine. |
| Store/coordinator query or semantic-surface changes | `make -C packages/core lint`; `make -C packages/core typecheck`; `make -C packages/core smoke`; `make -C packages/core test_unit` | Add `test_installed` for handoff/release candidates or when the public package boundary changes. |
| Coordinator service-boundary refactors | `make -C packages/core lint`; `make -C packages/core typecheck`; focused seam tests; `make -C packages/core test_unit` | Add `test_container` when the seam touches overlay, session, materialization execution, or lifecycle recovery confidence. |
| Overlay, session, runtime, or direct-capture changes | `make -C packages/core lint`; `make -C packages/core typecheck`; `make -C packages/core smoke`; `make -C packages/core test_unit`; `make -C packages/core test_container` | Call out Linux/container assumptions explicitly in review. |
| Substrate, plugin, or discovery changes | `make -C packages/core lint`; `make -C packages/core typecheck`; `make -C packages/core smoke`; `make -C packages/core test_unit` | Add `test_installed` if the change affects package-root exports or installed-mode behavior. |

## Review Notes

- For package-local navigation, start with
  [packages/core/ARCHITECTURE.md](packages/core/ARCHITECTURE.md).
- Formal review routing still lives in the parent workspace today, so
  standalone handoff consumers should coordinate reviewers directly.
- Keep PR titles in Conventional Commits form.
- Include motivation plus a tested-by list in the PR description.
- For substrate/runtime changes, call out Linux/container assumptions explicitly.

## Which Packages Are Open To PRs

- `alpha` means coordinate before extending major surfaces.
- Small bug fixes and docs/test improvements are usually fine.
- Open a design discussion first for new substrate, runtime, or storage abstractions.
