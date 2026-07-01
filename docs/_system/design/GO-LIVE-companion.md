# Go-Live Companion — background & decisions

Everything the [Go-Live Runbook](README.md) deliberately leaves out. **Not needed to put the site online** — read these only when you want the *why*, the deeper mechanics, or the remaining content work.

| Read this | For |
|---|---|
| [](../../the design proposals) | The documentation-structure contract and the reasoning behind it. |
| [pipelines.md](pipelines.md) | How each page is produced — the two production pipelines + the build step. |
| [PRE-LAUNCH-PREP.md](PRE-LAUNCH-PREP.md) | The pre-launch content punch-list (docstrings, rename, examples, concepts, CLI, and the entry-point rerouting sweep). |
| [BRIEF-api-reference-cleanup.md](BRIEF-api-reference-cleanup.md) | The public-API-surface + docstring work, symbol by symbol. |
| [MIGRATION-entry-points.md](MIGRATION-entry-points.md) | The entry-point rerouting inventory (applied in the launch window — PRE-LAUNCH item 8). |
| [FIRST-RUN-FINDINGS.md](FIRST-RUN-FINDINGS.md) | The evidence that drove the punch-list. |
| `docs/shepherd/reference/source-state.md` | The live ledger of what the docs may claim today (real vs placeholder). |

> **and `pipelines.md` are the authoritative pair** (the prior `DESIGN.md` is retired).

## Prove the guarantees (60s)

1. **Default-deny:** add any `docs/shepherd/foo.md` with a metadata block → it appears on the reviewer site (:8001) automatically and **cannot** reach the public site (:8000); `5_check_everything_is_ok.sh` stays green. Drop the metadata → the gate names the page and the missing keys.
2. **Drift:** append a byte to `docs/_generated/shepherd/python-api/public-symbols.json` → `5_check_everything_is_ok.sh` fails with the fix command.
3. **Docs = behavior:** change a transcript in `docs/_src/shepherd/_sim/transcripts.json` → the tutorial's "Expected output" test fails — the page can't lie.

## Key operational facts (so they're not buried)

- The pipeline builds **two** sites: `docs/_system/site/shepherd/` (public, default-deny — **deployable**) and `docs/_system/site/internal/` (full reviewer build — **never deploy**).
- Only `release-ready` pages reach the public build; everything else is withheld by the gate. `run.py pages` shows the split.
- Versioned URLs (`/latest`, `/v1.0`, `/dev` via `mike`) are a deferred fast-follow; the runbook ships a single current build.
- The **entry-point rerouting** (legacy README/spec surfaces → `docs/shepherd/`) is a mandatory launch-window sweep tracked as PRE-LAUNCH item 8 — separate from putting the site online.
- `run.py` (in `docs_system/`) is the one entry point for the pipeline: `check [regen]`, `preview`, `preview-internal`, `promote <page>`, `pages`. The `.sh` files are thin wrappers over it. All commands run from `docs_system/`.
