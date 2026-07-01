<!--
PR authoring objective:
Make this PR easy to review. Explain the meaning of the delta, not just which files changed.
Optimize for a reviewer who wants to know:
1. why this PR exists,
2. what logical deltas it contains,
3. what order to read them in,
4. where correctness or design risk may be hiding,
5. what validation supports the change.

Avoid duplication:
- Summary states the outcome only.
- Motivation explains why this PR exists now.
- Delta Breakdown explains the logical changes.
- Entry Point gives the review/read order DAG.
- Review Guide calls out risk and reviewer questions.
- Validation lists evidence only.
Keep bullets concrete and branch-specific. Do not include line-count or file-count accounting unless it affects review risk.
-->

## Summary

<!-- 2-4 bullets. State the outcome and scope. Do not repeat motivation, file lists, or validation. -->

## Motivation / Context

<!-- Why is this PR needed now? If stacked, explain why this chunk belongs at this point in the stack. Name what this PR intentionally does not include if that prevents review confusion. -->

## Delta Breakdown

<!-- Explain the logical deltas in suggested review order. Each item should say:
- Meaning: what behavior/process/contract changes
- Key files: only the files needed to understand that delta
- Reviewer question: what the reviewer should verify
Avoid raw file-count accounting.
-->

## Entry Point

<!-- Provide a read DAG or ordered dependency graph. Start with conceptual/public surfaces, then core implementation, then support glue, then tests/docs. After the graph, list executable/API entrypoints. -->

## Review Guide

<!-- Call out the highest-risk areas, likely failure modes, compatibility concerns, and places where reviewer judgment is needed. Do not repeat the full Delta Breakdown. -->

## Validation

- [ ] Static checks:
- [ ] Type checks:
- [ ] Tests:
- [ ] Targeted/manual validation:
- [ ] Not run:

## Documentation / Changelog / Decisions

<!-- Note docs/changelog/decision-record updates, or explain why they are not needed. -->

## Risk / Rollback / Migration

<!-- Name the main risks, rollback path, migration requirements, and compatibility concerns. -->

## Follow-ups

<!-- List known follow-ups, or say none. -->
