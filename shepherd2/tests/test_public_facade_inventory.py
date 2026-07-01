from __future__ import annotations

import shepherd2

# Current root exports that are intentionally broader than the retained-record
# ABI. Keep this inventory explicit until the facade strategy thread decides
# whether these stay public or move behind owner modules.
CURRENT_RUNTIME_EXPORTS = {
    "ChildHandle",
    "Run",
    "TaskControl",
    "task",
}

CURRENT_VNEXT_EXPORTS = {
    "EchoSubstrate",
    "KV_PUT_CAPTURE_SCHEMA",
    "KV_PUT_DECLARATION_SCHEMA",
    "KV_SQLITE_SUBSTRATE_REF",
    "MaterializationOutcome",
    "MaterializationReceipt",
    "MaterializationRequest",
    "MaterializationResult",
    "SQLiteKVSubstrate",
    "Substrate",
    "SubstrateError",
    "SubstrateRegistry",
    "UnknownSubstrateError",
    "materialize",
}

CURRENT_TRACE_STORE_IMPL_EXPORTS = {
    "AppendIntentConflict",
    "AppendIntentConflictError",
    "SQLiteTraceStore",
    "TraceStoreError",
    "UnknownFact",
    "UnknownFactError",
}

CURRENT_SCHEMA_PROJECTION_EXPORTS = {
    "EffectiveChild",
    "EffectiveHistory",
    "Execution",
    "ExecutionRelation",
    "ProjectionModeError",
    "ProjectionModeRequirement",
    "ProjectionSpec",
    "PublishedFact",
    "RelationId",
    "SchemaLibrary",
    "StaticSchemaLibrary",
    "complete_execution_batch",
    "create_execution_batch",
    "create_execution_relation_batch",
    "ensure_projection_compatible",
    "execution_completed",
    "execution_created",
    "execution_failed",
    "execution_id_for",
    "execution_relation_created",
    "execution_relation_from_fact",
    "execution_started",
    "fail_execution_batch",
    "project_effective_history",
    "project_effective_history_from_store",
    "project_execution",
    "project_execution_from_store",
    "project_execution_relations",
    "project_execution_relations_from_store",
    "project_execution_relations_slice",
    "project_execution_slice",
    "publish_execution_frontier",
    "relation_id_for",
}

CURRENT_FACT_COMPAT_EXPORTS = {
    "Fact",
    "FactBody",
    "FactDraft",
    "FactEnvelope",
    "FactId",
    "FactShape",
    "FactView",
}

INTENDED_RETAINED_RECORD_FACADE_TARGET = {
    "ABI_VERSION",
    "CANONICAL_VERSION",
    "ROOT_WITNESS_REF",
    "ROOT_WITNESS_SCHEMA_REF",
    "TRUSTED_APPEND_CONTEXT",
    "TRUSTED_READ_CONTEXT",
    "WITNESS_SCHEMA_REF",
    "AppendBatch",
    "AppendContext",
    "AppendGroup",
    "AppendLocalId",
    "AppendReceipt",
    "CausalClosure",
    "ContextAnchor",
    "Cut",
    "CutId",
    "CutSelector",
    "CutSpec",
    "ExternalAnchor",
    "ModeFilter",
    "OperationContext",
    "OperationKind",
    "OwnerCutoff",
    "OwnerCutoffSpec",
    "PathPrefix",
    "ReadContext",
    "Record",
    "RecordBody",
    "RecordDraft",
    "RecordEnvelope",
    "RecordId",
    "RecordShape",
    "RecordView",
    "RetainedContext",
    "RetainedContextDraft",
    "TraceOwnerId",
    "TraceSlice",
    "TraceStore",
    "VisibilityProfile",
    "VisibleFact",
    "VisibleRecord",
    "WitnessAnchor",
    "WitnessBody",
    "WitnessDraft",
    "canonical_digest",
    "canonical_json_bytes",
    "canonical_record_input",
    "canonical_witness_input",
    "record_digest",
    "root_witness_body",
    "root_witness_body_digest",
    "root_witness_record_id",
    "validate_witness_body",
    "witness_body_digest",
}

CURRENT_ROOT_EXPORTS = (
    INTENDED_RETAINED_RECORD_FACADE_TARGET
    | CURRENT_RUNTIME_EXPORTS
    | CURRENT_VNEXT_EXPORTS
    | CURRENT_TRACE_STORE_IMPL_EXPORTS
    | CURRENT_SCHEMA_PROJECTION_EXPORTS
    | CURRENT_FACT_COMPAT_EXPORTS
)


def _root_exports() -> set[str]:
    return set(shepherd2.__all__)


def test_root_facade_matches_current_inventory_exactly() -> None:
    assert _root_exports() == CURRENT_ROOT_EXPORTS


def test_retained_record_facade_target_is_available_but_not_current_shape() -> None:
    exports = _root_exports()
    non_abi_exports = (
        CURRENT_RUNTIME_EXPORTS
        | CURRENT_VNEXT_EXPORTS
        | CURRENT_TRACE_STORE_IMPL_EXPORTS
        | CURRENT_SCHEMA_PROJECTION_EXPORTS
        | CURRENT_FACT_COMPAT_EXPORTS
    )

    assert exports >= INTENDED_RETAINED_RECORD_FACADE_TARGET
    assert non_abi_exports <= exports
    assert INTENDED_RETAINED_RECORD_FACADE_TARGET.isdisjoint(CURRENT_RUNTIME_EXPORTS | CURRENT_VNEXT_EXPORTS)
