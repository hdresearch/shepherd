# Entry-point rerouting inventory ( §"Target Layout" / step 3)

> Status: scaffold-PR deliverable · File role: migration inventory · Audience: maintainers

requires the scaffold PR to inventory existing entry points that currently
look like public docs and apply a **route / label / leave** decision to each.
This table is the inventory; all eleven entry points below were confirmed to
exist in the repo. Actions follow the recommendations.

- **Route** — page stays live and points first-run users to `docs/shepherd/`.
- **Label** — page stays live but clearly states it is repository, migration,
  historical, specification, proposal, engineering, or maintainer material.
- **Leave** — page is not a first-run surface and needs no scaffold-PR change;
  the row still records why it was left alone.

| Entry point | Current role | Action | Owner | Follow-up |
|---|---|---|---|---|
| `README.md` | Repository landing page | **Route** first-run readers to `docs/shepherd/`; keep workspace links | Docs owner | Revisit at launch |
| `docs/README.md` | Shepherd/spec documentation landing | **Label** as repository/spec documentation; not the Shepherd public homepage | Docs owner | Sweep or split after release docs stabilize |
| `docs/curriculum/tutorial/README.md` | Long-form Shepherd tutorial index | **Label** as curriculum/migration; selectively port release-safe structure | Curriculum/docs owner | Rewrite after release surfaces settle |
| `docs/spec/README.md` | Formal Shepherd specification entry | **Label** as formal specification, not first-run docs | Spec owner | Decide later whether a swept spec is served publicly |
| `docs/scope.md` | v1.0 boundary / acceptance-demo framing | **Label** as repository release-scope reference; link only with context | Release owner | Reconcile with public demo docs when runnable |
| `docs/paradigm.md` | Project rationale | **Label** as rationale/reference, not quickstart guidance | Docs owner | Link from concepts only when helpful |
| `shepherd/README.md` | Shepherd package/workspace landing | **Label** as Shepherd-era material, or route first-run users to Shepherd docs | Package owner | Update when package layout changes |
| `shepherd/docs/README.md` | Shepherd guide index | **Label** as migration/reference, or route to Shepherd docs | Package/docs owner | Sweep individual pages later |
| `shepherd/examples/README.md` | Current + legacy example inventory | **Label** as migration/reference; route first-run examples to `docs/_src/shepherd/` | Examples owner | Port only executable release examples |
| `shepherd/examples/tutorials/README.md` | Shepherd tutorial/example status table | **Leave** as migration inventory; do not treat numbered tutorials as launch docs | Examples owner | Reuse the status-table pattern for Shepherd inventory |
| `AGENTS.md` | Contributor/agent working instructions | **Leave** as repository contributor material | Maintainer owner | Update only stale public-docs pointers |

**Applying the decisions is deliberately NOT done here.** Editing the real
`README.md`, `docs/README.md`, `shepherd/…` surfaces touches main repository work
outside this prototype; per the standing isolation rule it waits for owner
sign-off. This inventory is the analysis step 3 calls for; the edits are
the follow-up.
