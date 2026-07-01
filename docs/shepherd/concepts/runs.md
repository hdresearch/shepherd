# Runs

> Page status: scaffold
> Source state: scaffold
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: not yet validated

*This is a concept page; it builds the mental model. Steps live in the tutorial, signatures in the reference.*

!!! warning "Scaffold — not yet runnable"
    This page is a draft against a surface that has not shipped. Treat commands and code as illustrative until the page is promoted.

Calling a task gives you more than a return value. Every call produces a
**run** — the durable record of that one execution: what was sent, what came
back, what was decided along the way, and what was produced besides the
answer. The value is one field of the record, not the whole story.

## One execution, fully recorded

A run carries four things worth naming:

- **The outcome.** Every run ends in exactly one of four shapes — it
  *finished* with a value, *failed* with an error, was *exhausted* when a
  budget ran out, or was *stopped* by a cancellation. All four are values you
  can inspect; none is a stack trace you have to scrape.
- **The trace.** The ordered record of every boundary crossing.
- **Artifacts.** Side-channel outputs the task chose to keep.
- **Usage.** What the run cost.

The record survives every ending. A failed run is not an absence of
information — it is *more* information: everything up to the failure, kept.

## The trace: debugging is reading, not guessing

You cannot set a breakpoint inside the model; there is no body to step
through. What you have instead is the complete, ordered sequence of
everything that crossed the boundary — every [effect](effects.md) the task
performed, every model request and response, every nested task call, every
artifact emission.

```python
run = review_change.detailed(diff)
for event in run.trace:
    print(event.kind, event)
```

So debugging changes character. The question is no longer "can I reproduce
this under a debugger?" but "what does the record say was actually sent, and
what actually came back?" Events are typed — filter by kind, narrow by
class, or use the built-in projections for common views like the prompts or
the model exchanges. Forensics rather than archaeology: the evidence was
collected at the moment it happened, not reconstructed afterward.

## Artifacts: what a task keeps besides the answer

Some tasks produce things callers want alongside the return value — the full
audit behind a one-paragraph summary, a generated report, a patch. Those are
**artifacts**: emitted from inside the task, collected on the run, and
distinct from the return value by design. The return value is what the
*caller* consumes; artifacts are what reviewers, auditors, and downstream
tools consume. They persist across all four endings — a cancelled run keeps
everything it had emitted up to the moment it stopped.

## Runs make tasks comparable — and replayable

Because the record is data, runs compose with ordinary reasoning:

- **Compare.** Two runs of the same task — different model, different
  docstring wording, different day — are two values. Diff their traces,
  compare their outcomes side by side. "Did the upgrade change behavior?"
  becomes a question about two records, not two recollections.
- **Replay.** A recorded exchange can stand in for the live model: feed
  recorded answers back through a [handler](effects.md) and the same code
  runs deterministically. The trace's structure — complete, ordered, typed —
  is what branch-and-replay machinery builds on.

Watching a run *while it executes* — iterating the trace live, pausing,
steering — is the supervision arc built on this same record.

!!! info "Design vocabulary — not shipped yet"
    Live run steering and the supervision surface are design vocabulary; they ship with the run-control product work.

## What a run is not

- **Not a log file.** Logs are best-effort strings someone remembered to
  print. The trace is complete by construction — boundary crossings are
  effects, and effects are recorded — and every entry is a typed value.
- **Not just the return value.** Treating the value as the whole output
  throws away the evidence. The run *is* the output; the value is its
  headline.

## Where runs sit

A [task](tasks.md) declares; a [workspace](workspaces.md) situates; the run
records. The [first Shepherd app tutorial](../tutorials/first-shepherd-app.md)
has you reading your first trace within minutes of your first task call.

## Going deeper

- Formal semantics: `docs/spec/04-constructs.md` §sec-runs-constructs *(formal spec — repository reference)*
- Trace surface: `docs/spec/04-constructs.md` §construct-trace *(formal spec — repository reference)*
- Artifacts: `docs/spec/04-constructs.md` §construct-emit-artifact *(formal spec — repository reference)*
- Outcome and trace data model: `docs/spec/01-data-model.md` §sec-traces, §type-outcome *(formal spec — repository reference)*
- Rationale: `docs/paradigm.md` *(design rationale — repository reference)*
- Teaching source: `docs/curriculum/tutorial/06-artifacts-and-the-trace.md` *(internal curriculum — repository reference)*
