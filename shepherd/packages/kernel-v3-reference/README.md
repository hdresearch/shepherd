# shepherd-kernel-v3-reference

Reference implementation for the shepherd-kernel-v3 core calculus.
See `../../design/shepherd-kernel-v3/` for the normative semantics.

This package implements the single-root callable-resumption core plus a
quarantined publication-control spike across these executable components:

- source AST (`Return` / `Let` / `Perform` / `Handle`, record expressions, plus
  the Core-A handler-body forms `Resume` and `Abort`; publication-experimental
  controls are available only through `shepherd_kernel_v3_reference.source.experimental`);
- a generator-based direct evaluator with deep handling, where a paused
  generator is the captured continuation up to the matched delimiter;
- a defunctionalized kernel IR where source `Let` lowers to
  `KBind(binder_id, binder_env_ref)` and handler `Resume` lowers to
  `KResumeWith`;
- a small abstract machine with explicit bind, handler, handler-return, and
  resume-return frames;
- normalized trace records for declaration, selection, resumption, resume,
  resume-return, capture, selection closure, forwarding, pending delay, fork
  sources, and terminal resume results;
- durable continuation-image catalogs for content-addressed continuation refs;
- explicit execution-context refs for worker entry, handler entry, and selected
  outer continuation restoration;
- runtime schema validation at the three §10 boundaries: perform payload,
  resume value, and handler answer.

## Executable Boundaries

`shepherd_kernel_v3_reference` intentionally names two boundaries while the Lean proof catches up:

- **Core-0** is the ordinary answer-producing handler fragment: a selected
  handler creates a resumption handle and either answers directly or invokes the
  handle once, receives a `ResumeReturn`, and then completes with
  `EffectCapture(return, completed)`.
- **Core-A** extends Core-0 with answer-position `Abort` and `SelectionClosed`
  records for selected paths skipped or abandoned by a dynamically nested handler
  answer.
- **Publication experimental** extends the executable kernel path with
  answer-position `Forward`, `TerminalDelay`, and `TerminalFork`. These forms
  must be imported from `shepherd_kernel_v3_reference.source.experimental`; they are emitted and
  serialized for spike tests, but the Core-0/Core-A validators intentionally
  remain scoped to their proof boundaries.

The public `validate_core_trace(...)` and `validate_core_trace_prefix(...)`
functions validate the current executable core, which is Core-A. Use
`validate_core0_trace(...)` / `validate_core0_trace_prefix(...)` when a test or
paper-facing artifact should stay inside the narrower ordinary-answer boundary.

## Validation Layers

`validate_generated_trace_against_program(program, trace, ...)` adds a stronger
executable reference check for the deterministic static fragment: it
lifecycle-validates the trace, reruns the current kernel/trace machine for the
supplied `KernelProgram`, and requires the stored records to match exactly. It is
an executable-oracle check, not yet an independent semantic verifier. It is
intentionally scoped to the initial `run_trace(...)` execution; traces extended
by later external continuation applications need an explicit replay input stream.
Trace records cite a `program_ref`, and program/continuation fingerprints include
the content of referenced binder definitions, handler definitions, and schema
definitions rather than only local fresh ids, so two programs with different code
or typing boundaries do not validate as the same trace merely because elaboration
chose the same local names.

## Lowerability Boundary

`kernel-v3-reference` validates explicit static/reference programs and the
deterministic traces generated from those programs. A trace is
reference-validatable here only when the caller supplies a `KernelProgram`
produced from the auditable static fragment, and the trace matches the current
`run_trace(...)` execution for that program exactly.

That boundary is intentionally narrower than the Python runtime. Arbitrary
Python task bodies, `DynamicHandlerInstall` builders, provider SDK calls, host
exceptions, opaque host objects, and vcs-core carrier envelopes are runtime
evidence unless a future lowerer maps them into an explicit static/reference
program with a stronger claim. Runtime-normalized traces may still be useful
lifecycle evidence, but they are not proof-backed or reference-validatable by
this package merely because their records are well formed.

Completed Core-A trace validation requires every selected path to terminate with
`EffectCapture` or `SelectionClosed`. Prefix validation admits open paths for
suspended executions. Continuation refs are content addressed over the complete
deterministic `ContinuationImage` payload; opaque Python objects are rejected
rather than folded into refs via process-local `repr(...)` strings, and image
payloads are immutable once their refs are computed. Semantic transition batches
validate embedded continuation images for shape, content-addressed ref
agreement, program identity, and coverage of continuation-image refs cited by
their records. Handler selection records also carry role-independent
`continuation-control:*` refs for dynamic ancestry checks, because restart image
identity intentionally commits to role and top-level execution context while
ancestry does not.

## Claim Hierarchy

The artifact separates several strengths of claim:

- source execution checks the ordinary direct evaluator behavior;
- kernel execution checks the defunctionalized abstract machine against the
  static source fragment;
- lifecycle validation checks whether a trace has well-formed Core-0/Core-A
  declaration, selection, resumption, resume-return, capture, and closure
  structure;
- generated-trace validation checks exact agreement with the current executable
  kernel for a deterministic `KernelProgram`;
- proof-level semantic adequacy remains the job of the Lean development.

The validation stack is intentionally layered:

- kernel preflight validation checks structural well-formedness of `KernelProgram`
  refs before execution: binders, handler environments, installs, and schemas;
- lifecycle validation checks declaration, selection, resumption, resume,
  resume-return, capture, and closure order;
- context validation checks worker, handler, and outer-continuation restoration
  refs on those lifecycle records;
- closure validation requires a terminal capture on the selected path that
  claims to close an abandoned/skipped path, and checks that the closing
  selection is dynamically nested under the closed selection using
  role-independent continuation-control refs plus active resume paths;
- generated-trace validation checks exact agreement with the current executable
  kernel for the deterministic static fragment;
- independent program-semantics validation is future work.
Publication-experimental traces use the same private lifecycle ledger for common
declaration, selection, path, callable-resume, capture, and closure facts. The
currently implemented Forward, terminal Delay, and terminal Fork controls add
profile-gated ledger obligations: selected handler paths and later resumable
source paths are separate resources; forwards, delays, terminal pending/fork
source use, branch materialization, explicit branch-scope identities,
branch-local callable resumes, and Core-A-style selection closures are checked
independently. Publication fork branches carry a `branch_scope_ref` lifecycle
identity, currently the fork-branch terminal resume ref, so nested branches can
reuse visible branch labels without conflating their obligations.
Pending terminal sources must record their `ContinuationDelay` before external
resume in both prefix and completed validation.
`Forward` is the only publication transition that admits a later handler
selection for the same declaration. Completed terminal forks are currently flat:
each branch must complete directly enough to emit its own `TerminalResumeResult`;
nested terminal outcome aggregation remains future work. Generated-trace
validation dispatches by program profile. Replay input streams for traces
extended after external pending resume remain future work.

## Proof-Facing Artifact Surface

The proof-facing executable surface is deliberately small and fixture-backed.
`tests/fixtures/golden_traces.json` stores deterministic JSON traces for the
current core corpus:

- Core-0 completed cases: pure let, handled return, one callable resume, deep
  handling, and handler-side outward perform;
- Core-0 prefix case: unhandled suspension;
- Core-A extension cases: answer abort, outer return closing an inner selection,
  and outer abort closing an inner selection.

The fixture test rebuilds each source term, elaborates it, runs the direct
evaluator, runs the kernel, regenerates the trace and continuation-image
catalog, validates the declared boundary, and requires byte-for-byte-equivalent
JSON record and image data. It also rebuilds an initial semantic transition
batch from the fixture, so trace/image coverage drift is explicit. These
fixtures are evidence for executable agreement and reviewer inspection; the Lean
development remains the authority for proof-level adequacy claims.
Publication-control fixtures are the next reference-artifact step now that
publication-experimental lifecycle validation exists, but they should remain
clearly profile-gated.

## Execution Contexts

The kernel tracks a small `ExecutionContext` with `binding_env_ref`,
`region_ref`, and `authority_ref`. Runtime trace records use local
`continuation:*` refs by default; when debug evidence is requested, those refs
map to content-addressed `ContinuationRoot` objects whose reachable DAG contains
the executable stack, frame, environment, and context payloads. Separate
`continuation-control:*` refs name the role-independent dynamic control shape
used by trace lifecycle validation. Source admission, observed frontier,
retry/idempotency, and carrier authentication remain outside the continuation
object DAG. Ordinary value-position replay is exposed through
`shepherd_kernel_v3_reference.kernel.resume_continuation(...)` over a serialized
`ContinuationReplayArtifact`. In the current runtime profile this artifact is
the executable restart input: it carries the content-addressed continuation DAG
needed to rebuild stack, environment, and execution context without a live
Python closure. When source metadata is present, its `source_key` is a canonical
content ref over the program, source record, root continuation, and result
schema, and may be consumed by the optional one-shot replay ledger. `source_ref`,
`source_record_type`, and `effect_kind` remain carrier metadata rather than
certified trace provenance. Publication terminal replay remains future work.

## Trace modes

The direct source evaluator is host-friendly: it accepts
`DynamicHandlerInstall`, where handler code is a Python `payload -> Computation`
builder, and returns source outcomes directly.

The kernel and trace path is the auditable static fragment:
`StaticHandlerInstall` stores a source handler body plus `payload_name`,
`elaborate(...)` lowers it into first-order kernel IR, `run_kernel(...)` runs the
defunctionalized abstract machine after `validate_kernel_program(...)` preflight,
and `run_trace(...)` records a normalized trace snapshot. If a run suspends or
delays and the caller later applies the returned continuation, use
`TraceSession` to observe the additional records emitted by that resumed
execution. By default, trace records carry runtime-local continuation/control
refs and do not construct continuation-object evidence. Trace lifecycle
validation is public debug tooling; content-addressed continuation-object
evidence is conformance/debug data and is returned only when requested with
`include_debug_evidence=True`. `ExternalEffectRequest` is the first-order host
handoff for an unhandled effect: it carries the effect declaration, trace prefix,
and restart artifact required to admit a later completed host observation.
`HostCompleted` is the only host observation supported by this replay profile.
The `start_replayable_kernel_transition(...)` and
`resume_replayable_kernel_transition(...)` helpers return a validated
`ReplayableKernelTransition`, a minimal durable envelope containing the
completed payload, external request, or rejected host observation plus the exact
trace delta emitted by that transition. Completed and rejected replay payloads
are explicitly bound to the `program_ref`, including no-trace completed runs.
Direct rejected transitions require the caller to provide the parent request
transition ref; without that frontier, post-admission failure cannot be encoded
as a journal-valid rejected transition.
`start_kernel_replay(...)` and
`resume_kernel_replay(...)` add a small in-memory `KernelReplayState` around
those helpers for canary-style runtime integration. The authoritative state
surface is the prepared program identity, exact open request capability,
consumed source keys, transition frontier, and terminal/rejected status. Direct
state JSON retains accumulated trace only as a diagnostic snapshot. Hosts that
need process restart should prefer `KernelReplayJournal`, a validated sequence
of `ReplayableKernelTransition` envelopes from which `KernelReplayState` and
trace can be reconstructed without trusting serialized state trace. The journal
wire shape stores continuation objects once in a shared catalog and records
external request transitions by artifact/request refs, while live
`ExternalEffectRequest` values remain materialized host handoffs. The journal is
a trusted local log, not an independent semantic verifier: transition shape,
canonical ids, catalog refs, parent frontier order, program identity, and host
observation refs are checked, but the current wire shape does not carry enough
host observation payload to re-execute each transition from scratch; rejected
observation reasons are trusted diagnostics. The current
profile is sequential: exactly one external request may be open, and that
request must sit at the current transition frontier. `KernelReplaySession` is a
small mutable wrapper for canaries that persists rejected state as a rejected
transition in its journal.
Its compact request refs are scoped to the live session; callers that need a
portable boundary must serialize a `KernelReplayJournal`, whose catalog validates
the complete continuation-object closure for those refs. Host-facing
`ExternalEffectRequestDescriptor` payloads are snapshots, not live request
payload aliases. Standalone replay transition JSON remains self-contained and
rejects compact refs without a journal catalog.
Full semantic transition batch admission is still future work. The conformance
artifact path validates offline Core-A/runtime evidence. Publication-experimental
continuation evidence artifacts remain intentionally unsupported until the
publication profile has a stable artifact/replay contract. The
`shepherd_kernel_v3_reference.trace.serde` module serializes trace records to
stable JSON and deserializes them back into dataclasses for validation.

`prepare_kernel_program(...)` is the preferred runtime admission boundary for
repeated execution. It snapshots and validates a `KernelProgram`, builds the
shared program index, and lazily caches the stable program identity the first
time debug evidence or identity readiness needs it. Execution-only
`run_kernel(...)` over a prepared program does not compute program identity.

## Value domain

The kernel and trace layers intentionally run over explicit JSON-compatible
values: `None`, booleans, integers, finite floats, strings, lists/tuples, and
string-keyed dicts containing those values. This keeps continuation refs stable
across processes and runs. Opaque host objects and arbitrary dataclasses are
accepted by the direct source evaluator but are outside this kernel fragment;
`run_kernel(...)` and `run_trace(...)` may reject them when a captured
continuation is referenced. Built-in schemas have stable structural fingerprints;
custom schemas used by the auditable kernel path must provide a deterministic
`fingerprint()` method.

## Out of scope

This package is deliberately small. It does not include:

- a full §05 trace machine with replay, branch-affine callable reentry,
  authority records, scheduler records, or migration records;
- a full §06 adequacy proof or paired-evaluator proof harness;
- direct-evaluator support for `Forward` / `TerminalDelay` / `TerminalFork`;
- replay/admission validation for externally extended publication-control
  traces;
- branch-affine callable resumption beyond the one-shot single-root case;
- §08 counterfactual replay;
- the §11 runtime contract.

The matching capability spikes under
`../../../spikes/260501-shepherd-kernel-v3/` cover the items above.

## Usage

```python
from shepherd_kernel_v3_reference import (
    AnySchema, Completed, DynamicHandlerInstall, Handle, HandlerEnv,
    Let, Lit, Perform, Resume, Return, Var, run,
)

program = Handle(
    Let("y", Perform("ask", Lit(None)), Return(Var("y"))),
    HandlerEnv((
        DynamicHandlerInstall(
            effect_kind="ask",
            handler_id="h.v1",
            handled_result_schema=AnySchema(),
            body=lambda _payload: Let("r", Resume(Lit(42)), Return(Var("r"))),
        ),
    )),
)

assert run(program) == Completed(42)
```

The same source program can be elaborated and run through the abstract/trace
path when its handler installs use static handler bodies with `payload_name`
rather than Python `payload -> Computation` builders:

```python
from shepherd_kernel_v3_reference import StaticHandlerInstall
from shepherd_kernel_v3_reference.kernel import elaborate, prepare_kernel_program
from shepherd_kernel_v3_reference.trace.machine import TraceSession, run_trace
from shepherd_kernel_v3_reference.trace.validate import validate_core_trace

static_program = Handle(
    Let("y", Perform("ask", Lit(None)), Return(Var("y"))),
    HandlerEnv((
        StaticHandlerInstall(
            effect_kind="ask",
            handler_id="h.v1",
            handled_result_schema=AnySchema(),
            payload_name="_payload",
            body=Let("r", Resume(Lit(42)), Return(Var("r"))),
        ),
    )),
)

prepared = prepare_kernel_program(elaborate(static_program))

result = run_trace(prepared)
assert result.outcome == Completed(42)
validate_core_trace(result.trace)

debug_result = run_trace(prepared, include_debug_evidence=True)
assert debug_result.require_debug_evidence().program_ref.startswith("program:sha256:")

session = TraceSession(prepared)
live_result = session.run()
assert live_result.outcome == Completed(42)
assert session.trace == live_result.trace
```

## Contract profiles (`core-reference-v0-lite`)

Beyond the source-author and trace APIs above, the package ships a *contract
layer*: a narrowed, byte-stable wire profile intended as the differential
boundary between this Python reference and a future Lean-native kernel. The
bootstrap profile is `core-reference-v0-lite` — `int`/`null` values,
`IntSchema` / `NullSchema` / `LiteralSchema(int)` schemas, and the Core-0H /
Core-A source fragment (`Return` / `Let` / `Perform` / `Handle` / `Var` / `Lit`
plus `Resume` / `Abort` handler bodies). See
`../../design/shepherd-kernel-v3/` for the normative semantics.

A contract profile is *enforced at admission*, not merely stamped.
`admit_and_prepare(...)` is the only minter of a prepared program carrying a
`requires_source_admission` profile: it runs `validate_profile_admission(source,
profile)` on the **source AST** — rejecting `RecordExpr`,
`DynamicHandlerInstall`, non-`-lite` handler-body shapes, and string/record
values while they still exist — then elaborates and stamps. IR-level
`prepare_kernel_program(...)` defaults to the permissive `CORE_A` profile and
*refuses* to stamp a `requires_source_admission` profile on raw IR, so a `-lite`
stamp always means the program was actually admitted.

```python
from shepherd_kernel_v3_reference import (
    Handle, HandlerEnv, Let, Lit, Perform, Resume, Return, StaticHandlerInstall, Var,
)
from shepherd_kernel_v3_reference.schemas import IntSchema
from shepherd_kernel_v3_reference.kernel import admit_and_prepare
from shepherd_kernel_v3_reference.profiles import CORE_REFERENCE_V0_LITE

program = Handle(
    Let("y", Perform("ask", Lit(1)), Return(Var("y"))),
    HandlerEnv((
        StaticHandlerInstall(
            effect_kind="ask", handler_id="h.v1",
            handled_result_schema=IntSchema(), payload_name="_payload",
            body=Let("r", Resume(Lit(7)), Return(Var("r"))),
        ),
    )),
)

# The only way to mint a -lite prepared program: source-level admit + elaborate + stamp.
prepared = admit_and_prepare(program, profile=CORE_REFERENCE_V0_LITE)
assert prepared.profile is CORE_REFERENCE_V0_LITE

# prepare_kernel_program(elaborate(program), profile=CORE_REFERENCE_V0_LITE)
#   -> raises KernelProgramValidationError: -lite requires source admission,
#      which cannot run on already-elaborated IR.
```

The normative consumer API runs admitted programs and projects wire-shape
results. By convention these symbols live in named submodules (see the
`__init__.py` module docstring); import them directly rather than from the top
level:

- `run.start_kernel_run` / `run.resume_kernel_run` /
  `run.validate_observation_stream` drive a prepared program through an
  observation stream, each returning an `envelope.KernelResultEnvelope`.
- `projection.semantic_batch_from_transition` /
  `projection.project_envelope_to_wire` produce the byte-stable `WireResult`
  (a `SemanticTransitionBatch` plus a mandatory `CanonicalRefMap`) that is the
  Python↔Lean differential contract; `projection.validate_semantic_batch`
  enforces ref-map coverage / tightness / determinism.

The hand-authored `-lite` differential fixtures live under
`tests/conformance/v0_lite/`; see that directory's `README.md` for the fixture
format and the six rejection-kind discriminators.

## Benchmarks

The benchmark harness covers pure `Let` chains, sequential handled effects,
sequential external replay effects, nested handlers, and publication fork traces:

```
uv run python shepherd/packages/kernel-v3-reference/benchmarks/kernel_runtime_bench.py --sizes 25,50,100 --repeat 5
uv run python shepherd/packages/kernel-v3-reference/benchmarks/kernel_runtime_bench.py --sizes 50,100,200,400 --repeat 3 --check-linear
```

It reports `run_kernel`, default `run_trace` without continuation-object
evidence, opt-in debug evidence trace construction, lifecycle validation,
conformance evidence validation status where applicable, trace record count,
evidence object count, evidence JSON size, and `Env.bindings` materialization
reads. It also reports replay start, full host-loop replay, direct state JSON,
journal JSON/derivation costs, replay transition counts, consumed source counts,
journal closure object reads, long-open-journal current-request costs, and
serialized replay sizes split across thin transition envelopes, artifact catalog
records, continuation-object catalog entries, and the full replay journal where
the workload is in the replayable profile.

## Run tests

```
uv run pytest
```
