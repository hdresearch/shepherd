# shepherd2

Reference implementation for `shepherd.kernel.abi.v0`.

This package contains the trace microkernel plus schema/runtime layers used to
freeze the pre-launch kernel ABI:

- Ring 0 `shepherd2.kernel`: canonical `RecordDraft`, `RecordEnvelope`,
  `RecordBody`, `Cut`, and `Slice`
  vocabulary with temporary `Fact*`, `OwnerCutoff`, and `TraceSlice` aliases
- `OperationContext` plus compatibility `AppendContext` and `ReadContext`
- SQLite-backed atomic append batches over retained records
- content-addressed semantic identity where `record_id == digest`
- retained root and ordinary witness records
- append intent idempotency with conflict detection
- path entries for owner ordinals and storage receipts
- retained `Cut`/`OwnerCutoff` read addresses
- explicit slice mode filters for declarations, captures, or both
- Ring 1 `shepherd2.schemas`: owner-prefix execution projection from trace
  slices, parent-owned execution relation facts, and effective-history
  projection for root executions, active children, and
  parent-published facts
- Ring 2 `shepherd2.runtime`: synchronous `TaskControl.spawn`, `adopt`,
  `abandon`, and `publish`
- vNext `shepherd2.vnext`: minimal substrate registry, deterministic local
  `kv.sqlite.local.v1` substrate, and a path-explicit `materialize`
  orchestrator with witness-stamped dispatch and completed-intent idempotency

It deliberately does not include async scheduling, rich supervision, provider
integration, search, replay, live steering, remote devices, or real capability
attenuation/delegation yet.

Run the current law suite:

```bash
uv run --directory shepherd2 pytest -q
```

See `docs/shepherd-concept-inventory.md` for the current pre-migration mapping
from existing `shepherd/` framework concepts to the `shepherd2` rings.
