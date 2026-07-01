import pytest

from shepherd_kernel_v3_reference.kernel import __all__ as kernel_all
from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.continuation_objects import ContinuationObjectBuilder
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace import __all__ as trace_all
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.validate import validate_runtime_trace


def test_trace_public_api_excludes_conformance_evidence_helpers() -> None:
    assert "TraceEvidenceBundle" not in trace_all
    assert "TRACE_EVIDENCE_BUNDLE_SCHEMA_VERSION" not in trace_all
    assert "validate_trace_evidence" not in trace_all
    assert "TraceDebugEvidence" in trace_all


def test_kernel_public_api_excludes_runtime_evidence_internals() -> None:
    assert set(kernel_all) == {
        "CONTINUATION_REPLAY_ARTIFACT_SCHEMA_VERSION",
        "CONTINUATION_SOURCE_KEY_SCHEMA_VERSION",
        "EXTERNAL_EFFECT_REQUEST_SCHEMA_VERSION",
        "HOST_COMPLETED_SCHEMA_VERSION",
        "KERNEL_REPLAY_JOURNAL_SCHEMA_VERSION",
        "KERNEL_REPLAY_STATE_SCHEMA_VERSION",
        "REPLAYABLE_KERNEL_TRANSITION_SCHEMA_VERSION",
        "ContinuationReplayArtifact",
        "ContinuationReplayError",
        "ContinuationReplayLedger",
        "ContinuationReplaySerializationError",
        "Elaborator",
        "ExternalEffectRequest",
        "ExternalEffectRequestDescriptor",
        "ExternalEffectRequestRef",
        "HostCompleted",
        "KernelReplayJournal",
        "KernelProgram",
        "KernelProgramValidationError",
        "KernelReplayRejected",
        "KernelReplaySession",
        "KernelReplayState",
        "OpenReplayRequest",
        "PreparedKernelProgram",
        "ReplayArtifactCatalog",
        "ReplayableCompleted",
        "ReplayableExternalEffectRequest",
        "ReplayableKernelResult",
        "ReplayableKernelTransition",
        "ReplayableRejected",
        "continuation_replay_artifact_from_json",
        "continuation_replay_artifact_from_objects",
        "continuation_replay_artifact_to_json",
        "elaborate",
        "elaborate_publication_experimental",
        "external_effect_request_from_json",
        "external_effect_request_to_json",
        "host_completed_from_json",
        "host_completed_to_json",
        "kernel_replay_journal_current_request",
        "kernel_replay_journal_current_request_descriptor",
        "kernel_replay_journal_from_json",
        "kernel_replay_journal_to_json",
        "kernel_replay_state_from_journal",
        "kernel_replay_state_from_json",
        "kernel_replay_state_to_json",
        "admit_and_prepare",
        "prepare_kernel_program",
        "replayable_kernel_transition_from_json",
        "replayable_kernel_transition_to_json",
        "resume_external_effect_request",
        "resume_kernel_replay",
        "resume_kernel_replay_from_journal",
        "resume_continuation",
        "resume_replayable_kernel_transition",
        "run_kernel",
        "start_kernel_replay",
        "start_replayable_kernel_run",
        "start_replayable_kernel_transition",
        "validate_kernel_program",
    }


def test_run_trace_debug_evidence_is_opt_in() -> None:
    program = elaborate(Return(Lit("ok")))

    assert run_trace(program).debug_evidence is None
    assert run_trace(program, include_debug_evidence=True).debug_evidence is not None


def test_run_trace_debug_evidence_does_not_change_trace_refs() -> None:
    program = elaborate(
        Handle(
            Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="h.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )

    default_result = run_trace(program)
    debug_result = run_trace(program, include_debug_evidence=True)

    assert debug_result.trace == default_result.trace
    assert debug_result.require_debug_evidence().continuation_ref_map


def test_default_run_trace_does_not_project_debug_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked_put_root(self: ContinuationObjectBuilder, *args: object, **kwargs: object) -> str:
        raise AssertionError("default run_trace projected continuation-object evidence")

    monkeypatch.setattr(ContinuationObjectBuilder, "put_root", blocked_put_root)
    program = elaborate(
        Handle(
            Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="h.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )

    result = run_trace(program)

    assert result.debug_evidence is None
    assert result.trace
    validate_runtime_trace(result.trace)
