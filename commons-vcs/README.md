# commons-vcs

`commons-vcs` is the shared plumbing kernel for content-addressed graph
recording.

It owns:

- canonical object identity;
- typed edges;
- object storage;
- refs and compare-and-swap ref updates;
- inverse citation lookup;
- profile dispatch;
- focused graph verification.

It does not own product semantics. Filesystem capture, workspaces, LLM tools,
sessions, PRs, governance policy, materialization, and user workflows belong in
the consuming projects.

The package is internal 0.x infrastructure. Profiles own schema meaning and
validation policy; the kernel only stores and walks typed graph facts.

For the cross-project convergence contract, including the authority boundary
between `commons-vcs`, Shepherd, vcs-core, and SGC, see
[`docs/convergence-kernel-contract.md`](../docs/convergence-kernel-contract.md).

## Public API

- `Object`, `Edge`
- `Repo`, `Profile`, `Resolver`
- `Failure`, `FailureRecord`, `VerifyResult`
- `canonical_bytes`, `canonical_value_from_bytes`, `digest`, `CANONICAL_PREFIX`
- `Backend`, `MemoryBackend`
- `GitBackend` from `commons_vcs.backends.git`

`GitBackend` is imported from `commons_vcs.backends.git` so memory-only users do
not import `pygit2`.

## Canonical Identity Contract

Every shared object identity is derived from `commons.canonical.v1` bytes:

- payloads are UTF-8 JSON with lexicographically sorted object keys, compact
  separators, `ensure_ascii=True`, and the mandatory
  `commons.canonical.v1\n` prefix;
- digests are SHA-256 strings formatted as `sha256:<lowercase-hex>`;
- floats, tuples, sets, bytes, dataclasses, duplicate object keys, and
  non-string object keys are outside the canonical input boundary;
- domain projects must project Python-specific values into schema-declared JSON
  primitives before constructing `Object` values;
- `canonical_value_from_bytes(...)` is the public strict decoder for stored
  canonical bytes and rejects non-prefix, non-canonical, or duplicate-key JSON.

Backends store byte-exact canonical object payloads. A stored blob that is only
JSON-equivalent to the canonical payload is corrupt state and must be rejected
on read, even if it would decode to an object with the requested digest.

The normative long-form encoding design currently lives in
`spikes/260426-convergence/encoding-spec.md`; this package section is the public
runtime contract consumed by the workspace packages.

`GitBackend` also exposes explicit Git object pins under
`refs/commons-vcs/pins/`. Pins are caller-managed retention roots: domain
coordinators decide which Git objects to pin, and the backend does not infer
pinning behavior from schema names or body fields.
