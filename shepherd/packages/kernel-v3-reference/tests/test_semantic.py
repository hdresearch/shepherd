import pytest

from shepherd_kernel_v3_reference.kernel.continuation_objects import (
    BindFramePayload,
    ContinuationEnvEmpty,
    ContinuationObjectBuilder,
    ContinuationRoot,
    continuation_object_ref,
    continuation_object_to_json,
)
from shepherd_kernel_v3_reference.profiles import CORE_A, PUBLICATION_EXPERIMENTAL
from shepherd_kernel_v3_reference.semantic import (
    AdmissionBasis,
    CanonicalRefMap,
    ContinuationSource,
    ExternalEvidenceLink,
    ObservedFrontier,
    OneShotKey,
    SemanticTransitionBatch,
    SemanticTransitionBatchValidationError,
    SourceGeneration,
    admission_basis_from_source,
    build_admitted_transition_batch,
    build_initial_transition_batch,
    fork_branch_source_from_record,
    pending_source_from_record,
    semantic_transition_batch_to_json,
    unhandled_suspension_source_from_declaration,
)
from shepherd_kernel_v3_reference.trace.records import ContinuationPending, EffectDeclaration, ForkBranch

_EMPTY_ENV_REF = continuation_object_ref(ContinuationEnvEmpty())


def test_unhandled_suspension_source_is_selection_free() -> None:
    source = ContinuationSource(
        source_ref="source:unhandled:1",
        source_kind="UnhandledSuspension",
        source_generation=SourceGeneration(0),
        continuation_ref="kont:root",
        branch_ref="branch:root",
        one_shot_key=OneShotKey("oneshot:unhandled:1"),
        declaration_ref="decl:1",
        source_path_ref="path:unhandled/source:unhandled:1/branch:root",
        operation_result_schema_ref="schema:result",
        worker_context_ref="ctx:worker",
    )

    assert source.selection_ref is None
    assert source.profile == CORE_A


def test_selected_sources_must_cite_selected_handler() -> None:
    with pytest.raises(ValueError, match="must cite a selected handler"):
        ContinuationSource(
            source_ref="source:handle:1",
            source_kind="ResumptionHandle",
            source_generation=SourceGeneration(0),
            continuation_ref="kont:resume",
            branch_ref="branch:root",
            one_shot_key=OneShotKey("oneshot:handle:1"),
        )


def test_transition_batch_requires_admission_after_initial_run() -> None:
    with pytest.raises(ValueError, match="requires an explicit AdmissionBasis"):
        SemanticTransitionBatch(
            transition_id="transition:resume:1",
            idempotency_key="idem:resume:1",
            transition_kind="callable_resume",
            admission_basis=None,
            profile=CORE_A,
            program_ref="program:1",
            parent_transition_refs=("transition:initial",),
            records=(),
            ref_map=CanonicalRefMap(),
        )


def test_admitted_unhandled_resume_transition_uses_source_basis() -> None:
    declaration = EffectDeclaration(
        ref="declaration:1",
        program_ref="program:1",
        effect_kind="eff.unhandled",
        payload="payload",
        full_continuation_ref="kont:full",
        branch_ref="branch:root",
        payload_schema_ref=None,
        operation_result_schema_ref="schema:result",
        execution_context_ref="ctx:worker",
    )
    source = unhandled_suspension_source_from_declaration(declaration)
    admission = admission_basis_from_source(
        source,
        observed_frontier=ObservedFrontier(("declaration:1",)),
        input_value_or_digest="resume-value",
        program_ref="program:1",
    )

    batch = build_admitted_transition_batch(
        program_ref="program:1",
        transition_id="transition:resume:1",
        transition_kind="unhandled_top_level_resume",
        admission_basis=admission,
        parent_transition_refs=("transition:initial",),
        records=(),
        ref_map=CanonicalRefMap(),
    )

    assert batch.admission_basis == admission
    assert admission.source_kind == "UnhandledSuspension"
    assert admission.source_path_ref == "path:unhandled/declaration:1/branch:root"
    assert admission.one_shot_key == source.one_shot_key


def test_admitted_terminal_pending_resume_transition_uses_source_basis() -> None:
    pending = ContinuationPending(
        ref="pending:1",
        declaration_ref="declaration:1",
        selection_ref="selection:1",
        selection_path_ref="path:selection:1",
        continuation_ref="kont:pending",
        operation_result_schema_ref="schema:result",
        branch_ref="branch:root",
        reason="waiting",
        worker_context_ref="ctx:worker",
    )
    source = pending_source_from_record(pending, profile=PUBLICATION_EXPERIMENTAL)
    admission = admission_basis_from_source(
        source,
        observed_frontier=ObservedFrontier(("pending:1",)),
        input_value_or_digest="resume-value",
        program_ref="program:1",
    )

    batch = build_admitted_transition_batch(
        program_ref="program:1",
        transition_id="transition:pending-resume:1",
        transition_kind="pending_resume",
        admission_basis=admission,
        parent_transition_refs=("transition:initial",),
        records=(),
        ref_map=CanonicalRefMap(),
        profile=PUBLICATION_EXPERIMENTAL,
    )

    assert batch.profile == PUBLICATION_EXPERIMENTAL
    assert admission.source_kind == "ContinuationPending"
    assert admission.source_path_ref == "path:selection:1/pending:1/branch:root"


def test_admitted_fork_branch_resume_transition_uses_source_basis() -> None:
    branch = ForkBranch(
        ref="fork-branch:1",
        fork_ref="fork:1",
        declaration_ref="declaration:1",
        selection_ref="selection:1",
        selection_path_ref="path:selection:1",
        branch_ref="branch:A",
        continuation_ref="kont:captured",
        terminal_continuation_ref="kont:terminal",
        value="branch-value",
    )
    source = fork_branch_source_from_record(branch, profile=PUBLICATION_EXPERIMENTAL)
    admission = admission_basis_from_source(
        source,
        observed_frontier=ObservedFrontier(("fork-branch:1",)),
        input_value_or_digest="resume-value",
        program_ref="program:1",
    )

    batch = build_admitted_transition_batch(
        program_ref="program:1",
        transition_id="transition:fork-branch-resume:1",
        transition_kind="fork_branch_resume",
        admission_basis=admission,
        parent_transition_refs=("transition:initial",),
        records=(),
        ref_map=CanonicalRefMap(),
        profile=PUBLICATION_EXPERIMENTAL,
    )

    assert batch.profile == PUBLICATION_EXPERIMENTAL
    assert admission.source_kind == "ForkBranch"
    assert admission.source_path_ref == "path:selection:1/fork-branch:1/branch:A"
    assert source.restart_continuation_ref == "kont:terminal"


def test_transition_batch_keeps_schema_versions_and_evidence_links() -> None:
    evidence = ExternalEvidenceLink(
        semantic_record_ref="record:decl:1",
        relation="produced_resume_input",
        external_system_kind="mock",
        external_ref="mock:op:1",
        external_schema_ref="mock.schema.v0",
        evidence_digest="sha256:abc",
        external_status="completed",
    )
    admission = AdmissionBasis(
        source_ref="source:unhandled:1",
        source_kind="UnhandledSuspension",
        source_generation=SourceGeneration(0),
        observed_frontier=ObservedFrontier(("record:decl:1",)),
        source_path_ref="path:unhandled/source:unhandled:1/branch:root",
        input_value_or_digest="sha256:abc",
        idempotency_key="idem:resume:1",
        one_shot_key=OneShotKey("oneshot:unhandled:1"),
        profile=CORE_A,
        program_ref="program:1",
        external_evidence_refs_or_digests=("sha256:abc",),
    )

    batch = SemanticTransitionBatch(
        transition_id="transition:resume:1",
        idempotency_key="idem:resume:1",
        transition_kind="unhandled_top_level_resume",
        admission_basis=admission,
        profile=CORE_A,
        program_ref="program:1",
        parent_transition_refs=("transition:initial",),
        records=({"ref": "record:resume:1"},),
        ref_map=CanonicalRefMap(),
        external_evidence_links=(evidence,),
    )

    encoded = semantic_transition_batch_to_json(batch)

    assert encoded["batch_schema_version"]
    assert encoded["kernel_version"]
    assert encoded["admission_basis"]["external_evidence_refs_or_digests"] == [
        "sha256:abc",
    ]
    assert encoded["external_evidence_links"][0]["external_system_kind"] == "mock"


def test_transition_batch_rejects_non_json_record_payloads() -> None:
    with pytest.raises(TypeError, match="non-string mapping key"):
        SemanticTransitionBatch(
            transition_id="transition:initial:1",
            idempotency_key="idem:initial:1",
            transition_kind="initial_run_prefix",
            admission_basis=None,
            profile=CORE_A,
            program_ref="program:1",
            parent_transition_refs=(),
            records=({1: "bad-key"},),
            ref_map=CanonicalRefMap(),
        )


def test_transition_batch_validates_continuation_object_catalog() -> None:
    root_ref, root_json = _root_object(program_ref="program:1")
    _extra_ref, extra_json = _root_object(program_ref="program:1", continuation_kind="outer")

    batch = SemanticTransitionBatch(
        transition_id="transition:initial:1",
        idempotency_key="idem:initial:1",
        transition_kind="initial_run_prefix",
        admission_basis=None,
        profile=CORE_A,
        program_ref="program:1",
        parent_transition_refs=(),
        records=(
            {
                "ref": "declaration:0",
                "record_type": "EffectDeclaration",
                "full_continuation_ref": root_ref,
            },
        ),
        ref_map=CanonicalRefMap(),
        continuation_objects=(
            root_json,
            extra_json,
        ),
    )

    assert batch.continuation_objects[0]["object_type"] == "root"


def test_transition_batch_rejects_invalid_continuation_object_payload() -> None:
    _root_ref, root_json = _root_object(program_ref="program:1")
    root_json["object_schema_version"] = "shepherd_kernel_v3_reference.continuation-object.v999"

    with pytest.raises(SemanticTransitionBatchValidationError, match="invalid"):
        SemanticTransitionBatch(
            transition_id="transition:initial:1",
            idempotency_key="idem:initial:1",
            transition_kind="initial_run_prefix",
            admission_basis=None,
            profile=CORE_A,
            program_ref="program:1",
            parent_transition_refs=(),
            records=(),
            ref_map=CanonicalRefMap(),
            continuation_objects=(root_json,),
        )


def test_transition_batch_rejects_duplicate_continuation_object_refs() -> None:
    _root_ref, root_json = _root_object(program_ref="program:1")

    with pytest.raises(SemanticTransitionBatchValidationError, match="duplicate"):
        SemanticTransitionBatch(
            transition_id="transition:initial:1",
            idempotency_key="idem:initial:1",
            transition_kind="initial_run_prefix",
            admission_basis=None,
            profile=CORE_A,
            program_ref="program:1",
            parent_transition_refs=(),
            records=(),
            ref_map=CanonicalRefMap(),
            continuation_objects=(root_json, root_json),
        )


def test_transition_batch_rejects_object_program_mismatch() -> None:
    _root_ref, root_json = _root_object(program_ref="program:other")

    with pytest.raises(SemanticTransitionBatchValidationError, match="program_ref"):
        SemanticTransitionBatch(
            transition_id="transition:initial:1",
            idempotency_key="idem:initial:1",
            transition_kind="initial_run_prefix",
            admission_basis=None,
            profile=CORE_A,
            program_ref="program:1",
            parent_transition_refs=(),
            records=(),
            ref_map=CanonicalRefMap(),
            continuation_objects=(root_json,),
        )


def test_transition_batch_requires_objects_for_cited_continuation_refs() -> None:
    root_ref, _root_json = _root_object(program_ref="program:1")

    with pytest.raises(SemanticTransitionBatchValidationError, match="missing"):
        SemanticTransitionBatch(
            transition_id="transition:initial:1",
            idempotency_key="idem:initial:1",
            transition_kind="initial_run_prefix",
            admission_basis=None,
            profile=CORE_A,
            program_ref="program:1",
            parent_transition_refs=(),
            records=(
                {
                    "record_type": "ContinuationResume",
                    "ref": "resume:0",
                    "handler_continuation_ref": root_ref,
                },
            ),
            ref_map=CanonicalRefMap(),
        )


def test_transition_batch_ignores_domain_payload_continuation_ref_keys() -> None:
    SemanticTransitionBatch(
        transition_id="transition:initial:1",
        idempotency_key="idem:initial:1",
        transition_kind="initial_run_prefix",
        admission_basis=None,
        profile=CORE_A,
        program_ref="program:1",
        parent_transition_refs=(),
        records=(
            {
                "record_type": "EffectDeclaration",
                "ref": "declaration:0",
                "payload": {
                    "continuation_ref": "continuation-image:domain-value",
                },
            },
        ),
        ref_map=CanonicalRefMap(),
    )


def test_transition_batch_ignores_control_refs_for_image_coverage() -> None:
    SemanticTransitionBatch(
        transition_id="transition:initial:1",
        idempotency_key="idem:initial:1",
        transition_kind="initial_run_prefix",
        admission_basis=None,
        profile=CORE_A,
        program_ref="program:1",
        parent_transition_refs=(),
        records=(
            {
                "record_type": "HandlerSelection",
                "ref": "selection:0",
                "captured_continuation_control_ref": "continuation-control:abc",
                "outer_continuation_control_ref": "continuation-control:def",
            },
        ),
        ref_map=CanonicalRefMap(),
    )


def test_transition_batch_ignores_unknown_record_shapes_for_image_coverage() -> None:
    SemanticTransitionBatch(
        transition_id="transition:initial:1",
        idempotency_key="idem:initial:1",
        transition_kind="initial_run_prefix",
        admission_basis=None,
        profile=CORE_A,
        program_ref="program:1",
        parent_transition_refs=(),
        records=(
            {
                "record_type": "ExternalDomainRecord",
                "ref": "external:0",
                "continuation_ref": "continuation-image:domain-value",
            },
            {
                "ref": "domain:0",
                "continuation_ref": "continuation-image:no-record-type",
            },
        ),
        ref_map=CanonicalRefMap(),
    )


def test_trace_record_helpers_build_continuation_sources() -> None:
    declaration = EffectDeclaration(
        ref="declaration:0",
        program_ref="program:1",
        effect_kind="eff.a",
        payload=None,
        full_continuation_ref="kont:root",
        branch_ref="branch:root",
        payload_schema_ref=None,
        operation_result_schema_ref="schema:result",
        execution_context_ref="ctx:worker",
    )
    pending = ContinuationPending(
        ref="pending:0",
        declaration_ref="declaration:0",
        selection_ref="selection:0",
        selection_path_ref="path:selection:0/resumption:0/branch:root",
        continuation_ref="kont:captured",
        operation_result_schema_ref="schema:result",
        branch_ref="branch:root",
        reason="waiting",
        worker_context_ref="ctx:worker",
    )
    fork_branch = ForkBranch(
        ref="fork-branch:0",
        fork_ref="fork:0",
        declaration_ref="declaration:0",
        selection_ref="selection:0",
        selection_path_ref="path:selection:0/resumption:0/branch:root",
        branch_ref="branch:A",
        continuation_ref="kont:captured",
        value="A",
        terminal_continuation_ref="kont:restart",
    )

    unhandled = unhandled_suspension_source_from_declaration(declaration)
    pending_source = pending_source_from_record(pending)
    fork_source = fork_branch_source_from_record(fork_branch)

    assert unhandled.source_kind == "UnhandledSuspension"
    assert unhandled.selection_ref is None
    assert unhandled.continuation_ref == "kont:root"
    assert unhandled.selected_path_ref is None
    assert unhandled.source_path_ref == "path:unhandled/declaration:0/branch:root"
    assert pending_source.source_kind == "ContinuationPending"
    assert pending_source.selection_ref == "selection:0"
    assert pending_source.selected_path_ref == ("path:selection:0/resumption:0/branch:root")
    assert pending_source.source_path_ref == "path:selection:0/pending:0/branch:root"
    assert pending_source.one_shot_key.value == "oneshot:pending:0:0"
    assert fork_source.source_kind == "ForkBranch"
    assert fork_source.continuation_ref == "kont:captured"
    assert fork_source.restart_continuation_ref == "kont:restart"
    assert fork_source.source_path_ref == "path:selection:0/fork-branch:0/branch:A"


def test_transition_batch_builders_encode_initial_and_admitted_batches() -> None:
    initial = build_initial_transition_batch(
        program_ref="program:1",
        transition_id="transition:initial:0",
        records=({"ref": "declaration:0"},),
        ref_map=CanonicalRefMap(),
    )
    admission = AdmissionBasis(
        source_ref="pending:0",
        source_kind="ContinuationPending",
        source_generation=SourceGeneration(0),
        observed_frontier=ObservedFrontier(("pending:0",)),
        source_path_ref="path:selection:0/pending:0/branch:root",
        input_value_or_digest={"value": "ok"},
        idempotency_key="idem:pending:0",
        one_shot_key=OneShotKey("oneshot:pending:0"),
        profile=CORE_A,
        program_ref="program:1",
    )
    admitted = build_admitted_transition_batch(
        program_ref="program:1",
        transition_id="transition:pending:0",
        transition_kind="pending_resume",
        admission_basis=admission,
        parent_transition_refs=(initial.transition_id,),
        records=({"ref": "resume:0", "value": "ok"},),
        ref_map=CanonicalRefMap(),
    )

    assert semantic_transition_batch_to_json(initial)["transition_kind"] == ("initial_run_prefix")
    assert admitted.idempotency_key == "idem:pending:0"


def test_transition_batch_rejects_profile_and_program_mismatches() -> None:
    admission = AdmissionBasis(
        source_ref="pending:0",
        source_kind="ContinuationPending",
        source_generation=SourceGeneration(0),
        observed_frontier=ObservedFrontier(("pending:0",)),
        source_path_ref="path:selection:0/pending:0/branch:root",
        input_value_or_digest="sha256:abc",
        idempotency_key="idem:pending:0",
        one_shot_key=OneShotKey("oneshot:pending:0"),
        profile=PUBLICATION_EXPERIMENTAL,
        program_ref="program:1",
    )

    with pytest.raises(ValueError, match="profile"):
        build_admitted_transition_batch(
            program_ref="program:1",
            transition_id="transition:pending:0",
            transition_kind="pending_resume",
            admission_basis=admission,
            parent_transition_refs=("transition:initial:0",),
            records=(),
            ref_map=CanonicalRefMap(),
            profile=CORE_A,
        )

    with pytest.raises(ValueError, match="program_ref"):
        build_admitted_transition_batch(
            program_ref="program:wrong",
            transition_id="transition:pending:0",
            transition_kind="pending_resume",
            admission_basis=admission,
            parent_transition_refs=("transition:initial:0",),
            records=(),
            ref_map=CanonicalRefMap(),
        )


def _root_object(**overrides) -> tuple[str, dict[str, object]]:
    builder = ContinuationObjectBuilder()
    kwargs = {
        "stack": builder.empty_stack,
        "program_ref": "program:1",
        "branch_ref": "branch:root",
        "branch_scope_ref": None,
        "continuation_kind": "full",
        "execution_context_ref": "ctx:test",
        "execution_context": {
            "binding_env_ref": _EMPTY_ENV_REF,
            "region_ref": "region:root",
            "authority_ref": "authority:root",
        },
        "result_schema_ref": None,
    }
    kwargs.update(overrides)
    ref = builder.put_root(**kwargs)
    root = builder.store.get(ref)
    assert isinstance(root, ContinuationRoot)
    return ref, continuation_object_to_json(root)


def _frame_object() -> tuple[str, dict[str, object]]:
    builder = ContinuationObjectBuilder()
    ref = builder.put_frame(
        BindFramePayload(
            binder_ref="binder:0",
            env_ref=builder.empty_env_ref,
            context_ref="ctx:0",
            context={
                "binding_env_ref": builder.empty_env_ref,
                "region_ref": "region:root",
                "authority_ref": "authority:root",
            },
        )
    )
    return ref, continuation_object_to_json(builder.store.get(ref))
