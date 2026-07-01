# Kernel ABI v0

`shepherd2` is the reference implementation for `shepherd.kernel.abi.v0`.
The ABI is intentionally narrow: it freezes retained record identity, witness
identity, cut-shaped reads, slice visibility, and projection purity. It does
not freeze materialization, observation, devices, substrate execution, provider
integration, async scheduling, live steering, replay, or durable migration from
older prototype SQLite files.

The package now also contains a small vNext `materialize` spike. That code is
intentionally outside the ABI v0 freeze: it is a vNext orchestrator over the
Ring 0 store, exercises witness-stamped substrate dispatch and
declaration-to-capture append, and is not part of the frozen
`shepherd.kernel.abi.v0` contract. Materialize targets are owner-path explicit;
bare record ids are not enough to select where emitted captures belong.

## Closure Slice

The 2026-04-30 closure slice is focused on tightening the frozen ABI before
larger framework work resumes. The current baseline command is:

```text
uv run --directory shepherd2 pytest -q
```

That baseline passed before closure edits began. This slice may change
`shepherd2` internals, tests, and docs, but keeps ABI v0 narrow: no async
scheduling, provider integrations, remote devices, multiple real substrates,
cross-machine capability transit, durable migration of old prototype SQLite
files, vcs-core replication, or counterfactual replay.

A shallow read of the existing `shepherd/` framework names the concepts most
likely to pressure the package split: effect streams, scopes, context
materialization, devices, provider execution lifecycle, task combinators,
persistence/cache state, and trajectory export. These remain Ring 1 or Ring 2
concerns unless a later design explicitly promotes a narrower record schema or
runtime protocol. They are not kernel primitives.

## Canonical Version

The ABI version is `shepherd.kernel.abi.v0`.

The byte-level canonicalization version remains
`shepherd.kernel.canonical.v2`. Digest inputs are encoded as canonical JSON with
sorted keys, compact separators, UTF-8 output, and no NaN values. The digest is
SHA-256 over:

```text
shepherd.kernel.canonical.v2\n<canonical-json-bytes>
```

## Records

A retained record is identified by its canonical digest. For ABI v0,
`record_id == digest`. The SQLite reference backend stores semantic records
separately from path entries, so the same record may appear at multiple owner
paths or ordinals without changing identity.

Canonical record input has this shape:

```json
{
  "kind": "record",
  "schema_ref": "schema.name.v1",
  "mode": "capture",
  "body": {},
  "caused_by": [],
  "witness": "sha256:..."
}
```

`schema_ref` is mandatory. `mode` is mandatory and is one of `capture` or
`declaration`. `caused_by` is ordered; changing parent order changes record
identity. `witness` is part of record identity.

Operational storage data is not part of semantic record identity. Append
intents, commit receipts, path positions, owner ordinals, retained-context
diagnostics, and local append references are retained outside the canonical
record digest.

## Witnesses

Witnesses describe the retained authority and environment under which records
were accepted. Witnesses are records in the store, but their witness identity is
also comparable through canonical witness-body input:

```json
{
  "kind": "witness",
  "schema_ref": "kernel.witness.v1",
  "body": {
    "actor_ref": "runtime:test",
    "authority_refs": [],
    "active_binding_refs": [],
    "semantic_environment_refs": [],
    "visibility_policy_refs": [],
    "provenance_policy_refs": [],
    "substrate_ref": "sqlite.local.v1",
    "containment": "contained"
  }
}
```

The root witness record uses schema `kernel.witness.root.v1`, body
`root_witness_body()`, and the empty witness-ref sentinel. The empty sentinel is
legal only for the root witness record. Ordinary retained records must carry a
non-empty witness reference that resolves to a retained witness record.
Witness-body digests are useful fixtures for comparing witness bodies, but they
are not witness references. Ordinary records cite the retained witness record id.

Unknown `substrate_ref` values may be retained if the witness body is otherwise
well formed. Future materialization would fail without a registered substrate.
`device_ref` and `binding_policy_ref` are not part of v0 witness identity.
Append and preview both validate the ABI witness-body shape before retaining
records: `substrate_ref` must be non-empty, and `containment` must be one of
`full`, `contained`, `buffered`, or `uncontained`.

## Cuts And Slices

A cut is an immutable read address over retained path entries. Published cuts
are created through append, and later records do not mutate a resolved cut.

A slice is a graph-shaped read result. It carries the selected visible records,
owner paths, causal edges, external anchors for out-of-slice or hidden parents,
visible witness records, and witness anchors when visibility hides supporting
evidence. The current SQLite reference backend also exposes `contexts_by_id`
and `context_anchors` as compatibility diagnostics for the older retained
context vocabulary; those fields are not semantic record identity.

Mode filtering is part of slice semantics. The default is `both`, meaning
captures and declarations are visible when allowed by the visibility profile.
`captures_only` and `declarations_only` are explicit filters.

Causal-closure reads support the ABI closure policies
`include_external_anchors` and `visible_only`. Unknown closure policies are
rejected. Causal closure is computed before `mode_filter` is applied; the mode
filter controls which retained records appear in the slice, and the closure
policy controls whether filtered or out-of-slice parents appear as anchors.
Read options are keyword-only on the reference API to avoid confusing
visibility with mode filtering.

See `slice-semantics.md` for the target Slice contract guiding the ABI v0
closure work.

## Operation Context

Kernel operations accept `OperationContext` with an explicit operation:
`append`, `read`, or `publish_cut`. Compatibility `AppendContext` and
`ReadContext` wrappers remain available during the migration. Operation
authority is operation-specific: a read context does not authorize append, and
an append operation context does not authorize cut publication.

`materialize` currently uses `OperationContext(operation="materialize")`, but
that operation remains vNext. The ABI v0 `TraceStore` surface stays limited to
append, read, and cut publication/resolution.

## Compatibility Names

The prototype `Fact*`, `OwnerCutoff`, and `TraceSlice` names remain as
compatibility aliases while the implementation moves to `Record*`, `Cut`, and
`Slice`. The canonical ABI names are the record/cut/slice vocabulary.
