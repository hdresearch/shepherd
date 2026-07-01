# `core-reference-v0-lite` Differential Fixture Corpus

Per `260524-post-72-design-pass.md` §"Item B: Fixture Format Prototype".

## Layout

- `positive/NN_name.json` — programs that admit and run to a known
  terminal/suspended/observation-stream outcome.
- `negative/NN_name.json` — programs (or observation streams) that fail
  admission or validation at a specific stage with a known diagnostic.

`NN` is a zero-padded sequence number for stable ordering; `name` is a
short kebab-case label.

## Fixture envelope

```json
{
  "fixture_schema_version": "shepherd_kernel_v3_reference.v0_lite_fixture.v1",
  "profile": "core_reference_v0_lite",
  "case": "NN_name",
  "kind": "<discriminator>",
  "description": "<one-line summary>",
  "covers": ["<feature-tag>", ...],
  "input": { "program": <source-AST DSL>, "observations": [...] },
  "expected": { "envelope_status": "...", "completed_value": ..., "batch": {...} }
            | { "rejection": {...} }
}
```

### `kind` discriminator values

Map 1:1 to the validator call chain:

| kind                            | Stage                                                              |
|---------------------------------|--------------------------------------------------------------------|
| `positive`                      | program admits and runs to expected outcome                        |
| `negative-profile-admission`    | `validate_profile_admission(...)` rejects with rejection_kind set  |
| `negative-kernel-admission`     | `prepare_kernel_program(...)` rejects (structural)                 |
| `negative-runtime-rejection`    | `resume_kernel_replay(...)` rejects after admission                |
| `negative-observation-admission`| `validate_observation_stream(...)` rejects a specific observation  |
| `negative-ref-map`              | `validate_semantic_batch(...)` rejects a malformed CanonicalRefMap |

### `input.program` — source-AST DSL

Each AST node is `{"node": "<NodeName>", ...fields}`. Computations
(`Return` / `Let` / `Perform` / `Handle` / `Resume` / `Abort`) and
expressions (`Lit` / `Var` / `RecordExpr`) are admitted as JSON. Handler
installations (`StaticHandlerInstall` / `DynamicHandlerInstall`) and
publication-experimental forms (`Forward` / `TerminalDelay` /
`TerminalFork`) are admitted as construction inputs so negative
fixtures can exercise rejection paths.

Examples:

```json
{"node": "Return", "expr": {"node": "Lit", "value": 42}}
{"node": "Let", "name": "x", "bound": {...}, "body": {...}}
{"node": "Perform", "effect_kind": "ask", "payload": {"node": "Lit", "value": null}}
{"node": "Handle", "body": {...}, "handler_env": {"bindings": [
    {"node": "StaticHandlerInstall", "effect_kind": "ask", "handler_id": "ask.v1",
     "handled_result_schema": {"node": "IntSchema"}, "payload_name": "_p",
     "body": {...}}]}}
```

### `expected` (positive)

- `envelope_status` — the run outcome (`completed` / `external-effect-request`).
- `completed_value` — the terminal value, when `envelope_status == "completed"`.
- `batch` — the canonical wire encoding (`wire.semantic_batch_to_wire`) of the
  fixture's **initial-run-prefix** transition, regenerated from the canonical
  Python implementation at fixture-authoring time and stored verbatim (same
  regenerate-and-commit pattern as `golden_traces.json` from #69b). Byte-stability
  is enforced by `test_corpus.test_positive_fixture_batch_is_byte_stable` (compared
  via `kernel.refs.canonical_json`, so the stored object may be pretty-printed).
  Regenerate with `uv run python tests/conformance/v0_lite/regenerate.py`.

Scope: `batch` freezes the initial-run-prefix projection only. The projection
function (`semantic_batch_from_transition`) currently supports just that
transition kind; projecting a *resume* transition (`callable_resume` /
`unhandled_top_level_resume`) raises "non-initial transitions require an
AdmissionBasis", so per-stream batch *sequences* for multi-step fixtures
(`04`/`05`/`07`) are not yet frozen. Closing that needs the projection function
to thread an `AdmissionBasis` for resume transitions — a projection-layer
follow-on, not a corpus shortcut (see §"Coverage status").

### `expected.rejection` (negative)

```json
{
  "rejection_kind": "profile-admission" | "kernel-admission" | ...,
  "construct": "RecordExpr",
  "message_substring": "not admitted by core-reference-v0-lite",
  "source_location": {"construct_path": "..."}  // optional
}
```

The loader matches `construct` exactly and asserts `message_substring`
appears in the diagnostic. `source_location` is checked when present.

## Lean Phase 9 contract

The same JSON files are consumed by both Python (regenerates
`expected.batch` from Python's executor) and Lean (regenerates from
Lean's executor). Byte agreement — `canonical_json(produced) ==
canonical_json(expected.batch)` — between the stored batch and Lean's
output is the differential gate. The stored `batch` is the
Lean-consumable artifact; the Lean-side differential *runner* that
re-generates and compares is Phase 8 work.

## Coverage status and deliberate omissions

This corpus tracks the Phase 6 fixture enumeration in
`260521-0600-kernel.md`, with the following deliberate scoping
decisions recorded so the gaps read as intentional, not forgotten.

### Frozen artifact: initial-transition `batch` (not full per-stream `WireResult`)

Positive fixtures freeze `expected.batch` — the canonical wire encoding of
the **initial-run-prefix** transition — and assert it byte-for-byte. That is
the Python-side shape-lock and the Lean Phase 9 differential input. It is a
real frozen artifact (regenerate-and-commit), not the dynamic-only guard the
earlier corpus carried.

Two scoped gaps remain, both genuine layer limitations rather than corpus
shortcuts:

1. **Resume-transition batches.** Multi-step fixtures (`04`/`05`/`07`) freeze
   only their initial-run-prefix batch. Freezing each resume transition's
   batch is blocked on `semantic_batch_from_transition` supporting non-initial
   transitions: today it raises "non-initial transitions require an
   AdmissionBasis" for `callable_resume` / `unhandled_top_level_resume`. That
   AdmissionBasis threading is a projection-function follow-on; once it lands,
   `regenerate.py` can extend `expected.batch` to an `expected.batches` list.
2. **Lean differential runner.** The stored `batch` is the Lean-consumable
   artifact, but the Lean-side runner that re-generates from Lean's executor
   and compares is Phase 8. Until then, the `tests/test_projection_corpus.py`
   70+ operational corpus carries the dynamic projection/`CanonicalRefMap`
   round-trip guarantee, and the frozen `batch` carries the static
   cross-code-change shape-lock.

### Duplicate-idempotent observation: subsumed by one-shot under `-lite`

Phase 6 named a distinct "duplicate idempotent observation" negative
fixture. Under `core-reference-v0-lite` it is subsumed by
`negative/06_one_shot_violation.json`: per the 2026-05-24 §"Post-#72
design pass" item F resolution, idempotency is implicit in
`state.consumed_source_keys` — the same `source_key` consumed twice IS
the duplicate, and there is no separate idempotency table. A distinct
duplicate-idempotent fixture only becomes meaningful in a future profile
that adds non-source-keyed retry semantics; it is intentionally absent
here.

### Reserved negative discriminators with no fixtures yet

Three discriminators are declared in `VALID_KINDS` but intentionally have
no fixtures and no runner path yet:

- `negative-kernel-admission` — structural rejection by `prepare_kernel_program`
  (cycles, missing refs). Reserved; structural admission is already covered by
  the package's `tests/kernel/` admission suite, so a corpus fixture is
  low-marginal-value until Lean models structural admission.
- `negative-runtime-rejection` — post-admission deterministic failure surfaced
  as `execution-failure`. Reserved; depends on the resume-transition path that
  the §"Frozen artifact" scope note defers.
- `negative-ref-map` — a malformed `CanonicalRefMap`. Deferred to Phase 8 for
  two reasons:

1. A malformed `CanonicalRefMap` is not produced by a source *program* —
   it is a corrupted projection output. The `input.program` DSL produces
   programs; this fixture needs a batch-input shape the format does not
   yet carry. That format extension naturally lands with the Phase 8
   `WireResult` upgrade.
2. `validate_semantic_batch`'s coverage / tightness / determinism /
   round-trip obligations are already exercised dynamically by the 70+
   operational corpus in `tests/test_projection_corpus.py`. The static
   `negative-ref-map` fixture's marginal value is Lean Phase 9
   differential coverage, not validator coverage.
