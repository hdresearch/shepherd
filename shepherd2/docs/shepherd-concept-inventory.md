# Shepherd Concept Inventory

This note is a shallow pre-migration inventory for the existing `shepherd/`
framework. It is intentionally not a full migration plan. Its purpose is to
name the concepts that are likely to pressure `shepherd2` boundaries and record
their current target ring.

## Ring Placement

| Existing `shepherd/` concept | Current target | Notes |
|---|---|---|
| Effect stream / fold invariant | Ring 1 schemas and projections | The kernel retains records, causality, witnesses, cuts, and slices. Effect kinds become schema libraries, not kernel kinds. |
| Scope | Ring 2 runtime | Scope owns live bindings, execution lifecycle, and ergonomic state. Kernel owner paths can represent retained evidence after the fact. |
| Contexts | Ring 1 schemas plus Ring 2 runtime | Context state and context-specific effects should be represented by schema libraries. Preparation, cleanup, and binding remain runtime work. |
| Materializers | vNext materialize/substrate layer | World escape is not ABI v0. Existing context-level materialization maps to path-explicit vNext orchestration, substrate protocols, and later observe work. |
| Devices | Ring 2 runtime, later capability/device design | Device choice is not ABI v0 witness identity. Future device promotion needs capability semantics before it becomes retained authority. |
| Provider lifecycle | Ring 2 runtime | Provider SDK calls, streaming, parsing, retries, and cleanup are runtime integrations. Retained traces should capture outcomes, not SDK handles. |
| Task and step APIs | Ring 2 runtime plus Ring 1 schemas | Task lifecycle records fit schema libraries. Live task handles and control surfaces stay runtime-only. |
| Combinators | Ring 2 runtime | Retry, parallel, race, gate, and speculation are orchestration constructs. Their retained evidence should be schema-specific records. |
| Persistence/cache | Ring 2 runtime plus backend storage | Cache policy and persistence management are not semantic record identity. Persisted evidence may still be read through kernel cuts. |
| Export/trajectory formats | Ring 1 projections | Export should consume slices/projections. It should not become a separate source of kernel truth. |
| Sandboxes and remote backends | vNext substrate/device work | Treat as substrates or devices after the authority model is explicit. Do not fold remote execution into ABI v0. |
| Transform/grounding APIs | Ring 1 schemas or Ring 2 tools | These can define records and projections, but their live workflows remain outside the kernel. |

## Migration Bias

- Prefer adding schema libraries for retained domain concepts before adding
  kernel primitives.
- Treat runtime handles as live capabilities. Retain their effects and
  relationships, not the handles themselves.
- Keep provider, sandbox, and remote-device APIs behind runtime or vNext
  substrate boundaries until capability semantics are designed.
- Use `shepherd2.kernel` only for canonical identity, retained record shape,
  witnesses, cuts, slices, and operation context.
