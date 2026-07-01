# Effects

> Page status: scaffold
> Source state: scaffold
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: not yet validated

*This is a concept page; it builds the mental model. Steps live in the tutorial, signatures in the reference.*

!!! warning "Scaffold — not yet runnable"
    This page is a draft against a surface that has not shipped. Treat commands and code as illustrative until the page is promoted.

A task's interior is opaque — you cannot step through the model's reasoning.
What you *can* see, completely, is everything that crosses the boundary. In
Shepherd every crossing is an **effect**: a named, typed value on one
explicit channel, where it can be answered, watched, refused, and recorded.

## Ask and Tell

Two intents cover the channel:

- **`shp.Ask`** — *I need a value.* Performing an Ask blocks the task until
  something supplies a typed answer: a reviewer's verdict, a policy decision,
  a looked-up fact.
- **`shp.Tell`** — *something happened.* Fire-and-forget; the task reports
  and moves on: a risk was flagged, a milestone passed.

An effect is a small frozen class — fields are the payload, `Ask`'s type
parameter is the answer's type:

```python
import shepherd as shp

@dataclass(frozen=True)
class ReviewVerdict(shp.Ask[str]):
    """Asked when a reviewer's decision is required."""
    summary: str
```

Inside a task body, `shp.ask(ReviewVerdict(...))` performs the effect and
waits for its answer; `shp.tell(RiskFlagged(...))` reports and returns
immediately. The defaults mirror the intent: an Ask nobody answers is an
error; a Tell nobody hears is silence, unless it declares it must be heard.

## Handlers answer; observers watch

Effects would be inert without the receiving end. Two verbs, one mechanism:

- **`shp.handle` intercepts.** A handler is authoritative — it consumes the
  effect, and what it returns *is* the answer. When handlers nest, the
  innermost wins; outer handlers never see a consumed effect.
- **`shp.observe` watches.** An observer taps the effect and lets it keep
  going. Observers stack — every observer in scope fires — which is exactly
  what audit logs and metrics want: to see without deciding.

Both install for a scope, so who-answers-what is visible in the source:

```python
with shp.handle(ReviewVerdict, ask_a_human):
    with shp.observe(RiskFlagged, write_audit_line):
        verdict = request_verdict(security)
```

The decisive detail: the model boundary is itself an effect. The same
mechanism that routes `ReviewVerdict` to a human can intercept the model
call — which is how tests run without a model at all: install a handler that
answers with a canned response. Substitution, not monkey-patching.

## Why this buys auditability and testability

- **Auditable.** Every crossing is a typed event, and every event lands in
  the run's [trace](runs.md). "What did this program do to the world?" has a
  complete, structured answer — by construction, not by best-effort logging.
- **Testable.** Behavior at any boundary is swappable from outside, without
  touching the task: answer the Asks, fake the model, assert on the Tells.
  The test installs handlers; the code under test never knows.

A handler can also do more than answer — hold the decision open, consult
someone, resume the task with a verdict, or decline to resume it at all.
That is the seed of supervision.

!!! info "Design vocabulary — not shipped yet"
    Supervisor-form handlers and live run steering are design vocabulary; they ship with the run-control and supervision product work.

## What effects are not

- **Not callbacks.** A callback is wired by a caller that knows exactly whom
  it invokes. An effect inverts that: the task states *what it needs*, and
  whoever is in scope decides how the need is met — the performer never
  names its resolver.
- **Not middleware everywhere.** There is no global pipeline every call is
  forced through. Interception is opt-in, typed, and scoped to a block —
  install nothing and effects simply meet their defaults.
- **Not log lines.** Logging describes behavior after the fact and can lie by
  omission. Effects *are* the behavior: typed, answerable, refusable, and
  recorded whether or not anyone is watching.

## Where effects sit

[Tasks](tasks.md) perform effects; the [run](runs.md) records every one; the
[workspace](workspaces.md) is the natural scope for the handlers and
observers that meet them. The
[first Shepherd app tutorial](../tutorials/first-shepherd-app.md) wires a
handler and an observer into working code.

## Going deeper

- Formal semantics: `docs/spec/04-constructs.md` §construct-ask, §construct-tell, §construct-handle, §construct-observe *(formal spec — repository reference)*
- Effect data model: `docs/spec/01-data-model.md` §sec-effects *(formal spec — repository reference)*
- Rationale: `docs/paradigm.md` *(design rationale — repository reference)*
- Teaching source: `docs/curriculum/tutorial/03-effects.md`, `docs/curriculum/tutorial/04-handlers-and-observers.md` *(internal curriculum — repository reference)*
