# shepherd-dialect

The Shepherd dialect over vcs-core's execution-mechanism SPI — the production
**run driver** (`ShepherdRunDriver`), discharged from vcs-core's experimental
staging per the dialect-composes boundary
([`docs/engineering/convergence/execution-boundary.md`](../../../docs/engineering/convergence/execution-boundary.md)).

- vcs-core owns the *mechanisms*: reversible scopes, the confined-launch verb,
  implicit capture at merge, dispatch.
- This package owns *policy + composition*: the `run` command's vocabulary,
  task resolution, the provider seam, and (from B3c) the `may=` →
  `ConfinementSpec` lowering.

Import discipline: only `vcs_core.runtime_api`, `vcs_core.spi`, and
`vcs_core.runtime_substrate` — never `vcs_core._*` or retired
`vcs_core.experimental` homes. The run-path executor guard (PD7) and the
no-private-coupling ratchet point here.

```bash
uv run --package shepherd-dialect pytest
```

## Runbook — the real-SDK demo (manual; never CI)

The gated/CI provider is the **deterministic fake** (`decisions.md`
`deterministic-fake-v1-provider`). The **real Claude Agent SDK body is the same
shape, swapped in** — `ClaudeAgentProvider` runs the headless `claude` CLI
inside the jail via `launch_confined` — and is run manually, with the
maintainer: nondeterministic, auth-needing, never a CI gate.

Prerequisites: macOS with `/usr/bin/sandbox-exec` (Seatbelt × clonefile — the
reversible × jailed cell of the run-mode matrix), the `claude` CLI on `PATH`
(probed at 2.1.172), and `ANTHROPIC_API_KEY` exported. Then:

```bash
uv run --package shepherd-dialect python spikes/260610-real-sdk-demo/run_demo.py
```

What it shows (budget ≈ 2 min total; the script SKIPs without the key):

- **success ×2** — real Claude's Write tool creates a real file in the carrier's
  working copy inside the jail; the delta is captured implicitly at merge; the
  durable hybrid trace reads back `merged`, and the fourth-row `task.invocation`
  digest recomputes byte-exactly and **holds across both runs** (cross-run
  identity under `shepherd.kernel.canonical.v2`, real body).
- **readonly** — `may=ReadOnly` refuses fail-closed at the jail. The CLI *hangs*
  under the denied network (S1 finding: `spikes/260610-real-sdk-jail-probe`), so
  the provider's argv carries its own hard stop (perl `alarm`+`exec`, mandatory —
  `launch_confined` has no timeout); the wrap discards; ground stays pristine;
  the trace outlives the discard (`discarded`, output pointer `None`).
- **supervised-deny** — `drafts_only_supervisor` (Pattern B, check-at-commit)
  denies the real agent's out-of-`drafts/` delta at the last undo point; the
  denial is recorded into the durable trace as a `supervisor.decision` event.

Honest non-claims (execution-boundary.md §7): no network `may=` enforcement
claim (coarse all-or-nothing until the egress broker); the CLI's own
`--allowed-tools` gating is the *advisory framework tier* — the jail is the
boundary; command-lane effects from inside the jail are Phase E.

Evidence of the maintainer-run demo: `spikes/260610-real-sdk-demo/FINDINGS.md`.
Plan: [`260610-1727-real-sdk-demo-plan.md`](../../../260610-1727-real-sdk-demo-plan.md).

## The authoring surface (re-pinned 2026-06-10)

Function-form only (triage D1): `@task` bodies are plain functions; the
class-form API retired with the spine.

- **Checks** — `Annotated[str, NonEmpty()]` on parameters (preconditions —
  refused *before* the reversible fork: no carrier cost, durable trace terminal
  `refused`) and on the return annotation (postconditions — the wrap discards).
  Builtins: `NonEmpty`, `InRange`, `Matches`, `MaxLength`, `FileExists`.
- **`@step`** — the docstring is the model prompt; outputs parse into the
  declared return type; `step.{started,completed,failed}` land in the run's
  durable trace (no parallel stream).
- **Metadata & serde** — `extract_task_metadata`, `task_input_model`,
  `dump_task_args`/`load_task_args` (the JSON-boundary roundtrip; also the
  typed fourth-row args key — same values ⇒ same cross-run digest),
  `task_prompt`, `extract_task_source`.
- **Source validation** — `validate_task_source`/`check_task_source`: the
  dependency-free **advisory** filter; the jail is the enforcement boundary
  (ledger `source-validation-is-advisory-the-jail-enforces`).
- **Autoconfig (mechanical)** — `Infer`, `extract_infer_fields`,
  `build_inference_model`; the LLM half rides the battery tranche.
