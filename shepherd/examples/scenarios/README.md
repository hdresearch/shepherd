# Scenarios

> Status: migration reference. These scenarios are retained as legacy
> class-form or advanced provider-backed demonstrations while the function-form
> surface lands.

Complete, realistic agent workflows retained as migration inventory. These
scripts currently remain legacy class-form or advanced provider-backed
demonstrations while the function-form surface lands. They are not first-run
examples, and some need import/API migration before they run against the
current top-level facade.
Provider-backed entries are best-effort demonstrations, so use the final
`Outcome` block to see what a given run actually demonstrated.

## Available Scenarios

| Scenario | Status | Description | Key Concepts |
|----------|--------|-------------|--------------|
| `simple_tasks.py` | legacy-class-form | Basic task patterns | Input/Output |
| `fix_bug.py` | legacy-class-form | Writable bug-fix demonstration | WorkspaceRef, Context |
| `review_code.py` | legacy-class-form | Read-only review demonstration | Structured outputs |
| `readonly_analysis.py` | legacy-class-form | Read-only analysis | WorkspaceRef (read-only) |
| `combined_chaining.py` | legacy-class-form | Advanced continuity demo | SessionState, WorkspaceRef, chaining |

## Prerequisites

- Run the syntax nucleus tutorial first:
  `uv run python shepherd/examples/tutorials/syntax_nucleus.py`
- Treat numbered tutorials as migration inventory unless their README
  marks them `current`.
- `ANTHROPIC_API_KEY` environment variable
- `pip install shepherd[all]`

## Migration Use

Treat these files as historical workflow examples to inspect and port. They are
not a current command cookbook; check a script's imports and owner-path
migration status before running it.

Provider-backed scenarios are best-effort demonstrations. Use the final
`Outcome` section to distinguish "script ran" from "workflow demonstrated".
`exit 0` means the script completed without a local setup or runtime failure;
it does not guarantee edits, findings, or cross-task continuity on every run.

## See Also

- `shepherd/examples/tutorials/` — Progressive learning path
- `shepherd/examples/reference/` — API deep-dive examples
- `docs/design/proposed/260505-plans/00-syntax-nucleus/EXAMPLES_AUDIT.md` —
  migration routing for examples and guides
