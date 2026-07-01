# Proof Surface Notes For Publication Core

This file records proof-facing changes desired from the Python semantic core.
It intentionally does not modify the Lean development.

## Expression Payload Dataflow

The source expression layer now admits `RecordExpr`, a pure record expression
whose fields are themselves source expressions. This is needed for examples
such as `AuditEntry(section)`, where the payload of a handler-side effect must
depend on the value returned by a resumed worker continuation.

Desired proof/model update:

- Extend the expression grammar from literals and variables to finite records.
- Add an expression evaluation rule:
  `eval(RecordExpr({k_i = e_i}), rho) = {k_i = eval(e_i, rho)}`.
- Preserve the existing source-to-kernel obligations: record expressions are
  pure, deterministic, and do not interact with handler lookup or continuation
  splitting.
- Update any expression fingerprint or code-identity model to treat record
  fields structurally and in a stable field order.

## Worked Example Alignment

The §13 worked example should be read as auditing the actual resumed worker
result:

```text
section = resume(draft)
_       = perform(audit.log, AuditEntry(section))
return section
```

The audit declaration payload should therefore contain the same `Draft` value
that flows through the worker return and supervisor answer.

## Publication Control Extensions

The Python kernel path now includes these proof-facing surfaces:

- durable `ContinuationImage` catalogs for continuation refs;
- `Forward` / `HandlerForward` with explicit skipped-selection closure;
- `TerminalDelay` / `ContinuationPending` / `ContinuationDelay`;
- pending-source `ContinuationResume(returns_to_handler = false)`;
- `TerminalFork` / `ForkSummary` / `ForkBranch`;
- `TerminalResumeResult` for completed terminal resumes.

The Python validators now share a private executable lifecycle ledger for common
declaration, selection, selected-path, callable-resume, capture, and closure
facts. The publication validator layers the extension surfaces onto that ledger:
forward records must identify and close one skipped selection path, delayed and
forked terminal sources are one-shot source paths, completed delayed sources
must carry exactly one `ContinuationDelay`, completed fork summaries must
materialize every declared branch exactly once, branch scopes have explicit
`branch_scope_ref` lifecycle identities opened by terminal fork-branch resume
and closed by `TerminalResumeResult`, callable resumptions inside active branch
scopes follow Core-A return/closure obligations, and `SelectionClosed` uses the
same dynamic-ancestor and terminal-cause checks as Core-A. Pending delayed
sources must record
`ContinuationDelay` before any external pending-source resume, including in
prefix validation. `Forward` is also the only extension transition
that admits a later handler selection for the same declaration. Completed
terminal forks are currently flat: nested terminal outcome aggregation remains
future work.

Desired proof/model update:

- Keep Core-0 theorem statements scoped to ordinary callable resume/return.
- Add separate extension lemmas for Forward, terminal Delay, and terminal Fork.
- Treat terminal resumes as not returning to handler code and therefore not
  producing `ResumeReturn` or `EffectCapture`.
- Treat branch-scoped fork sources as terminal source tokens, not as
  branch-affine callable handler reentry.
- Model `ContinuationImage` v1 as the storage-free semantic payload named by a
  continuation ref. That ref commits to the complete restartable
  frame/context/code/schema payload, while source admission and retained storage
  remain outside the proof surface.
- Treat fixture-backed semantic transition batches as carrying both trace
  records and the continuation-image payloads those records cite. Batch
  validation checks image shape, ref agreement, program identity, and
  trace/image coverage; replay admission remains a separate layer.

## Remaining Python/Proof Bridge Work

The one-child branch replay spike now gives Lean a structural certificate target
for an exact terminal-fork suffix replay. The remaining Python work that should
precede broader Lean extension proofs is:

1. golden fixture corpus for Forward/Delay/Fork;
2. general replay input streams over the v1 continuation-image catalog;
3. independent program-semantics validation beyond generated trace agreement.

Lean should keep the current Core-0 theorem boundary until the publication
fixture corpus and general replay contract are stable.
