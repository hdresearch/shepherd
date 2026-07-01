# Install

> Page status: scaffold
> Source state: scaffold
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: not yet validated

*Quickstart — the fastest working run. To learn the concepts in order, see the tutorial; for exact APIs, see the reference.*

!!! warning "Scaffold — not yet runnable"
    This page is a draft against a surface that has not shipped. Treat commands and code as illustrative until the page is promoted.

Everything on this page is the **planned install surface** — none of these
distributions is on PyPI yet, and the
[source-state inventory](../reference/source-state.md) tracks when that
changes. What you can run today is the [quickstart](quickstart.md), whose
examples execute against the recorded offline provider.

## Requirements — unshipped

- Python **3.11+**.
- No provider credentials for the offline path. One live provider key (for
  example `ANTHROPIC_API_KEY=<your-key>`) only when you opt into live runs.

## Pick a distribution — unshipped

| You want | Install | What you get |
|---|---|---|
| The product (tutorial path) | `pip install shepherd-ai` | The `shepherd` import package, the `shepherd` CLI, the local run path, the provider registry, the deterministic offline provider, and one live provider path. |
| Slim / audit install | `pip install shepherd-base` | Facade, CLI, slim runtime, minimal local placement support, and the offline provider — no live-provider SDK. |
| First-party workflows | `pip install "shepherd-ai[authoring]"` | Adds packaged workflow plugins on top of the product. |

The tutorial and quickstart path is always `shepherd-ai`, never
`shepherd-base`. `shepherd-ai` is the *distribution* name; the *import* is:

```python
import shepherd as shp
```

## Verify the install — unshipped

```bash
shepherd doctor
```

`doctor` reports installed packages, configured providers, available
placements, workflow readiness, and capability gaps — each gap with a fix
command.

```bash
shepherd demo --offline
```

`demo --offline` runs the packaged first-run demo against the deterministic
provider: no credentials, no network, the same output every time.

## Next

- [Quickstart](quickstart.md) — the path that works today.
- [Your first Shepherd app](../tutorials/first-shepherd-app.md) — the
  tutorial.
