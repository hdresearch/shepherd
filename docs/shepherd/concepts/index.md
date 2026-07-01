# Concepts

> Page status: scaffold
> Source state: scaffold
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: not yet validated

*This is a concept page; it builds the mental model. Steps live in the tutorial, signatures in the reference.*

!!! warning "Scaffold — not yet runnable"
    This page is a draft against a surface that has not shipped. Treat commands and code as illustrative until the page is promoted.

This section is the mental-model layer of the Shepherd docs. Four ideas carry
the whole framework; each gets one page, and the pages keep linking to each
other because the ideas genuinely interlock. Steps live in the
[tutorial](../tutorials/first-shepherd-app.md), exact signatures live in the
reference — *why the framework is shaped this way* lives here.

## The four ideas

| Page | The idea in one line |
| --- | --- |
| **[Tasks](tasks.md)** | A task is a typed function whose body the model fills in; the signature is the contract. |
| **[Effects](effects.md)** | Everything a task does to the world crosses one explicit, typed, interceptable channel. |
| **[Runs](runs.md)** | Every execution leaves a durable record; debugging is reading that record, not guessing. |
| **[Workspaces](workspaces.md)** | Context — model, root, shared objects — is ambient but explicit: a scope, not a global. |

How they interlock: a **task** declares what should happen; a **workspace**
supplies the situation it happens in; calling the task produces a **run**; and
the run's trace is populated by the **effects** that crossed the boundary
along the way. Pull any one of the four out and the other three stop making
sense — which is why the reading order below matters less than it looks.

## If you came here to build

You do not need this section to ship your first feature — the
[first Shepherd app tutorial](../tutorials/first-shepherd-app.md) gets you to
working code without it. Come back when something surprises you, and enter
through the question that brought you:

- "Why did editing a *docstring* change behavior?" → [Tasks](tasks.md)
- "Who answered that request — and who else saw it?" → [Effects](effects.md)
- "What did that call actually *do*?" → [Runs](runs.md)
- "Where did the model and that binding come from?" → [Workspaces](workspaces.md)

Each page is written to stand alone; cross-links fill whatever gaps remain.

## If you came here to evaluate

Read the four pages in order — [tasks](tasks.md), [effects](effects.md),
[runs](runs.md), [workspaces](workspaces.md). They build outward from the
unit of work to its channel, its record, and its context, and each ends with
a "Going deeper" footer citing the formal spec and the design rationale. The
concept pages are honest summaries of those documents, not replacements for
them; the citations are where the precision lives.

Two honesty conventions to know before you judge anything here:

- Where a page touches surface that has not shipped (permissions, placements,
  supervision), a "Design vocabulary — not shipped yet" note names the gating
  product work. The vocabulary is stable; the runnable surface is not.
- The [source-state inventory](../reference/source-state.md) is the
  hand-maintained ledger of what these docs may claim today and where each
  fact comes from. When a concept page and the inventory disagree, the
  inventory wins.

## Going deeper

- Rationale: `docs/paradigm.md` *(design rationale — repository reference)*
- Formal semantics: `docs/spec/04-constructs.md` *(formal spec — repository reference)*
- Data model: `docs/spec/01-data-model.md` *(formal spec — repository reference)*
