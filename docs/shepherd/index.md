---
hide:
  - navigation
  - toc
---

<!--
Page-metadata block — kept in an HTML comment so the membership gate
(scripts/check_shepherd_docs.py) still reads the `> Key: value` lines while the
landing renders without a visible status banner.
> Page status: release-ready
> Source state: shipped-source
> Applies to: Shepherd v1.0-dev
> Owner: @docs-system-owner (TBD)
> Validation: scripts/check_shepherd_docs.py
-->

<div class="shp-hero" markdown>

<p class="shp-eyebrow">Python agent framework</p>

# Build agent systems in Python

You write ordinary typed functions; Shepherd runs them against a model,
validates the results, records what happened, and lets you supervise and
compose the runs.

[Get started](tutorials/first-shepherd-app.md){ .md-button .md-button--primary }
[Quickstart](start/quickstart.md){ .md-button }
[Concepts](concepts/tasks.md){ .md-button }

</div>

```python title="hello.py"
--8<-- "quickstart/hello.py:hello"
```

## Find your path

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Build your first agent**

    ---

    A typed task, a workspace, and a small working reviewer — offline and
    deterministic, in one sitting.

    [:octicons-arrow-right-24: First Shepherd app](tutorials/first-shepherd-app.md)

-   :material-cog-play:{ .lg .middle } **Run a packaged workflow**

    ---

    For operators: install, configure credentials, run first-party workflows
    in CI. *Ships with the Shepherd CLI.*

    [:octicons-arrow-right-24: What operators can read today](workflows/index.md)

-   :material-lightbulb-on:{ .lg .middle } **Understand & evaluate**

    ---

    The mental model — tasks, effects, runs — and the ledger of exactly what
    these docs may claim today.

    [:octicons-arrow-right-24: Concepts: Tasks](concepts/tasks.md)

</div>

## Why Shepherd

- **Typed by construction.** A task is a function with a signature and a
  docstring; the return type is the contract the model must satisfy.
- **Observable.** Every run records what was sent and returned, so debugging is
  reading a trace, not guessing.
- **Composable.** Tasks are values — pass them, supervise them, and build larger
  programs out of small ones.

!!! info "Shepherd v1.0-dev — prototype docs"
    This site is built scaffold-first: a page is published only when its
    content is backed by checked source, and everything else stays in the
    reviewer build. The [source-state inventory](reference/source-state.md) is
    the honest ledger of what these docs may claim today.
