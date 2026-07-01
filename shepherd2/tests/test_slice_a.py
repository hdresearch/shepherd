from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shepherd2 import (
    AppendBatch,
    AppendContext,
    AppendGroup,
    Fact,
    FactBody,
    FactDraft,
    FactShape,
    OwnerCutoffSpec,
    ReadContext,
    RetainedContextDraft,
    SQLiteTraceStore,
    TraceSlice,
    TraceStoreError,
    create_execution_batch,
    execution_id_for,
    project_execution_slice,
)

if TYPE_CHECKING:
    from pathlib import Path


TRUSTED = AppendContext(
    actor_ref="runtime:test",
    presented_witness_refs=("trusted:internal",),
    schema_version_set="shepherd2-slice-a",
    trust_mode="internal",
)
READER = ReadContext(actor_ref="reader")


def _draft(*, kind_label: str, schema_ref: str | None = None, **kwargs: object) -> FactDraft:
    return FactDraft(
        kind_label=kind_label,
        mode=str(kwargs.pop("mode", "capture")),  # type: ignore[arg-type]
        schema_ref=schema_ref or f"shepherd2.trace.{kind_label}.v1",
        **kwargs,
    )


def test_fact_draft_body_split_and_local_refs_resolved_before_retention() -> None:
    store = SQLiteTraceStore()
    receipt = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:local-refs",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(
                        _draft(
                            append_local_id="local:first",
                            kind_label="step",
                            payload={"n": 1},
                        ),
                        _draft(
                            append_local_id="local:second",
                            kind_label="step",
                            payload={"n": 2},
                            caused_by_local_refs=("local:first",),
                        ),
                    ),
                ),
            ),
        ),
    )

    second = store.read_fact(READER, receipt.fact_ids[1])

    assert isinstance(second, Fact)
    assert isinstance(second.body, FactBody)
    assert second.fact_kind == "step"
    assert second.body.payload == {"n": 2}
    assert second.envelope.caused_by_fact_ids == (receipt.fact_ids[0],)
    assert "local:first" not in second.envelope.caused_by_fact_ids


def test_retained_context_refs_explain_committed_facts_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "trace.sqlite"
    batch = AppendBatch(
        append_intent_id="intent:context",
        groups=(
            AppendGroup(
                trace_owner_id="owner:one",
                retained_context=RetainedContextDraft(
                    active_binding_refs=("binding:supervisor",),
                    capability_witness_refs=("cap:append-step",),
                    semantic_environment_refs=("schema-set:shepherd2-slice-a",),
                    visibility_policy_refs=("visibility:payload",),
                ),
                fact_drafts=(_draft(kind_label="step", payload={"ok": True}),),
            ),
        ),
    )

    with SQLiteTraceStore(db_path) as store:
        receipt = store.append(TRUSTED, batch)
        fact = store.read_fact(READER, receipt.fact_ids[0])
        context_id = receipt.context_receipts[0]

    with SQLiteTraceStore(db_path) as restarted:
        retry = restarted.append(TRUSTED, batch)
        restored_fact = restarted.read_fact(READER, receipt.fact_ids[0])
        context = restarted.read_context(context_id)

    assert retry == receipt
    assert restored_fact.retained_context_ref == context_id
    assert fact.retained_context_ref == context_id
    assert context.active_binding_refs == ("binding:supervisor",)
    assert context.capability_witness_refs == ("cap:append-step",)
    assert context.semantic_environment_refs == ("schema-set:shepherd2-slice-a", "shepherd2-slice-a")
    assert context.visibility_policy_refs == ("visibility:payload",)


def test_presented_witnesses_are_not_retained_by_accident() -> None:
    store = SQLiteTraceStore()
    context = AppendContext(
        actor_ref="runtime:test",
        presented_witness_refs=("trusted:internal", "cap:not-retained-implicitly"),
        schema_version_set="shepherd2-slice-a",
        trust_mode="internal",
    )
    receipt = store.append(
        context,
        AppendBatch(
            append_intent_id="intent:no-implicit-witness",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(_draft(kind_label="step", payload={}),),
                ),
            ),
        ),
    )

    retained = store.read_context(receipt.context_receipts[0])

    assert retained.capability_witness_refs == ()
    assert "cap:not-retained-implicitly" not in retained.capability_witness_refs


def test_trace_slice_is_graph_shaped_and_visibility_filtered() -> None:
    store = SQLiteTraceStore()
    receipt = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:slice",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    retained_context=RetainedContextDraft(capability_witness_refs=("cap:slice",)),
                    fact_drafts=(
                        _draft(append_local_id="local:first", kind_label="step", payload={"n": 1}),
                        _draft(
                            append_local_id="local:second",
                            kind_label="step",
                            payload={"n": 2},
                            caused_by_local_refs=("local:first",),
                        ),
                    ),
                ),
            ),
        ),
    )
    cutoff = store.publish_frontier(
        TRUSTED,
        OwnerCutoffSpec(
            frontier_id="frontier:slice",
            target_trace_owner_id="owner:one",
            through_fact_id=receipt.fact_ids[-1],
        ),
    )

    shape = store.resolve_frontier(ReadContext(actor_ref="reader", visibility_profile="shape_only"), cutoff.frontier_id)
    payload = store.resolve_frontier(ReadContext(actor_ref="reader", visibility_profile="payload"), cutoff.frontier_id)

    assert isinstance(shape, TraceSlice)
    assert shape.fact_ids() == receipt.fact_ids
    assert shape.owner_paths == {"owner:one": receipt.fact_ids}
    assert shape.causal_edges == ((receipt.fact_ids[0], receipt.fact_ids[1]),)
    assert all(isinstance(fact, FactShape) for fact in shape.visible_facts_by_id.values())
    assert not any(isinstance(fact, Fact) for fact in shape.visible_facts_by_id.values())
    assert shape.contexts_by_id == {}
    assert shape.context_anchors

    assert all(isinstance(fact, Fact) for fact in payload.visible_facts_by_id.values())
    assert [
        fact.body.payload
        for fact_id in payload.fact_ids()
        if isinstance((fact := payload.visible_facts_by_id[fact_id]), Fact)
    ] == [{"n": 1}, {"n": 2}]
    assert payload.contexts_by_id
    assert payload.context_anchors == ()


def test_trace_slice_mode_filter_selects_declarations_or_captures() -> None:
    store = SQLiteTraceStore()
    execution_id = execution_id_for("intent:mode-filter:create")
    receipt = store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:mode-filter:create",
            execution_id=execution_id,
            task_ref="Task",
            inputs={"x": 1},
        ),
    )

    both = store.read_owner_prefix(READER, execution_id, 99)
    declarations = store.read_owner_prefix(READER, execution_id, 99, mode_filter="declarations_only")
    captures = store.read_owner_prefix(READER, execution_id, 99, mode_filter="captures_only")

    assert both.mode_filter == "both"
    assert both.fact_ids() == receipt.fact_ids
    assert declarations.mode_filter == "declarations_only"
    assert captures.mode_filter == "captures_only"
    assert declarations.fact_ids() == (receipt.fact_ids[0],)
    assert captures.fact_ids() == (receipt.fact_ids[1],)


def test_default_context_stamps_schema_environment() -> None:
    store = SQLiteTraceStore()
    receipt = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:default-context",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(_draft(kind_label="step", payload={}),),
                ),
            ),
        ),
    )

    context = store.read_context(receipt.context_receipts[0])

    assert context.semantic_environment_refs == ("shepherd2-slice-a",)


def test_read_fact_shape_only_returns_fact_shape() -> None:
    store = SQLiteTraceStore()
    receipt = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:read-shape",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(_draft(kind_label="step", payload={"secret": True}),),
                ),
            ),
        ),
    )

    visible = store.read_fact(ReadContext(actor_ref="reader", visibility_profile="shape_only"), receipt.fact_ids[0])

    assert isinstance(visible, FactShape)
    assert visible.fact_kind == "step"


def test_owner_cutoff_preserves_external_causal_anchor() -> None:
    store = SQLiteTraceStore()
    child = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:child-event",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:child",
                    fact_drafts=(_draft(kind_label="child_event", payload={"path": "note.txt"}),),
                ),
            ),
        ),
    )
    parent = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:parent-observed",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:parent",
                    causal_parents=child.fact_ids,
                    fact_drafts=(_draft(kind_label="observed", payload={"binding": "binding:one"}),),
                ),
            ),
        ),
    )
    cutoff = store.publish_frontier(
        TRUSTED,
        OwnerCutoffSpec(
            frontier_id="frontier:parent",
            target_trace_owner_id="owner:parent",
            through_fact_id=parent.fact_ids[-1],
        ),
    )

    view = store.resolve_frontier(ReadContext(actor_ref="reader", visibility_profile="shape_only"), cutoff.frontier_id)

    assert view.fact_ids() == parent.fact_ids
    assert view.external_anchors[0].ref == child.fact_ids[0]
    assert view.external_anchors[0].visible_shape["kind_label"] == "child_event"


def test_publish_frontier_uses_append_path_and_is_idempotent() -> None:
    store = SQLiteTraceStore()
    receipt = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:target",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(_draft(kind_label="terminal", payload={}),),
                ),
            ),
        ),
    )
    spec = OwnerCutoffSpec(
        frontier_id="frontier:target",
        target_trace_owner_id="owner:one",
        through_fact_id=receipt.fact_ids[-1],
        publisher_trace_owner_id="owner:publisher",
    )

    first = store.publish_frontier(TRUSTED, spec)
    second = store.publish_frontier(TRUSTED, spec)
    frontier_fact = store.read_fact(READER, first.created_by_fact_id or "")

    assert second == first
    assert frontier_fact.fact_kind == "frontier_published"
    assert frontier_fact.trace_owner_id == "owner:publisher"
    assert frontier_fact.retained_context_ref
    assert frontier_fact.envelope.fact_id in store.read_owner_prefix(READER, "owner:publisher", 99).fact_ids()


def test_frontier_fact_payload_is_source_of_truth_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(db_path) as store:
        receipt = store.append(
            TRUSTED,
            AppendBatch(
                append_intent_id="intent:frontier-truth-target",
                groups=(
                    AppendGroup(
                        trace_owner_id="owner:one",
                        fact_drafts=(_draft(kind_label="terminal", payload={}),),
                    ),
                ),
            ),
        )
        expected = store.publish_frontier(
            TRUSTED,
            OwnerCutoffSpec(
                frontier_id="frontier:truth",
                target_trace_owner_id="owner:one",
                through_fact_id=receipt.fact_ids[-1],
            ),
        )

    with SQLiteTraceStore(db_path) as restarted:
        restored = restarted.read_owner_cutoff("frontier:truth")

    assert restored == expected


def test_resolver_index_disagreement_does_not_create_truth() -> None:
    store = SQLiteTraceStore()
    first = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:first-target",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(_draft(kind_label="step", payload={"n": 1}),),
                ),
            ),
        ),
    )
    second = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:second-target",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(_draft(kind_label="step", payload={"n": 2}),),
                ),
            ),
        ),
    )
    store.publish_frontier(
        TRUSTED,
        OwnerCutoffSpec(
            frontier_id="frontier:corrupt",
            target_trace_owner_id="owner:one",
            through_fact_id=first.fact_ids[-1],
        ),
    )
    store._db.execute(
        "UPDATE frontiers SET through_fact_id = ?, through_owner_ordinal = ? WHERE frontier_id = ?",
        (second.fact_ids[-1], 1, "frontier:corrupt"),
    )

    with pytest.raises(TraceStoreError, match="resolver index disagrees"):
        store.resolve_frontier(ReadContext(actor_ref="reader"), "frontier:corrupt")


def test_malformed_frontier_fact_is_rejected() -> None:
    store = SQLiteTraceStore()
    receipt = store.append(
        TRUSTED,
        AppendBatch(
            append_intent_id="intent:malformed-frontier-target",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:one",
                    fact_drafts=(_draft(kind_label="terminal", payload={}),),
                ),
            ),
        ),
    )
    cutoff = store.publish_frontier(
        TRUSTED,
        OwnerCutoffSpec(
            frontier_id="frontier:malformed",
            target_trace_owner_id="owner:one",
            through_fact_id=receipt.fact_ids[-1],
        ),
    )
    store._db.execute(
        "UPDATE records SET body_json = ? WHERE record_id = ?",
        ('{"payload":{"frontier_id":"frontier:malformed"}}', cutoff.created_by_fact_id),
    )

    with pytest.raises(TraceStoreError, match="frontier fact missing"):
        store.read_owner_cutoff("frontier:malformed")


def test_untrusted_append_is_rejected_before_retaining_facts() -> None:
    store = SQLiteTraceStore()
    untrusted = AppendContext(actor_ref="user:test", presented_witness_refs=(), schema_version_set="shepherd2-slice-a")

    with pytest.raises(TraceStoreError, match="trusted internal"):
        store.append(
            untrusted,
            AppendBatch(
                append_intent_id="intent:untrusted",
                groups=(
                    AppendGroup(
                        trace_owner_id="owner:one",
                        fact_drafts=(_draft(kind_label="step", payload={}),),
                    ),
                ),
            ),
        )

    assert store.fact_count() == 0
    assert store.context_count() == 0


def test_failed_append_retains_no_partial_facts_or_contexts() -> None:
    store = SQLiteTraceStore()

    with pytest.raises(ValueError, match="schema_ref"):
        store.append(
            TRUSTED,
            AppendBatch(
                append_intent_id="intent:bad",
                groups=(
                    AppendGroup(
                        trace_owner_id="owner:one",
                        retained_context=RetainedContextDraft(capability_witness_refs=("cap:bad",)),
                        fact_drafts=(
                            _draft(kind_label="step", payload={}),
                            FactDraft(mode="capture", schema_ref="", payload={}),
                        ),
                    ),
                ),
            ),
        )

    assert store.fact_count() == 0
    assert store.context_count() == 0


def test_schema_ref_is_retained_and_not_inferred_on_decode(tmp_path: Path) -> None:
    db_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(db_path) as store:
        receipt = store.append(
            TRUSTED,
            AppendBatch(
                append_intent_id="intent:schema-ref",
                groups=(
                    AppendGroup(
                        trace_owner_id="owner:one",
                        fact_drafts=(
                            _draft(
                                kind_label="custom_step",
                                schema_ref="example.custom_step.v7",
                                payload={"value": 3},
                            ),
                        ),
                    ),
                ),
            ),
        )

    with SQLiteTraceStore(db_path) as restarted:
        fact = restarted.read_fact(READER, receipt.fact_ids[0])

    assert fact.envelope.schema_ref == "example.custom_step.v7"
    assert fact.body.payload == {"value": 3}


def test_schema_ref_is_structurally_required_on_record_draft() -> None:
    with pytest.raises(TypeError, match="schema_ref"):
        FactDraft(kind_label="step", mode="capture", payload={})  # type: ignore[call-arg]


def test_project_execution_from_trace_slice() -> None:
    store = SQLiteTraceStore()
    execution_id = execution_id_for("intent:execution-slice:create")
    receipt = store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:execution-slice:create",
            execution_id=execution_id,
            task_ref="Task",
            inputs={"x": 10},
        ),
    )
    cutoff = store.publish_frontier(
        TRUSTED,
        OwnerCutoffSpec(
            frontier_id="frontier:execution-slice",
            target_trace_owner_id=execution_id,
            through_fact_id=receipt.fact_ids[-1],
        ),
    )
    trace_slice = store.resolve_frontier(ReadContext(actor_ref="reader"), cutoff.frontier_id)

    projected = project_execution_slice(trace_slice, execution_id)

    assert projected.execution_id == execution_id
    assert projected.task_ref == "Task"
    assert projected.inputs == {"x": 10}
    assert projected.status == "running"


def test_projection_rejects_shape_only_slice_when_payload_required() -> None:
    store = SQLiteTraceStore()
    execution_id = execution_id_for("intent:shape-only-execution:create")
    receipt = store.append(
        TRUSTED,
        create_execution_batch(
            append_intent_id="intent:shape-only-execution:create",
            execution_id=execution_id,
            task_ref="Task",
            inputs={"x": 10},
        ),
    )
    cutoff = store.publish_frontier(
        TRUSTED,
        OwnerCutoffSpec(
            frontier_id="frontier:shape-only-execution",
            target_trace_owner_id=execution_id,
            through_fact_id=receipt.fact_ids[-1],
        ),
    )
    trace_slice = store.resolve_frontier(
        ReadContext(actor_ref="reader", visibility_profile="shape_only"),
        cutoff.frontier_id,
    )

    with pytest.raises(TypeError, match="payload-visible"):
        project_execution_slice(trace_slice, execution_id)
