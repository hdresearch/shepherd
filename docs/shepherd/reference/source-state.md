# Source-state inventory

> Page status: release-ready
> Source state: shipped-source
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: scripts/check_shepherd_docs.py
> Stale-names: migration-context

*This is the source-state inventory — the hand-maintained ledger of what these
docs may claim today, and where each fact comes from.*

This prototype exercises the full documentation pipeline. Some sources are the
**real repository** (read directly), some are **simulated** stand-ins for
product surfaces that have not shipped — each row says which.

| Fact family | Source of truth today | State |
|---|---|---|
| Python API reference (18 symbols) | **Real**: the `shepherd` integration facade (`shepherd/packages/meta/src/shepherd/__init__.py`), read statically by the generator; docstrings render from the actual runtime sources. | `generated` — internal build only, pre-rename banner; the rename retargets the generator. |
| API symbol snapshot + drift check | **Real**: `_generated/python-api/public-symbols.json`, regenerated and byte-compared by `5_check_everything_is_ok.sh`. | `generated` |
| Tutorial + quickstart example code | **Real code, simulated provider**: `docs_src/` examples execute in pytest against the simulation shim (`docs_src/_sim/`), the stand-in for the unshipped deterministic offline provider. Pages include this code via snippets — what you read is what ran. | `checked-example` (simulated provider) |
| CLI reference | **Simulated capture**: a checked fixture (`docs_src/_sim/cli-help.json`) plays the role of `shepherd --help` output; the generator + drift check run the real pipeline over it. | `checked-fixture` — internal build only |
| Workflow catalog | Nothing — the workflow surface has not shipped. `docs_src/workflows/fixtures/` holds the empty-fixtures sentinel. | `scaffold` |
| Install / packaging facts | names (`pip install shepherd-ai`); the distribution does not exist yet. | `scaffold` — internal build only |
| Concepts (tasks, effects, runs, workspaces) | Distilled from `docs/paradigm.md`, the spec, and the curriculum; conceptual claims only. | tasks: `shipped-source` (public) · others: `scaffold` (internal) |
| Placements / permissions concepts | Design vocabulary only — not shipped. | not yet authored |

## How a row changes

When a real source lands, the same PR updates the source, regenerates the
affected pages (`5_check_everything_is_ok.sh` fails otherwise), and flips this row — see the
runbook scenarios (S2, S7) in `docs/_runbook.md`.
