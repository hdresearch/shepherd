# Shepherd documentation — how each page gets built (runbook)

> **Two pipelines produce every page; one build step ships them all.** This is the
> runbook: which pipeline makes which page, what each stage does, and what's
> already built vs. still to build. Every pipeline ends in named files — no tracks,
> no theory. Companions: [PRE-LAUNCH-PREP.md](PRE-LAUNCH-PREP.md) (the schedule),
> [BRIEF-api-reference-cleanup.md](BRIEF-api-reference-cleanup.md) (the docstring
> backlog), [FIRST-RUN-FINDINGS.md](FIRST-RUN-FINDINGS.md).

## The page map — which pipeline makes which page

The prototype's nav is the target: **35 pages today** (19 generated + 16
authored), plus a few the nav implies that don't exist yet.

| Section | Page(s) | Producer |
|---|---|---|
| **Home** | `index.md` | Authored |
| **Start** | `start/quickstart.md` | Authored **+ tested code** |
| | `start/install.md` | Authored |
| **Tutorials** | `tutorials/index.md` | Authored (landing) |
| | `tutorials/first-shepherd-app.md` | Authored **+ tested code** |
| | `tutorials/2…N` *(nav implies a series — to commission)* | Authored **+ tested code** |
| **Concepts** | `concepts/index.md` | Authored (landing) |
| | `concepts/tasks.md` · `effects.md` · `runs.md` · `workspaces.md` | Authored |
| **Guides** | `guides/index.md` | Authored (landing) |
| | `guides/configure-provider.md` · `debug-your-first-run.md` | Authored |
| | `guides/<more>` *(to commission)* | Authored |
| **Reference** | `reference/api/*.md` ×18 | **Reference** |
| | `reference/cli.md` | **Reference** (from `--help`; when the CLI ships) |
| | `reference/index.md` | Authored (landing) |
| | `reference/source-state.md` | Authored (hand-maintained ledger) |
| **Workflows** | `workflows/index.md` | Authored (operator) |

Two audiences run through this — **builders** (write Shepherd code) and
**operators** (run packaged workflows from the terminal). Search, versioning, and
the nav come free with the site tooling.

### Where this is defined, and what to run

This table is not just prose — it's backed by a real manifest and a driver:

| What | Defined in | Run / see it |
|---|---|---|
| Every expected page → producer, sources, test | **`pages.yml`** | `run.py pages` (live status table) |
| One page's definition (e.g. the quickstart) | its row in `pages.yml` + the page file | `run.py pages quickstart` |
| The reference page set | the code facade (`__all__`) | `run.py regen` / `run.py check` |
| Publish a finished page | its metadata + the nav | `run.py promote <page>` |
| Validate everything (generate, drift, gates, tests, build) | the scripts in `scripts/` | `run.py check` |

So the **quickstart** is defined by its `pages.yml` row — `authored`, backed by
`docs/_src/quickstart/hello.py` and tested by `docs/_src/quickstart/test_hello.py`
— and `run.py pages quickstart` prints exactly that. What is *not* built yet: the
authoring **stages** A1–A3 (draft/fact-check/readability) and the four new checks
— those are still prose below, flagged **[to build]**.

---

## Reference pipeline — ships the 19 generated pages

**Produces:** `reference/api/<symbol>.md` ×18 and `reference/cli.md`.
**Deliverable each run:** those `.md` files + the committed code snapshot they're
checked against.

**Stage R1 — Make the docstrings good.** The pages are just the code's docstrings
rendered, so this is the only place quality is set.
- **R1a** Deterministic pre-pass *(to build — small script over the 18 public
  symbols)*: flag a missing docstring / parameters that don't match the signature
  / an example that won't parse.
- **R1b** Bounded AI pass *(to build — one agent per flagged symbol)*: check the
  docstring against the real code. **If it's clearly crafted, fact-check only —
  never reword.** If thin or empty, attach a suggested draft.
- **R1c** *(human)* fix the flagged docstrings in the code.
- **Backlog today:** 8 — rewrite `task`, `handle`, `Artifact`, `RunRef`,
  `Workspace`, `workspace`; write `Permissive`, `SINGLE_OUTPUT_KEY`. (Full brief:
  [BRIEF-api-reference-cleanup.md](BRIEF-api-reference-cleanup.md).)

**Stage R2 — Generate + verify.**
- `gen_shepherd_ref_pages.py` *(exists)* → the 18 pages.
- `gen_shepherd_api_inventory.py --check` *(exists)* → drift gate; fails on any
  code↔page mismatch.
- CLI: `gen_cli_reference.py` *(exists, off a fixture)* → swap the fixture for the
  real `shepherd --help` when the CLI ships.

**Fails the build on:** drift mismatch; a public symbol with no docstring, a
param-mismatch, or an unparseable example. **Human gate:** approve the R1 fixes.
**Runs today:** all of it except the real CLI capture.

---

## Authored pipeline — ships the 16 prose pages (+ the ones to commission)

**Produces:** Home, Quickstart, Install, the Tutorials, the Concepts, the Guides,
the Reference landing, the Source-state ledger, the Workflows/operator page.
**Deliverable each run:** one finished `.md` — plus the tested `docs/_src/*.py` for
any page that carries code.

**Run per page:**
- **A1 Draft** *(to build — agent)* — write or rewrite from real sources: the code,
  the design notes, the existing draft. No invented API.
- **A2 Fact-check** *(to build — the page-claim check + an agent)* — every claim
  and code reference must exist in the real public surface; reject unshipped API
  unless the page carries an explicit "teaches-unshipped" marker.
  - **Backlog today:** `concepts/effects.md` (`shp.Ask/Tell/observe`),
    `concepts/runs.md` (`run.trace`, `.detailed()`), `concepts/workspaces.md`
    (`ws.bind`) — each: prune, label "preview", or hold.
- **A3 Readability** *(to build — agent)* — simplify to the bar for its kind: a
  tutorial teaches a path, a how-to does one job, a concept builds a model. Flag
  where a picture helps.
- **A4 Test the code, if any** — `pytest docs_src` *(exists)* runs the embedded
  examples; embed the exact tested file. If using a stand-in, the fidelity check
  *(to build)* confirms it matches the real framework.
  - **Backlog today:** the stand-in's `deliver(value)` ≠ the real
    `deliver(result_type, *, goal, …)`.
- **A5 Sign-off + publish** *(human; promote via `run.py promote` — exists)* — flip
  the page live.

**Fails the build on:** page-claim, example tests, membership (only finished pages
go public). **Human gate:** the A5 sign-off. **Runs today:** A1–A4; A5 publish
waits for freeze.

---

## Build & publish — ships all 35 (produces no page of its own)

Runs after freeze, once the pages are green; repeats each release.
- `run.py check` *(exists)* — strict build of both site variants + the existing gates.
- Link-check, versioning (`mike`), search, sitemap *(to wire)*.
- **Provenance check** *(to build)* — no public page backed by a placeholder/stand-in.
- **Completeness check** *(to build)* — no page from the map is missing.
- **Human gate:** final release approval.

---

## The backlog right now (what to actually do)

**Fix — pages that already exist:**
- 8 docstrings → R1 *(Reference)*.
- 3 concept pages teaching unshipped API → A2 *(Authored)*.
- thin drafts — `install.md`, the 2 guides → A1–A3 *(Authored)*.

**Commission — pages the nav implies but that don't exist:**
- tutorials beyond the first; the rest of the guide set → *Authored*.

**Build — checks that don't exist yet:**
- the R1a/R1b docstring check · the A2 page-claim check · the A4 stand-in fidelity
  check · the provenance + completeness checks.

## Runs today vs. gated
- **Today (no freeze needed):** R1, R2 generation, A1–A4, and building the new checks.
- **Waits for freeze:** publishing (A5) + Build & publish.
- **Waits for the provider:** A4 against the real framework (stand-in until then).
- **Waits for the CLI:** `reference/cli.md` + the operator guides.

## Open decisions
1. Does the offline example-runner ship by launch? *(A4: real vs. stand-in)*
2. Is the operator/CLI surface in the launch, or does it follow later?
3. For each not-yet-shipped idea in a concept/guide: cut it, label it "preview", or
   hold it back?

---

## Appendix — mechanics (skip unless you're building the checks)

- **Docstring check — scope & signals.** Public symbols (the `__all__` surface) get
  the full bar (reader-grade + correct); the types they reference get correctness
  only. The only signals allowed to fail the build are *deterministic*: missing
  docstring, parameters that don't match the signature, an example that won't
  parse. Everything an AI judges (thinness, jargon, meaning-drift) is advisory.
- **"Don't rewrite crafted work," made reliable.** The crafted/not decision is
  computed once and cached against a hash of the docstring text, so it can't flip
  between runs. "Intentionally thin" exceptions are keyed the same way and expire
  when the text changes.
- **Page-claim & stand-in checks.** Pull every API reference out of a finished page
  — *including method calls like `run.trace`*, not just `shp.<Name>` — and require
  each to exist in the real public surface, unless an explicit "teaches-unshipped"
  marker excuses it. The stand-in is compared by name *and* call shape against the
  real code.
- **Outputs.** A per-symbol audit file (diffable) + a human triage list, worst-first.
- **Grounding facts.** 18 public symbols; reference pages are thin one-line stubs,
  so the rendered page is exactly as good as the docstring; examples currently run
  against a 96-line stand-in.
- **Considered and deferred.** A "staleness" detector (a second fingerprint of the
  code to catch a docstring that quietly went out of date) — it can only *hint*,
  never prove, and the fact-check pass already catches the real cases. Revisit only
  if drift becomes a real problem.
