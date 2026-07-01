# Docs-system runbook (prototype)

Build-excluded from both sites; lives with the docs for maintainers. Deeper
procedures live in the docs-system folder `docs_system/design/` (`README.md`, `pipelines.md`, `PRE-LAUNCH-PREP.md`).

| Red check | Meaning | Action |
|---|---|---|
| Snapshot/page drift | facade symbols/signatures/docstrings changed; generated docs stale | `./1_generate_docs_from_frozen_code.sh`, review diff, commit together (S2) |
| Example test | a documented example no longer behaves as shown | fix the example or catch the regression (S2/S5) |
| Membership gate | metadata/nav/exclusion disagree (invariant id printed) | fix the promotion triple (S4) |
| Built-output assertion | public site contains an unsanctioned page, or shipped unstyled | find the stray un-exclusion / fix `exclude_docs` negation (S4) |
| Stale-name | `shepherd`/`device` leak on a public page | fix, or expected-forward entry during the rename window (S12) |

Scenario index (procedures in the docs-system folder above): S1 deploy · S2 code-change
drift · S3 add page · S4 promote · S5 examples · S6 agent drafting · S7 rename
retarget · S8 docstring pass · S9 versioned release · S10 preview/share ·
S11 dep update / Zensical checkpoint · S12 rename-window failures · S13
emergency bypass.
