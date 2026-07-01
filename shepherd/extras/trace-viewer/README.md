# shepherd-trace-viewer

A read-only local webapp for durable Shepherd task-trace revisions and projected
`shepherd2.TraceStore` slices.

The viewer targets two read-only sources:

- provider-neutral durable trace revisions produced by
  `shepherd_dialect.trace.build_run_trace_revision(...)` and selected through
  VcsCore's `TaskTraceSubstrateDriver`
- `shepherd2.TraceSlice` projections resolved from a local SQLite TraceStore by
  cut, owner prefix, or causal closure

- `trace_runtime`
- `trace_owner_id`
- `frontier_id`
- `run_ref`
- `identity_domain`
- `events[]`
- `causal_edges[]`
- `owner_paths`

The old VcsCore commit-DAG, run-index, and JSONL stream readers were removed.
The UI renders a typed event graph: event nodes, owner lanes, causal edges,
pointer records, and run summary metadata.

## Quick Start

```bash
# Render a raw durable trace revision payload:
shepherd-trace-viewer serve --trace-payload tests/fixtures/durable-basic.trace.json --open

# Render a selected trace revision from a VcsCore workspace:
shepherd-trace-viewer serve --trace-rev <revision-head> --workspace /path/to/workspace --open

# Render the trace head currently selected on a VcsCore workspace:
shepherd-trace-viewer serve --trace-head --workspace /path/to/workspace --open

# Render a published TraceStore cut from a local SQLite store:
shepherd-trace-viewer serve --trace-store trace.sqlite --cut frontier:child --open

# Render an owner path prefix:
shepherd-trace-viewer serve --trace-store trace.sqlite --owner exec:child --through 42 --open

# Render a causal closure:
shepherd-trace-viewer serve --trace-store trace.sqlite --causal-root sha256:... --open

# Read TraceStore shape-only data without payloads:
shepherd-trace-viewer serve --trace-store trace.sqlite --cut frontier:child --visibility shape_only
```

`--trace-rev` and `--trace-head` use the public
`VcsCore.read_trace_revision(...)` path with `TaskTraceSubstrateDriver`.
`--trace-payload` accepts either a raw durable trace revision payload or an
already-built `shepherd.trace-view.v3` JSON document. Legacy
`shepherd.trace-view.v2` JSON is upgraded to v3 when loaded.

TraceStore reads support:

- `--visibility payload | shape_only | full_internal`
- `--mode both | declarations_only | captures_only`
- `--actor <actor-ref>`
- `--trusted-internal` for `full_internal` reads

The server binds to `127.0.0.1` by default. Binding to another interface prints a
warning because trace event payloads can contain task ids, file paths, world
head pointers, and record identity metadata.

TraceStore record ids are content-addressed and can appear more than once in a
slice. The v3 ViewModel uses owner-path occurrence ids for record nodes so the
browser can render repeated records without conflating their path positions.
