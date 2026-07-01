from __future__ import annotations

import pytest

from shepherd2 import (
    KV_PUT_CAPTURE_SCHEMA,
    KV_PUT_DECLARATION_SCHEMA,
    KV_SQLITE_SUBSTRATE_REF,
    AppendBatch,
    AppendContext,
    AppendGroup,
    EchoSubstrate,
    Fact,
    FactDraft,
    MaterializationRequest,
    MaterializationResult,
    OperationContext,
    ReadContext,
    RetainedContextDraft,
    SQLiteKVSubstrate,
    SQLiteTraceStore,
    SubstrateRegistry,
    TraceStoreError,
    UnknownSubstrateError,
    materialize,
)

APPEND = AppendContext(
    actor_ref="runtime:test",
    presented_witness_refs=("trusted:internal",),
    schema_version_set="shepherd2-slice-a",
    trust_mode="internal",
)
MATERIALIZE = OperationContext(
    actor_ref="runtime:test",
    operation="materialize",
    presented_authority_refs=("trusted:internal",),
    schema_environment_ref="shepherd2-slice-a",
    trust_mode="internal",
)
READ = ReadContext(actor_ref="reader")


def test_materialize_dispatches_via_witness_stamped_substrate() -> None:
    store = SQLiteTraceStore()
    registry = SubstrateRegistry()
    registry.register(EchoSubstrate("test.echo.v1", frozenset({"example.write.v1"})))
    declaration = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:declare-write",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:materialize",
                    retained_context=RetainedContextDraft(substrate_ref="test.echo.v1", containment="contained"),
                    fact_drafts=(
                        FactDraft(
                            mode="declaration",
                            schema_ref="example.write.v1",
                            kind_label="write",
                            payload={"path": "note.txt", "text": "hello"},
                        ),
                    ),
                ),
            ),
        ),
    )

    receipt = materialize(
        store,
        MATERIALIZE,
        MaterializationRequest(
            append_intent_id="intent:materialize-write",
            target_trace_owner_id="owner:materialize",
            target_record_ids=declaration.fact_ids,
        ),
        registry,
    )

    assert receipt.outcome == "success"
    assert receipt.substrate_ref == "test.echo.v1"
    assert receipt.target_record_ids == declaration.fact_ids
    assert len(receipt.produced_record_ids) == 1
    capture = store.read_fact(READ, receipt.produced_record_ids[0])
    assert isinstance(capture, Fact)
    assert capture.envelope.mode == "capture"
    assert capture.envelope.schema_ref == "example.write.v1"
    assert capture.envelope.caused_by_record_ids == declaration.fact_ids
    assert capture.body.payload == {"path": "note.txt", "text": "hello"}
    witness = store.read_fact(READ, capture.envelope.witness_ref)
    assert isinstance(witness, Fact)
    assert witness.body.payload["substrate_ref"] == "test.echo.v1"
    assert witness.body.payload["containment"] == "contained"


def test_materialize_rejects_capture_targets() -> None:
    store = SQLiteTraceStore()
    registry = SubstrateRegistry()
    registry.register(EchoSubstrate("test.echo.v1", frozenset({"example.write.v1"})))
    capture = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:capture-only",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:materialize",
                    retained_context=RetainedContextDraft(substrate_ref="test.echo.v1"),
                    fact_drafts=(
                        FactDraft(
                            mode="capture",
                            schema_ref="example.write.v1",
                            kind_label="write",
                            payload={"path": "note.txt"},
                        ),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(TraceStoreError, match="declaration"):
        materialize(
            store,
            MATERIALIZE,
            MaterializationRequest(
                append_intent_id="intent:bad-materialize",
                target_trace_owner_id="owner:materialize",
                target_record_ids=capture.fact_ids,
            ),
            registry,
        )


def test_materialize_fails_closed_for_unregistered_substrate() -> None:
    store = SQLiteTraceStore()
    declaration = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:unknown-substrate",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:materialize",
                    retained_context=RetainedContextDraft(substrate_ref="missing.substrate.v1"),
                    fact_drafts=(
                        FactDraft(
                            mode="declaration",
                            schema_ref="example.write.v1",
                            kind_label="write",
                            payload={"path": "note.txt"},
                        ),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(UnknownSubstrateError, match=r"missing\.substrate\.v1"):
        materialize(
            store,
            MATERIALIZE,
            MaterializationRequest(
                append_intent_id="intent:missing-substrate",
                target_trace_owner_id="owner:materialize",
                target_record_ids=declaration.fact_ids,
            ),
            SubstrateRegistry(),
        )


def test_materialize_with_deterministic_sqlite_kv_substrate(tmp_path) -> None:
    store = SQLiteTraceStore()
    kv_path = tmp_path / "world.sqlite"
    with SQLiteKVSubstrate(kv_path) as substrate:
        registry = SubstrateRegistry()
        registry.register(substrate)
        declaration = store.append(
            APPEND,
            AppendBatch(
                append_intent_id="intent:kv-put",
                groups=(
                    AppendGroup(
                        trace_owner_id="owner:kv",
                        retained_context=RetainedContextDraft(substrate_ref=KV_SQLITE_SUBSTRATE_REF),
                        fact_drafts=(
                            FactDraft(
                                mode="declaration",
                                schema_ref=KV_PUT_DECLARATION_SCHEMA,
                                kind_label="kv_put",
                                payload={"key": "answer", "value": {"n": 42}},
                            ),
                        ),
                    ),
                ),
            ),
        )

        receipt = materialize(
            store,
            MATERIALIZE,
            MaterializationRequest(
                append_intent_id="intent:kv-apply",
                target_trace_owner_id="owner:kv",
                target_record_ids=declaration.fact_ids,
            ),
            registry,
        )

        assert substrate.get("answer") == {"n": 42}

    assert receipt.outcome == "success"
    assert receipt.substrate_ref == KV_SQLITE_SUBSTRATE_REF
    assert receipt.world_side_anchors == (
        {"kind": "kv_key", "key": "answer", "substrate_ref": KV_SQLITE_SUBSTRATE_REF},
    )
    capture = store.read_fact(READ, receipt.produced_record_ids[0])
    assert isinstance(capture, Fact)
    assert capture.envelope.mode == "capture"
    assert capture.envelope.schema_ref == KV_PUT_CAPTURE_SCHEMA
    assert capture.envelope.caused_by_record_ids == declaration.fact_ids
    assert capture.body.payload == {"key": "answer", "value": {"n": 42}}
    witness = store.read_fact(READ, capture.envelope.witness_ref)
    assert isinstance(witness, Fact)
    assert witness.body.payload["substrate_ref"] == KV_SQLITE_SUBSTRATE_REF


class CountingSubstrate:
    substrate_ref = "test.count.v1"
    declaration_schemas = frozenset({"example.write.v1"})
    capture_schemas = frozenset({"example.write.applied.v1"})
    containment = "contained"

    def __init__(self) -> None:
        self.calls = 0

    def materialize(self, records: tuple[Fact, ...]) -> MaterializationResult:
        self.calls += 1
        return MaterializationResult(
            outcome="success",
            capture_drafts=(
                FactDraft(
                    mode="capture",
                    schema_ref="example.write.applied.v1",
                    kind_label="write_applied",
                    payload={"call": self.calls},
                    caused_by_fact_ids=(records[0].envelope.record_id,),
                ),
            ),
        )


def test_materialize_retry_returns_receipt_without_redispatching_substrate(tmp_path) -> None:
    db_path = tmp_path / "trace.sqlite"
    substrate = CountingSubstrate()
    registry = SubstrateRegistry()
    registry.register(substrate)
    with SQLiteTraceStore(db_path) as store:
        declaration = store.append(
            APPEND,
            AppendBatch(
                append_intent_id="intent:count-declaration",
                groups=(
                    AppendGroup(
                        trace_owner_id="owner:count",
                        retained_context=RetainedContextDraft(substrate_ref=substrate.substrate_ref),
                        fact_drafts=(
                            FactDraft(
                                mode="declaration",
                                schema_ref="example.write.v1",
                                kind_label="write",
                                payload={"value": 1},
                            ),
                        ),
                    ),
                ),
            ),
        )
        request = MaterializationRequest(
            append_intent_id="intent:count-apply",
            target_trace_owner_id="owner:count",
            target_record_ids=declaration.fact_ids,
        )

        first = materialize(store, MATERIALIZE, request, registry)
        second = materialize(store, MATERIALIZE, request, registry)

    with SQLiteTraceStore(db_path) as restarted:
        third = materialize(restarted, MATERIALIZE, request, registry)

    assert first == second == third
    assert substrate.calls == 1


def test_materialize_intent_conflict_is_rejected_before_substrate_dispatch() -> None:
    store = SQLiteTraceStore()
    substrate = CountingSubstrate()
    registry = SubstrateRegistry()
    registry.register(substrate)
    first = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:count-conflict:first",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:count-conflict",
                    retained_context=RetainedContextDraft(substrate_ref=substrate.substrate_ref),
                    fact_drafts=(FactDraft(mode="declaration", schema_ref="example.write.v1", payload={"value": 1}),),
                ),
            ),
        ),
    )
    second = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:count-conflict:second",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:count-conflict",
                    retained_context=RetainedContextDraft(substrate_ref=substrate.substrate_ref),
                    fact_drafts=(FactDraft(mode="declaration", schema_ref="example.write.v1", payload={"value": 2}),),
                ),
            ),
        ),
    )

    materialize(
        store,
        MATERIALIZE,
        MaterializationRequest(
            append_intent_id="intent:count-conflict:apply",
            target_trace_owner_id="owner:count-conflict",
            target_record_ids=first.fact_ids,
        ),
        registry,
    )

    with pytest.raises(TraceStoreError, match="different content"):
        materialize(
            store,
            MATERIALIZE,
            MaterializationRequest(
                append_intent_id="intent:count-conflict:apply",
                target_trace_owner_id="owner:count-conflict",
                target_record_ids=second.fact_ids,
            ),
            registry,
        )

    assert substrate.calls == 1


def test_materialize_targets_explicit_owner_path_for_shared_records() -> None:
    store = SQLiteTraceStore()
    registry = SubstrateRegistry()
    registry.register(EchoSubstrate("test.echo.v1", frozenset({"example.write.v1"})))
    draft = FactDraft(
        mode="declaration",
        schema_ref="example.write.v1",
        kind_label="write",
        payload={"path": "note.txt", "text": "hello"},
    )
    context = RetainedContextDraft(substrate_ref="test.echo.v1", containment="contained")
    first = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:shared:first",
            groups=(AppendGroup(trace_owner_id="owner:z", retained_context=context, fact_drafts=(draft,)),),
        ),
    )
    second = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:shared:second",
            groups=(AppendGroup(trace_owner_id="owner:a", retained_context=context, fact_drafts=(draft,)),),
        ),
    )
    assert first.fact_ids == second.fact_ids

    receipt = materialize(
        store,
        MATERIALIZE,
        MaterializationRequest(
            append_intent_id="intent:shared:apply",
            target_trace_owner_id="owner:z",
            target_record_ids=first.fact_ids,
        ),
        registry,
    )

    assert receipt.produced_record_ids[0] in store.read_owner_prefix(READ, "owner:z", 99).fact_ids()
    assert receipt.produced_record_ids[0] not in store.read_owner_prefix(READ, "owner:a", 99).fact_ids()


def test_materialize_rejects_record_missing_from_target_owner_path() -> None:
    store = SQLiteTraceStore()
    registry = SubstrateRegistry()
    registry.register(EchoSubstrate("test.echo.v1", frozenset({"example.write.v1"})))
    declaration = store.append(
        APPEND,
        AppendBatch(
            append_intent_id="intent:wrong-owner",
            groups=(
                AppendGroup(
                    trace_owner_id="owner:right",
                    retained_context=RetainedContextDraft(substrate_ref="test.echo.v1"),
                    fact_drafts=(FactDraft(mode="declaration", schema_ref="example.write.v1", payload={"value": 1}),),
                ),
            ),
        ),
    )

    with pytest.raises(TraceStoreError, match="not present on owner path"):
        materialize(
            store,
            MATERIALIZE,
            MaterializationRequest(
                append_intent_id="intent:wrong-owner:apply",
                target_trace_owner_id="owner:wrong",
                target_record_ids=declaration.fact_ids,
            ),
            registry,
        )
