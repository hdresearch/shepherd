# Quickstart

> Page status: release-ready
> Source state: checked-example
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: docs_src/quickstart/test_hello.py

*Quickstart — the fastest working run. To learn the concepts in order, see the tutorial; for exact APIs, see the reference.*

Five lines of Python, one model-backed function, deterministic output. That is
the whole page.

## Install

```bash
pip install shepherd-ai
```

One honest line first: this prototype runs every documented example against a
recorded, deterministic offline provider — no credentials, no network — and
the [source-state inventory](../reference/source-state.md) is the ledger of
exactly what is real today.

## Run

Save this as `hello.py` and run `python hello.py`:

```python
--8<-- "quickstart/hello.py:hello"
```

What the five lines do:

- `@shp.task` turns a typed function into something Shepherd can run. The
  function has no body, on purpose.
- The **docstring** is the instruction the model receives; the **return
  type** (`-> str`) is the contract the response must satisfy.
- `shp.workspace(model=claude("sonnet-4-5"))` pins which model every task
  call inside the block runs against.

## Expected output

```text
- Shepherd turns typed Python functions into model-backed tasks.
- The docstring is the instruction; the return type is the contract.
- Runs are recorded, so behavior is debuggable after the fact.
```

The output is deterministic because the offline provider replays a recorded
transcript — the same one CI asserts against
(`docs_src/quickstart/test_hello.py`), so this block cannot silently drift
from the code above.

## If it fails

- **Called the task outside the `with` block?** Shepherd refuses to run a
  task with no workspace configured and raises immediately, telling you to
  open one — there is no hidden default model. Move the call inside
  `with shp.workspace(...)`.
- **`shp.DeliveryFailed`?** The response could not be coerced to the declared
  return type. On this example, against the offline provider, that signals a
  broken install — reinstall and rerun.

## Next

One page, two tasks, one composed reviewer:

[Your first Shepherd app →](../tutorials/first-shepherd-app.md)
