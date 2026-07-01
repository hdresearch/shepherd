# Slice Semantics

## Status

Draft for ABI v0 closure. This document specifies the target read artifact
returned by kernel reads: `Slice`. Where the current `shepherd2` reference
implementation still carries compatibility names or partial behavior, this
document calls that out explicitly.

## Core Claim

A `Slice` is a visibility-filtered read artifact containing selected records plus
the support evidence needed to interpret those records.

Support evidence is not itself selected domain history. It exists so readers can
understand why visible records exist, what evidence was hidden, and where the
slice boundary lies.

## Terms

**Traversal records**

The records reached by the read selector before `mode_filter` is applied.

For an owner-prefix cut, this is the owner path prefix. For a causal-closure
read, this is the transitive causal closure of the requested root records.

**Selected records**

The traversal records that survive `mode_filter`.

For normal client reads, selected records are domain records. Witness records can
also be selected records if a read explicitly targets backend-internal witness
paths, but they are selected because the read selector reached them, not because
support closure promoted them.

`mode_filter` applies to traversal records. It does not remove witness records
added later as support evidence.

**Visible records**

Selected records after visibility filtering.

With payload visibility, visible records include payloads. With shape-only
visibility, visible records include shape but not payload.

**Support evidence**

Evidence needed to interpret selected records:

- witness records for selected records, when those witnesses are added as
  support rather than reached directly by the read selector
- witness chains from those support witnesses to the root witness
- external anchors for direct causal parents outside the selected set, when
  requested
- witness anchors when witness payloads are hidden

**Compatibility diagnostics**

Backend-specific operational data that may help debug or migrate an
implementation, but is not semantic slice content. In the SQLite reference
backend this includes the older retained-context vocabulary exposed through
`contexts_by_id` and `context_anchors`.

**Anchors**

An anchor is a visible placeholder for evidence that is known to exist but is not
included as a visible payload record in this slice.

Anchors preserve boundary references without disclosing more than the visibility
policy allows. They do not synthesize hidden structure, compress causal paths, or
create transitive edges.

## Slice Shape

Normalized ABI-facing vocabulary:

```text
Slice {
  read_address?
  visibility_profile
  mode_filter

  visible_records_by_id
  visible_witnesses_by_id

  owner_paths
  causal_edges

  external_anchors
  witness_anchors

  compatibility_contexts?
  compatibility_context_anchors?
}
```

The current `shepherd2` reference implementation still carries some
compatibility names while the vocabulary settles:

| Normalized role | Current reference field |
|---|---|
| `visible_records_by_id` | `facts_by_id`; also exposed as `visible_facts_by_id` |
| `visible_witnesses_by_id` | `visible_witnesses_by_id` |
| `compatibility_contexts` | `contexts_by_id` |
| `compatibility_context_anchors` | `context_anchors` |

The normalized roles are the semantic ABI vocabulary. The compatibility fields
exist so older schema/runtime code can continue to project from SQLite-backed
slices during migration.

`visible_records_by_id` contains selected records. For ordinary client reads,
these are non-support domain records. Backend diagnostic reads may select
witness records directly; in that case those witness records appear here in
their selected-record role.

`visible_witnesses_by_id` contains retained witness records needed to support
selected records. Witness support is closed to the root witness: if a visible
record cites witness `W`, and `W` cites root witness `R`, then the slice includes
or anchors both `W` and `R`, subject to visibility.

`visible_records_by_id` and `visible_witnesses_by_id` are role maps, not
disjoint type partitions. A retained witness record is a `Record`. If a
backend-internal read selects a witness record directly and that same record is
also needed as support evidence, it appears in both maps: once in its
selected-record role and once in its support-evidence role. Ordinary client
reads should not depend on selecting the witness owner path directly.

`causal_edges` contains direct causal edges where both endpoints are selected
records. The kernel does not synthesize transitive edges when an intermediate
record is filtered out.

`external_anchors` represent direct causal parents of selected records that are
outside the selected set or filtered out by `mode_filter`, when the closure
policy requests anchors. They do not recursively describe every omitted ancestor
behind that parent.

`witness_anchors` represent witness records whose existence may be disclosed but
whose payload is hidden by visibility. Shape-only witness anchors expose
envelope-level witness shape, including the parent `witness_ref`, so the witness
chain to root remains inspectable without exposing witness body or retained
context payload.

`compatibility_contexts` and `compatibility_context_anchors` are SQLite
reference-backend diagnostics for the older retained-context vocabulary. They are
not semantic record identity and are not required for future backends.

## Read Pipeline

A read proceeds in this order:

1. Resolve the cut or causal-closure request.
2. Compute traversal records.
3. Apply `mode_filter` to traversal records, producing selected records.
4. Compute causal edges among selected records.
5. Emit causal anchors for direct omitted parents if the closure policy requests
   them.
6. Compute witness support closure for selected records.
7. Apply visibility to selected records and witness support.
8. Attach compatibility diagnostics, if the backend exposes them.

## Witness Closure

Witness support is computed independently from ordinary record selection:

1. Start with each selected record's non-empty `witness_ref`.
2. Resolve that reference to a retained witness record.
3. Add the witness record to the support set.
4. If the witness record has a non-empty `witness_ref`, repeat from step 2.
5. Stop at the root witness, whose `witness_ref` is the empty sentinel.
6. Dedupe witness support by retained witness record id.

The root witness is support evidence, not selected domain history. If a selected
record's ordinary witness cites the root witness, the slice must include or
anchor the root witness under the same visibility rules as other witness
support.

Malformed witness chains are invalid retained evidence. This includes missing
witness records, witness refs that resolve to non-witness records, non-root
witnesses with empty `witness_ref`, root witnesses with non-empty `witness_ref`,
and cycles before the root witness. Strict reads may fail; lenient diagnostic
reads may expose an anchor or invalid-evidence marker, but they must not silently
drop or truncate malformed witness support.

### Reference Implementation Status

The current `shepherd2` SQLite implementation closes witness support to the root
witness. Law tests assert that payload-visible slices include retained witness
records through the root witness, and shape-only slices expose corresponding
witness anchors.

## Mode Filter

`mode_filter` has three values:

```text
both
captures_only
declarations_only
```

The filter applies to traversal records. Witness records added as support
evidence are included or anchored regardless of their own mode.

A `captures_only` slice may contain support witness records whose mode is
`capture`. Those support witness records do not make the slice a mixed-mode
selected-record slice. If a read explicitly selects witness records as traversal
records, then those selected witness records are subject to the same
`mode_filter` rules as other traversal records.

## Closure Policy

ABI v0 supports:

```text
include_external_anchors
visible_only
```

These names control anchor emission, not traversal.

Both policies compute the same traversal set. For causal-closure reads, the
difference is what happens after `mode_filter` omits causal parents:

- `include_external_anchors` emits anchors for omitted causal parents.
- `visible_only` omits those anchors.

`visible_only` may therefore produce a graph where selected records are
disconnected because an intermediate causal parent was filtered out. The kernel
does not repair this by adding synthetic edges.

Unknown closure policies are rejected.

### Policy Scope In ABI v0

In the current `shepherd2` reference API, `closure_policy` is exposed on
`read_causal_closure(...)`.

Owner-prefix reads and published-cut resolution are path-prefix reads. They do
not currently accept a `closure_policy` argument; the SQLite reference backend
emits external anchors for causal parents outside the selected owner-prefix
slice when visibility permits. That is a reference-backend behavior, not a
parameterized ABI v0 read option. Generalizing `closure_policy` to every read
shape is a possible ABI v1/vNext cleanup, but ABI v0 should not overclaim that
surface.

## Visibility

Visibility applies separately to selected records and support evidence.

With `payload` visibility:

- selected records appear in `visible_records_by_id`
- witness support records appear in `visible_witnesses_by_id`

With `shape_only` visibility:

- selected record payloads are hidden
- witness payloads are hidden
- witness existence appears through `witness_anchors`

Shape-only selected records still appear as record shapes in
`visible_records_by_id`; witness support moves to `witness_anchors` because
witnesses are support evidence rather than selected domain history. This keeps
the selected-record graph separate from the provenance evidence that explains it.
If a witness record is directly selected by a backend-internal read, it still
appears as a selected record shape in `visible_records_by_id`; any support role
for that same witness is represented separately by `witness_anchors`.

With `full_internal` visibility:

- payloads and internal diagnostics may be visible
- this requires appropriate read authority

## Laws

These laws describe the target ABI behavior unless marked as current-reference
compatibility.

1. A slice is read-only. It creates no records and grants no authority.
2. A slice has an explicit `mode_filter`.
3. `mode_filter` applies to traversal records, not witness support.
4. Witness support is closed to the root witness.
5. Causal edges are direct edges between selected records only.
6. The kernel does not synthesize transitive causal edges.
7. Anchors preserve omitted or hidden evidence when policy permits.
8. Compatibility context fields are not semantic identity.
9. Support evidence does not affect `owner_paths`, selected-record ids,
   projection mode compatibility, or selected-record causal edges.
10. Projection may consume a slice but must not mutate it.
11. Same retained state plus same read request yields the same slice.

## Cross-References

- `kernel-abi-v0.md` defines the frozen ABI surface that this document refines.
- `../tests/test_kernel_abi_v0_laws.py` is the executable law suite for the
  current reference implementation.
