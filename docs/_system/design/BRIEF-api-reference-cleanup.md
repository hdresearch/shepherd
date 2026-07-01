# Briefing: API Reference Clean-up (Items 1 & 2)

**For:** whoever picks this up — no prior knowledge of the project assumed.
**This document is your briefing.** Read it top to bottom; it has everything you
need to start.

---

## What you're doing (30 seconds)

The Shepherd Python framework's **API reference** is auto-generated from the
docstrings in the source code. Today those docstrings are written for the
framework's own engineers — they say things like *"syntax nucleus… per DECISIONS
D10… stored opaquely on `TaskMetadata`"* — which is exactly what a new user
reading the docs should never see. Two related jobs:

1. **Decide which symbols are actually public** (some exported names are internal
   plumbing that shouldn't be in the reference at all).
2. **Rewrite the remaining docstrings so they read like documentation**, not
   engineering notes.

That's it. ~18 symbols. Rough effort: **1–2 focused days.**

---

## 5-minute orientation (how this actually works)

- **Shepherd** lets you write ordinary typed Python functions that a model fills
  in. You don't need to know more than that for this task.
- **The reference is generated, not hand-written.** Each reference page is a tiny
  stub containing a `::: some.module.symbol` directive; a tool (mkdocstrings)
  reads that symbol's **docstring from the source `.py` file** and renders the
  page. **So your work is editing docstrings in the source — never the generated
  pages.**
- **The public surface is one list.** It's `__all__` in
  [`shepherd/packages/meta/src/shepherd/__init__.py`](../../../../shepherd/packages/meta/src/shepherd/__init__.py)
  — 18 symbols. That list drives the entire reference. Job 1 is curating it.
- **Ignore the name `shepherd`.** The code is named `shepherd`; the product will be
  `shepherd`. Renaming is a *separate* workstream — **do not rename anything.**
- **Don't panic about "docstrings are behavior."** You'll see that phrase all
  over the docs. It refers to docstrings on *a user's own* `@task` functions
  (where the docstring is the model's instruction). It does **not** apply to the
  framework's own API below — these are normal documentation docstrings, safe to
  edit like any code comment.
- **This is real source work.** Items 1 & 2 change the real framework
  (`shepherd/packages/…`), reviewed like any code PR — not a throwaway sandbox.
  (The `docs/shepherd/` folder is only how you *preview* your changes.)

---

## Job 1 — Decide the public API surface  *(~½ day; needs API-owner sign-off)*

18 symbols are exported. Some are framework internals that leaked into the public
list. For each, ask one question: **would a Shepherd *user* ever type this name?**
If it's only touched by the framework's own plumbing, it shouldn't be public.

**Drop-candidates (confirm with the framework/API owner — removing an export is
an API decision, not a docs decision):**

| Symbol | Why it looks internal |
|---|---|
| `VcsCoreExecutionLink` | "Identity bridge from a `RunRef` to its vcs-core execution… per Phase 0a decision D-0a-1." Pure internal plumbing. |
| `ModelRequest` / `ModelResponse` | "Provider request/response payload (embedded in `EffectDeclaration`/`EffectCapture`)." Provider-boundary internals. |
| `SINGLE_OUTPUT_KEY` | An internal schema constant (the string `"result"`). Unlikely to be user-facing. |

**Actions:**
- **Drop** → remove the name from `__all__` (with owner sign-off). It vanishes
  from the reference automatically.
- **Keep** → it must (a) have a reader-grade docstring (Job 2) and (b) get a
  "See also" entry in `_map.yml` (below).

**Finish `_map.yml`.** This file adds the "See also → concept page" footer to a
reference page. It lives at
[`docs/shepherd/reference/api/_map.yml`](../../../../shepherd/reference/api/_map.yml).
Format:

```yaml
task:        { concept: concepts/tasks.md, guide: tutorials/first-shepherd-app.md }
current_binding: { concept: concepts/workspaces.md }   # <- add entries like this
```

12 of 18 symbols are mapped; **6 are not**: `Permissive`, `ModelRequest`,
`ModelResponse`, `SINGLE_OUTPUT_KEY`, `VcsCoreExecutionLink`, `current_binding`.
For every symbol you *keep*, add a `concept:` link if a matching concept page
exists (e.g. `current_binding` → `concepts/workspaces.md`, `Permissive` →
`concepts/tasks.md`). If no concept page fits, leave it out — the generator just
omits the footer. (Don't link guides/concepts that are still scaffold.)

---

## Job 2 — Make the docstrings reader-grade  *(the bulk of the work)*

**The standard.** mkdocstrings parses **Google-style** docstrings. A reader-grade
docstring has:
- a **one-line summary** a newcomer understands (no internal jargon);
- a sentence or two on **what it's for / when to use it**;
- **`Args:` / `Returns:` / `Raises:`** sections;
- a **tiny example** when it helps;
- **zero internal references** — no "DECISIONS D10", "syntax nucleus", "Plan 04",
  "tranche", and no internal type names a reader can't see.

**Bad → good** (using `task`):

```python
# BEFORE (what's there now)
"""Decorate a function as a syntax nucleus callable task.
Both bare and parameterized usage are supported per DECISIONS D10::
    ...
guidance and name are stored opaquely on TaskMetadata.
may is the first-cut runtime-only effect-surface hook ..."""

# AFTER (reader-grade)
"""Turn a typed function into a model-backed task.

The function's signature is its contract: parameters are the inputs shown to
the model, the return type is the schema the response must satisfy, and the
docstring is the instruction the model receives. A bodyless task runs as a
single model call; give it a body to orchestrate several.

Args:
    guidance: Extra instruction, used when the function has no docstring.
    name: Display name for the task; defaults to the function name.
    may: The effects the task is allowed to perform.

Returns:
    A callable task. Call it inside an open ``workspace`` to run it.

Example:
    >>> @task
    ... def summarize(article: str) -> str:
    ...     '''Summarize this article in three bullet points.'''
"""
```

**Copy the style of these — they're already good:** `deliver`, `emit_artifact`,
`current_binding`, `ask`, `tell`.

### The worklist (all 18)

Paths are under `RT = shepherd/packages/runtime/src/shepherd_runtime/` unless noted.

| Symbol | Edit at | Action |
|---|---|---|
| `task` | `RT/nucleus/callable_task.py:189` | **Rewrite** — worst offender (jargon + internal types). |
| `handle` | `RT/effects/handle.py:52` | **Rewrite** — drop "Plan 04", "CONTRACTS C1 + DECISIONS D6, D11". |
| `Artifact` | `RT/nucleus/artifacts.py:27` | **Rewrite** — strip "per DECISIONS D17 …". |
| `RunRef` | `RT/identities.py:41` | **Rewrite** — drop "syntax nucleus" + roadmap aside. |
| `Workspace` | `RT/nucleus/workspace.py:57` | **Rewrite + expand** — 1 jargon line today. |
| `workspace` | `RT/nucleus/workspace.py:166` | **Rewrite + expand** — document `model`, `root`, `vcscore`. |
| `DeliveryFailed` | `RT/nucleus/types.py:22` | **Expand** — explain when it's raised. |
| `Run` | `RT/nucleus/types.py:104` | **Expand** — what's inspectable on it. |
| `Permissive` | `RT/nucleus/profiles.py:78` | **Write from scratch** — no docstring (see note). |
| `SINGLE_OUTPUT_KEY` | `shepherd/packages/core/src/shepherd_core/_shared/schema.py:23` | **Decide (Job 1)**; if kept, write from scratch. |
| `ModelRequest` | `RT/provider_boundary/payloads.py:109` | **Decide (Job 1)**; if kept, rewrite. |
| `ModelResponse` | `RT/provider_boundary/payloads.py:118` | **Decide (Job 1)**; if kept, rewrite. |
| `VcsCoreExecutionLink` | `RT/identities.py:11` | **Decide (Job 1)** — likely drop. |
| `deliver` | `RT/nucleus/delivery.py:134` | Good — light touch (drop "function-form"). |
| `emit_artifact` | `RT/nucleus/artifacts.py:43` | Good — leave / model to imitate. |
| `current_binding` | `RT/scope_bindings.py:98` | Good — leave / model to imitate. |
| `ask` | `RT/effects/ask_tell.py:69` | Good — light touch (drop "tranche"). |
| `tell` | `RT/effects/ask_tell.py:105` | Good — leave. |

**Note on the two empty ones** (`Permissive`, `SINGLE_OUTPUT_KEY`): they're
module-level constants. To document a constant, put a string literal on the line
*after* the assignment — mkdocstrings reads it as the docstring:

```python
Permissive = EffectSurfaceProfile("Permissive")
"""The permission profile that allows a task to perform any effect."""
```

---

## How to do it (the loop)

1. **Edit the docstring** in the source `.py` (the worklist tells you where).
2. **Regenerate + check.** From `docs_system/`:
   `./1_generate_docs_from_frozen_code.sh` *(or `uv run python run.py check --regen` once the
   cross-platform runner lands — see that folder's `README.md`)*. This re-renders
   the pages, updates the drift snapshot, and runs the gate. It must end green.
3. **Preview.** `./preview_reviewer_build.sh`, then open
   `http://localhost:8001/reference/api/<symbol>/` and read your page as a user
   would. *(The reference is internal-only until the rename — preview on :8001,
   not :8000.)*
4. **Commit together.** Your source docstring change **and** the regenerated
   prototype pages + snapshot go in the same commit — the drift gate expects them
   in lockstep.

---

## Done when

- [ ] Public symbol list decided; drops removed from `__all__` (owner signed off).
- [ ] Every *kept* symbol has a reader-grade docstring (worklist cleared).
- [ ] `_map.yml` has a See-also entry for every kept symbol that has a matching
      concept page.
- [ ] `./5_check_everything_is_ok.sh` (run from `docs_system/`) is green.
- [ ] You've eyeballed each changed page on the :8001 preview.

## Don't
- Don't rename `shepherd` → `shepherd` (separate workstream).
- Don't hand-edit the generated `docs/reference/api/*.md` files — edit the
  **source docstring** and regenerate.
- Don't delete an export without the API owner's OK.
- Don't worry about the "docstring = behavior" rule here — it's not these symbols.

## Pointers
- Public surface: [`shepherd/.../meta/src/shepherd/__init__.py`](../../../../shepherd/packages/meta/src/shepherd/__init__.py)
- See-also map: [`docs/shepherd/reference/api/_map.yml`](../../../../shepherd/reference/api/_map.yml)
- Why this matters (evidence): [`FIRST-RUN-FINDINGS.md`](FIRST-RUN-FINDINGS.md) §3.2
- Where this sits in the plan: [`PRE-LAUNCH-PREP.md`](PRE-LAUNCH-PREP.md) (items 1 & 2)
