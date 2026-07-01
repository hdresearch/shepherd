from shepherd_kernel_v3_reference.semantic import (
    ContinuationSource,
    OneShotKey,
    SourceGeneration,
    semantic_transition_batch_to_json,
)
from shepherd_kernel_v3_reference.spikes.mock_carrier import MockCarrierEvidence, build_mock_resume_batch


def test_mock_carrier_batch_links_evidence_to_resume_admission() -> None:
    source = ContinuationSource(
        source_ref="source:unhandled:0",
        source_kind="UnhandledSuspension",
        source_generation=SourceGeneration(0),
        continuation_ref="kont:root",
        branch_ref="branch:root",
        one_shot_key=OneShotKey("oneshot:source:unhandled:0"),
        declaration_ref="record:declaration:0",
        source_path_ref="path:unhandled/source:unhandled:0/branch:root",
        operation_result_schema_ref="schema:shell-result",
        worker_context_ref="ctx:worker",
    )
    evidence = MockCarrierEvidence(
        external_ref="mock-operation:0",
        effect_kind="shell.exec",
        payload={"argv": ["printf", "ok"]},
        result={"exit_code": 0, "stdout": "ok"},
    )

    batch = build_mock_resume_batch(
        program_ref="program:demo",
        parent_transition_ref="transition:initial",
        declaration_record_ref="record:declaration:0",
        source=source,
        evidence=evidence,
    )
    encoded = semantic_transition_batch_to_json(batch)

    assert encoded["transition_kind"] == "unhandled_top_level_resume"
    assert encoded["external_evidence_links"][0]["external_system_kind"] == "mock"
    assert encoded["external_evidence_links"][0]["relation"] == "fulfilled_by"
    assert encoded["external_evidence_links"][1]["relation"] == "produced_resume_input"
    assert encoded["external_evidence_links"][1]["semantic_record_ref"] == ("record:mock-resume:0")
    assert encoded["external_evidence_links"][1]["evidence_digest"] == evidence.digest
    assert encoded["admission_basis"]["input_value_or_digest"] == evidence.digest
    assert encoded["records"][0]["value_digest"] == evidence.digest
