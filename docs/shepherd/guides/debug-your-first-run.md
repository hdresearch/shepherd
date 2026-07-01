# Debug your first run

> Page status: scaffold
> Source state: scaffold
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: not yet validated

*This is a how-to guide for one job. New to Shepherd? Start with the tutorial. For exact APIs, see the reference.*

!!! warning "Scaffold — not yet runnable"
    This page is a draft against a surface that has not shipped. Treat commands and code as illustrative until the page is promoted.

**Job.** Your first run failed; identify which of the three classic
first-run failures you hit, and fix it.

**Prerequisites.** You attempted the [quickstart](../start/quickstart.md) or
the [tutorial](../tutorials/first-shepherd-app.md).

## Steps

1. **Read the exception type, not just the message.** Shepherd fails with
   typed errors, and the type names the layer that failed: task definition
   (`TypeError`), missing context (`RuntimeError`), or the model's response
   (`shp.DeliveryFailed`).

2. **Match it in the table.** All three rows are real behaviors, asserted by
   this prototype's checked examples today — the page stays scaffold only
   because the surrounding tooling (traces, `shepherd doctor`) has not
   shipped:

    | What you see | Why | Fix |
    |---|---|---|
    | ``RuntimeError: call tasks inside `with shp.workspace(model=...)` `` | The task was called with no workspace open. There is no default model and no accidental network call — Shepherd refuses instead. | Wrap the call: `with shp.workspace(model=claude("sonnet-4-5")): ...` |
    | `shp.DeliveryFailed: ...` | The model's response could not be coerced into the declared return type — missing dataclass fields, or the wrong shape where `-> str` was promised. The message names what was missing. | Tighten the return type and docstring so the contract is unambiguous, then rerun; the docstring is the instruction the model is following. |
    | `TypeError: Bodyless callable task ... must declare a docstring or guidance=` | A bodyless `@shp.task` has no docstring. The docstring **is** the model-call goal, so omitting it is an error at definition time, not a silent no-op. | Write the docstring: first line is the job, the rest is elaboration. |

3. **Re-run the checked examples** to confirm your environment is sound:

    ```bash
    pytest docs_src/quickstart/ docs_src/tutorials/
    ```

## Expected result

The failing call completes: the quickstart prints its three bullets, the
tutorial prints its `bugfix/high: approve - ...` line, and `pytest` over
`docs_src/` is green.

## If it fails

- A fourth, different error? Check the
  [source-state inventory](../reference/source-state.md) — you may be using
  a surface these docs have not promoted yet.
- `shepherd doctor` (planned CLI, unshipped) will be the one-command
  diagnosis — installed packages, providers, placements, and fix commands —
  once it ships.
