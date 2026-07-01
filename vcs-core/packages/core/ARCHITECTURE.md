# vcs-core Package Map

This is the short navigation guide for `vcs-core/packages/core`.

For the current public/internal boundary and experimental SPI v0
surface, see `vcs-core/design/reference/DESIGN-substrate-spi.md` and
`tests/contract/test_public_surface_baseline.py`.

## Start Here

- `src/vcs_core/cli.py`
  Main Click entrypoint. If you are adding or changing a top-level CLI
  command, start here and follow the `_cli_*` helper modules.
- `src/vcs_core/vcscore.py`
  Coordinator/runtime surface. Scope lifecycle, activation,
  materialization, recovery, and high-level query flows live here.
- `src/vcs_core/store.py`
  Bare Git storage model. Commit layout, refs, operation history,
  archive handling, and low-level scope persistence live here.
- `src/vcs_core/substrates.py`
  Built-in substrate implementations and the overlay-backed filesystem
  runtime.
- `src/vcs_core/discovery.py`
  Built-in and plugin discovery, manifest resolution, validation, and
  binding instantiation.
- `src/vcs_core/materialization.py`
  Planning and applying materialization work across bound substrates.

## Common Change Paths

- Add a new CLI command:
  start in `cli.py`, then move command-specific code into an appropriate
  `_cli_*` helper if the logic is more than a small wrapper.
- Change scope lifecycle or session behavior:
  start in `vcscore.py`; then check `_app.py`, `_session.py`,
  `_lifecycle_run.py`, and related integration tests.
- Change Git storage or history queries:
  start in `store.py`; then inspect `git_store.py`,
  `_operation_projection.py`, and store/integration tests.
- Add or change a substrate:
  start in `substrates.py` or the specific substrate module, then check
  `discovery.py`, `manifest.py`, `materialization.py`, and the SPI tests.
- Change materialization or push behavior:
  start in `materialization.py` and `vcscore.py`, then check recovery
  tests and upstream-aware tests.

## Test Map

- `tests/unit/`
  Fast module-level coverage and local contract checks.
- `tests/integration/cli/`
  Stateless and session-backed CLI behavior.
- `tests/integration/vcscore/`
  Coordinator/runtime behavior.
- `tests/integration/store/`
  Bare Git storage and history behavior.
- `tests/integration/substrates/`
  Built-in substrate and runtime integration.
- `tests/container/`
  Overlay and container-backed flows.
- `tests/contract/`
  Public-surface and experimental SPI inventory checks.
