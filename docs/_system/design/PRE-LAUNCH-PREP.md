# Pre-launch Doc System Prep

**Status (2026-06-14).** The pipeline works and CI is green. **But only 6 of 35
pages are publishable today** — the system correctly withholds the other 29. The
gaps are in the *content and the code it documents*, not the machinery. This is
the punch-list to close them. **Item 6 (pipeline hardening) is ✅ done; the rest
remain.** Evidence: [FIRST-RUN-FINDINGS.md](FIRST-RUN-FINDINGS.md).

**Launch-ready =** the developer's public path is real and promoted —
install → quickstart → tutorial → concepts → **API reference** — with no
simulated stand-ins on public pages, `shepherd` naming throughout, CI green.
The operator/workflow surface is explicitly *fast-follow* (see Scope).

## The work

| # | Workstream | Own | Missing → do | Size | Blocked by |
|---|---|---|---|---|---|
| 1 | **Public API surface** | Eng+Docs | Facade exports 18 symbols; some are internal plumbing (`VcsCoreExecutionLink`, the provider-boundary types) and 6 have no concept link. **Decide the public set, drop/annotate the rest, finish `_map.yml`.** | S | — |
| 2 | **Docs-grade docstrings** | Eng | Reference renders contributor docstrings ("syntax nucleus… per DECISIONS D10… opaquely on `TaskMetadata`"). **Rewrite the public symbols' docstrings for readers, in source.** | M | 1 |
| 3 | **The rename** | Eng | Code is `shepherd`; docs are `shepherd`. **Land shepherd→shepherd, or the generator-retarget, so reference renders `shepherd` names.** | M | — |
| 4 | **Real runnable examples** | Eng+Docs | Quickstart + tutorial run on a 96-line *simulator*, not the framework. **Ship the offline deterministic provider + `shepherd` facade; flip `conftest.py` to the real package; re-record transcripts; delete the shim.** | L | 3 |
| 5 | **Concepts** | Docs | effects/runs/workspaces teach API that isn't shipped (`shp.Ask`, `run.trace`, `ws.bind()`). **Prune each to shipped-only surface (or wait for the API), then promote.** | M | (4) |
| 6 | **Pipeline hardening** ✅ | DevEx | **DONE (2026-06-14).** Hash-locked deps (`docs-requirements.txt`); cross-platform `run.py` with `.sh` wrappers; a self-reverting `promote` helper; CI snippet finalized. All entry points verified green. | S | — |
| 7 | **Operator surface** *(fast-follow)* | Eng+Docs | CLI/guides/install describe unshipped commands. **Ship the CLI → replace the `cli-help.json` fixture with a real `--help` capture → promote CLI ref + 3 guides + install.** | L | — |
| 8 | **Entry-point rerouting sweep** *(launch window)* | Docs | Legacy surfaces (`README.md`, `docs/README.md`, `shepherd/*/README.md`, spec/scope/paradigm) still present Shepherd-era docs as if public. **Apply the route/label/leave decisions in [`MIGRATION-entry-points.md`](MIGRATION-entry-points.md)** so first-run users land in `docs/shepherd/`. A one-off sweep — run against the *final* entry points in the launch window, not now (they'll move/sweep before then). The inventory is done; only the application is pending. | S | run in launch window |

*Promotion is each workstream's closing commit (flip status to `release-ready`,
un-exclude, add to nav — `./5_check_everything_is_ok.sh` enforces it), not a separate step.*

## Sequencing

- **Start now, no blockers:** 1, and the pruning in 5. *(6 ✅ done.)*
- **Critical path:** 1 → 2, and 3 → both publishable reference and 4. Reference
  ships only after **1 + 2 + 3**; real examples ship after **3 + 4**.
- **Three real efforts gate the launch:** the public API surface (1+2), the
  rename (3), and real examples (4). Everything else hangs off these.
- **Launch-window sweep (mandatory, one-off):** 8 — the entry-point rerouting,
  applied to the *final* surfaces after the rename settles. The inventory is
  ready; only the application waits.

## Scope & merges

- **Merge 1 + 2** — same files, same reviewers, one "fix the public API surface"
  effort. Do them together.
- **Cut 7 from launch** if the CLI won't ship in time. Keep the honest
  "not shipped yet" stub; don't let the operator surface block the developer
  launch.
- **Don't expand scope:** the facade (18 symbols) is the public surface by
  design — the other 8 packages stay out. Resist documenting them for v1.
