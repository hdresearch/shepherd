# Workspaces

> Page status: scaffold
> Source state: scaffold
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: not yet validated

*This is a concept page; it builds the mental model. Steps live in the tutorial, signatures in the reference.*

!!! warning "Scaffold — not yet runnable"
    This page is a draft against a surface that has not shipped. Treat commands and code as illustrative until the page is promoted.

A [task](tasks.md) deliberately says nothing about which model executes it,
which directory it works against, or which domain objects are at hand. That
silence is what keeps tasks reusable. The missing context comes from the
**workspace** — the ambient scope your tasks run inside:

```python
import shepherd as shp
from shepherd.providers import claude

with shp.workspace(model=claude("sonnet-4-5"), root="./my-project") as ws:
    ws.bind(Repository(name="auth-service", languages=("python",)))
    review = review_change(diff)
```

Everything called inside that block — directly or nested arbitrarily deep —
sees the same model, the same root, and the same bound `Repository`.

## Explicit, but ambient

Context handling usually forces a bad choice:

- **Thread it as parameters**, and every signature drags configuration
  through layers that never use it. On a task this is worse than clutter — it
  corrupts the signature's meaning, because task parameters are supposed to
  be *evidence for the model*, not plumbing for the framework.
- **Make it global**, and configuration becomes invisible, mutable from
  anywhere, and hostile to tests and concurrency.

The workspace is the third option: **explicit but ambient**. Ambient, because
nothing threads it by hand — any task in the block's dynamic extent can reach
it. Explicit, because it is a `with` block: you can point at the exact line
where the context begins and the exact line where it ends. Nest a second
workspace and the inner one shadows the outer for its extent; leave the block
and the outer context is restored. It is a scope, not a mutable setting.

## Bindings: shared objects, looked up by type

Beyond the model and the root, programs share longer-lived objects across
tasks — a repository description, a user identity, a database handle. Bind
them once on the workspace, and any task fetches them by *type* with
`shp.current_binding` — no string keys, no hidden registry, and a missing
binding is a typed error rather than a `None` surprise. Inner scopes can
rebind, and the innermost binding wins — which is how tests substitute a fake
without touching the code under test, and how a supervisor scopes a domain
object to one child.

## The same scope will carry more

Model, root, and bindings are the workspace's job today. The same scope is
where execution placement — choosing the contained environment the work runs
in — and a workspace-wide default policy attach in the target design: one
place where "the situation" is declared, whatever the situation includes.

!!! info "Design vocabulary — not shipped yet"
    Placement and workspace-level policy are design vocabulary from the design proposals; they ship with the placement registry and the supervision product work.

## The triangle: task, workspace, run

Three nouns are easy to blur and worth keeping sharp:

- A **[task](tasks.md)** is the *declaration* — what should happen, typed.
  Timeless and context-free.
- A **workspace** is the *situation* — which model, where, with what at
  hand. It spans many calls.
- A **[run](runs.md)** is the *event* — one execution, fully recorded.

Call a task inside a workspace and you get a run. Same task, two different
workspaces: two runs you can [compare](runs.md). Same workspace, many tasks:
one consistent situation. Each noun answers a different question — *what*,
*where and with what*, and *what happened*.

## What a workspace is not

- **Not a global config singleton.** It is scoped, nestable, and shadowable;
  two workspaces can coexist in one program without ever seeing each other.
- **Not a conversation.** A workspace accumulates no model memory between
  calls. Tasks inside it remain independent invocations that happen to share
  configuration — context here is *configuration*, not *history*.

## Where workspaces sit

The [first Shepherd app tutorial](../tutorials/first-shepherd-app.md) opens
its workspace in the first ten lines. [Effect](effects.md) handlers commonly
install at workspace scope, and every [run](runs.md) carries the context it
executed under as part of its record.

## Going deeper

- Formal semantics: `docs/spec/04-constructs.md` §construct-workspace, §sec-bindings *(formal spec — repository reference)*
- Typed lookup: `docs/spec/04-constructs.md` §construct-current-binding *(formal spec — repository reference)*
- Ambient-context model: `docs/spec/02-execution-model.md` §sec-ambient-context *(formal spec — repository reference)*
- Rationale: `docs/paradigm.md` *(design rationale — repository reference)*
- Teaching source: `docs/curriculum/tutorial/07-workspaces-and-ambient-context.md` *(internal curriculum — repository reference)*
