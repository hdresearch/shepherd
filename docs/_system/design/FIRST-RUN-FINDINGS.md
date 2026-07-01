# First-run findings — building the docs from the codebase

> Evidence from running the docs system end-to-end against the repo and reading
> the actual content. Drove the [PRE-LAUNCH-PREP.md](PRE-LAUNCH-PREP.md)
> punch-list. Paths below use the integrated layout (`docs/shepherd/`,
> `docs/_src/shepherd/`, `scripts/`); the analysis predates the re-site but
> the findings are unchanged.

## TL;DR
The machinery is real and green. The gaps are in the **content and the code it
documents**, not the pipeline: the public site is **6 pages**, the gate correctly
withholds the other **29**, and the parts most derived from code (reference) are
the parts that can't ship yet.

## 1. Process
- The pipeline runs green first try; only `uv` is needed.
- *(Since fixed by Item 6 — pipeline hardening:)* deps were unpinned, entry points
  were bash-only (Windows blocked), and promotion was a manual two-file edit.
- "Pull from the actual sources" is true for **one of three** generators: the API
  reference reads the live `shepherd` facade; the CLI reads a simulated fixture;
  the examples run against a stand-in.

## 2. The actual run — public vs withheld
- Public site = **6 pages** (home, quickstart, first-app tutorial, the `tasks`
  concept, the source-state ledger, the operator stub).
- Asked the gate to promote all 35: it returned **58 errors (29 LEAK + 29 ADMIT)**
  — it correctly refuses every scaffold page. That count is the size of the gap.

## 3. Content — the real gaps
### 3.1 The inversion
The content *most* derivable from the code (API reference) is **withheld**; the
content *promoted* to the public (the two runnable examples) is the **least**
connected to today's code.

### 3.2 Reference = contributor docstrings, not reader docs
Reference pages are thin `:::` stubs, so the rendered page **is** the source
docstring. Today's skew internal: `task` renders *"syntax nucleus… per DECISIONS
D10… stored opaquely on `TaskMetadata`… first-cut runtime-only effect-surface
hook"* (`shepherd/packages/runtime/src/shepherd_runtime/nucleus/callable_task.py`).
`deliver`, `emit_artifact`, `current_binding`, `ask`, `tell` are reader-grade;
`Permissive` and `SINGLE_OUTPUT_KEY` have **no docstring at all**. → the
highest-leverage fix is a docs-grade docstring pass **in source** (PRE-LAUNCH Items
1–2; the [brief](BRIEF-api-reference-cleanup.md)).

### 3.3 Concepts teach unshipped API
`concepts/effects.md` teaches `shp.Ask`/`shp.Tell`/`shp.observe` (not in `__all__`);
`runs.md`/`workspaces.md` use `run.trace`, `.detailed()`, `ws.bind`. Honestly
labelled scaffold, but unpromotable until the code catches up.

### 3.4 The promoted examples don't touch the real code
`quickstart/hello.py` and the tutorial `import shepherd` → a 96-line **simulation
shim** (`docs/_src/shepherd/_sim/`), not the framework. `checked-example` proves the
simulator runs, not that the framework does.

### 3.5 Naming
Code is `shepherd`; docs are `shepherd`. The reference is rename-gated.

### 3.6 Public-facade hygiene
`__all__` exports 18 symbols; `_map.yml` links 12. The 6 unmapped — `Permissive`,
`ModelRequest`, `ModelResponse`, `SINGLE_OUTPUT_KEY`, `VcsCoreExecutionLink`,
`current_binding` — render with no concept home, and several read as internal
plumbing that may not belong in the *public* reference at all.

## 4. Output
- Internal jargon ships into rendered reference (§3.2).
- No public API reference at all — the first thing a developer looks for.
- The public `Workflows` nav section resolves to "nothing shipped yet" (honest,
  but a dead-endy first impression).

## 5. What it would take to ship a complete public site
1. Docs-grade docstrings on the ~18 facade symbols (source-side).
2. Land the rename (or the generator-retarget).
3. Decide the real public facade; finish `_map.yml`.
4. Ship the offline provider + `shepherd` facade; run the migration contract for
   real (delete the `conftest.py` shim path).
5. Mature the concepts to shipped-only surface.
6. Ship the CLI → real `--help` capture → promote the operator surface.
