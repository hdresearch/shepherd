# Go-Live Runbook — Shepherd docs

**Follow top-to-bottom to put the public documentation site online. Every command runs from `docs_system/` (the `.sh` wrappers cd there for you).** (Background, design, and the why: [GO-LIVE-companion.md](GO-LIVE-companion.md) — not needed here.)

## Prerequisites — confirm all before you start

- [ ] **Codebase is frozen** and ready for documentation prep.
- [ ] **Layout complies with** — `docs/shepherd/`, `docs/_src/shepherd/`, `docs/_generated/shepherd/` exist at the repo root and `docs_system/mkdocs.yml` has `docs_dir:../shepherd`.
- [ ] **`uv` is installed and on PATH** — the only tool required; it installs the pinned dependencies automatically.
- [ ] **Network access on the first run** — `uv` fetches the pinned deps (`docs-requirements.txt`) once, then caches them.
- [ ] **The frozen public API package is present** in the repo and the reference generator (`docs_system/scripts/_facade.py`) targets it — the pipeline reads it read-only to regenerate the API reference.
- [ ] **A deploy target exists** for the static `docs/_system/site/shepherd/` output (GitHub Pages, an S3/CDN bucket, or an internal static host). For the GitHub Pages path: `git` + push access.

## Steps

**1. Regenerate from the frozen code and run the full pipeline.**
```bash
./1_generate_docs_from_frozen_code.sh
```
Confirm the last line reads **`ALL GREEN`**. If not, the output names the failing check — fix it and re-run before continuing.

**2. Confirm what will be public.**
```bash
./2_show_all_pages.sh
```
The `PUBLIC` = `yes` rows are exactly the pages that will ship. If a page that should be live shows `no`, do step 3; otherwise skip it.

**3. (Only if needed) Publish a page cleared for launch.**
```bash
./3_publish_a_page.sh <page> # e.g../3_publish_a_page.sh concepts/runs.md
./5_check_everything_is_ok.sh # re-verify -> ALL GREEN
```

**4. Preview the public site locally.**
```bash
./4_preview_the_site.sh # serves http://localhost:8000; Ctrl-C to stop
```
Open http://localhost:8000 and click through the nav.

**5. Make any final tweaks, then re-verify.**
Edit pages under `docs/shepherd/`, then:
```bash
./5_check_everything_is_ok.sh # must stay ALL GREEN
```

**6. Deploy the public build.**
The green run already built the public site to **`docs/_system/site/shepherd/`** — deploy that directory only, **never** `docs/_system/site/internal/`.
```bash
# Any host: publish the contents of docs/_system/site/shepherd/

# GitHub Pages (checks first, then rebuilds + pushes to gh-pages):
./6_deploy_the_site.sh
```

**7. Verify live.**
Open the production URL and confirm the home page and nav load.

**Done — the site is online.**
