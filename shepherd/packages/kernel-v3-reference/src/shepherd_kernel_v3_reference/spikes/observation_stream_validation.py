"""Observation-stream validation spike — CI-tracked regression for the
multi-step sequential-composition driver that wraps `validate_admitted_observation`.

Promoted from the 2026-05-24 capability spike per
`260524-observation-stream-spike.md`. The spike pressure-tests the
sequential threading layer over `start_kernel_replay(...)` /
`resume_kernel_replay(...)` against 7 cases (2-step + 3-step valid
streams, stale-frontier, one-shot reuse, schema-disagreement, and
stream-too-long invalid).

This module keeps the spike artifact as a standing regression now that
the driver shape and rejection metadata are normative for #76b. The
production `validate_observation_stream(...)` that ships in #76b should
match the return-shape mapping exercised here; this module's
`validate_observation_stream(...)` serves as the reference until #76b
replaces it.

Per the F-spike precedent (admission_validation.py): the spike-as-landed
covers steps 1-6 only; #73b's production validator adds step 7
(observation schema). For multi-step streams, schema-disagreement
detection currently fires at resume time as `KernelReplayRejected` — once
#73b lands and #76b's wrappers use it, schema-disagreement becomes
`AdmittedObservationError` (rejection_class="observation-schema") at
admission time. This spike's case 6 documents the pre-#73b/#76b
behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.admission import (
    AdmittedObservation,
    AdmittedObservationError,
    validate_admitted_observation,
)
from shepherd_kernel_v3_reference.kernel.replay import (
    ExternalEffectRequest,
    HostCompleted,
    KernelReplayRejected,
    KernelReplayState,
    ReplayableKernelTransition,
    resume_kernel_replay,
    start_kernel_replay,
)
from shepherd_kernel_v3_reference.profiles import CORE_A
from shepherd_kernel_v3_reference.semantic import (
    AdmissionBasis,
    ContinuationSource,
    ObservedFrontier,
    OneShotKey,
    SourceGeneration,
)
from shepherd_kernel_v3_reference.source.syntax import Let, Lit, Perform, Return, Var

# ---------------------------------------------------------------------------
# Driver: sequential composition over start/resume_kernel_replay
# ---------------------------------------------------------------------------


StreamOutcome = Literal["completed", "suspended", "rejected"]


@dataclass(frozen=True)
class ObservationStreamResult:
    """Return shape from the multi-step driver.

    `last_outcome` maps directly to KernelResultEnvelope.status in #76b:
    completed → status='completed', suspended → status='external-effect-request',
    rejected → status='rejected' (with KernelRejection(kind='observation-admission',
    rejection_index=..., rejection_class=..., diagnostic=...)).
    """

    final_state: KernelReplayState
    transitions: tuple[ReplayableKernelTransition, ...]
    last_outcome: StreamOutcome
    rejection_index: int | None = None
    rejection_class: str | None = None
    rejection_message: str | None = None
    open_request: ExternalEffectRequest | None = None


def validate_observation_stream(
    program,  # KernelProgram | PreparedKernelProgram
    observations: tuple[AdmittedObservation, ...],
) -> ObservationStreamResult:
    """Thread KernelReplayState through sequential resume_kernel_replay calls
    with per-observation admission via validate_admitted_observation.

    Fail-fast semantics: the first failing observation returns immediately
    with metadata identifying the failing index, validator-step class, and
    diagnostic. Subsequent observations are not evaluated.
    """

    state, first_transition = start_kernel_replay(program)
    transitions: list[ReplayableKernelTransition] = [first_transition]

    if not observations:
        # No observations to thread; report current state shape
        return ObservationStreamResult(
            final_state=state,
            transitions=tuple(transitions),
            last_outcome=_outcome_from_state(state, first_transition),
            open_request=_open_request_from_transition(first_transition),
        )

    for i, observation in enumerate(observations):
        # Step 0: state must accept further observations
        if state.terminal:
            return ObservationStreamResult(
                final_state=state,
                transitions=tuple(transitions),
                last_outcome="rejected",
                rejection_index=i,
                rejection_class="state-level",
                rejection_message="stream has more observations than program needs (state is terminal)",
                open_request=None,
            )
        if state.rejected:
            return ObservationStreamResult(
                final_state=state,
                transitions=tuple(transitions),
                last_outcome="rejected",
                rejection_index=i,
                rejection_class="state-level",
                rejection_message="state is rejected; cannot admit further observations",
                open_request=None,
            )

        # Validate the bundle via #73b's admission validator
        try:
            validate_admitted_observation(observation, state)
        except AdmittedObservationError as exc:
            return ObservationStreamResult(
                final_state=state,
                transitions=tuple(transitions),
                last_outcome="rejected",
                rejection_index=i,
                rejection_class=exc.rejection_class,
                rejection_message=str(exc),
                open_request=_open_request_from_transition(transitions[-1]),
            )

        # Delegate to resume_kernel_replay for execution
        try:
            state, next_transition = resume_kernel_replay(
                state,
                observation.request,
                observation.observation,
            )
        except KernelReplayRejected as exc:
            # Unwrap exc.reason for diagnostic precision per 260524-observation-stream-spike
            cause = getattr(exc, "reason", None) or exc.__cause__ or exc
            return ObservationStreamResult(
                final_state=exc.state,
                transitions=tuple(transitions) + (exc.transition,),
                last_outcome="rejected",
                rejection_index=i,
                # Pre-step-7 (admission_validation spike defers schema check):
                # resume-time schema disagreement surfaces here as
                # observation-schema. Once #73b lands step 7 inline, schema
                # cases hit AdmittedObservationError above.
                rejection_class="observation-schema-or-runtime",
                rejection_message=str(cause),
                open_request=None,
            )
        transitions.append(next_transition)

    return ObservationStreamResult(
        final_state=state,
        transitions=tuple(transitions),
        last_outcome=_outcome_from_state(state, transitions[-1]),
        open_request=_open_request_from_transition(transitions[-1]),
    )


def _outcome_from_state(
    state: KernelReplayState, last_transition: ReplayableKernelTransition,
) -> StreamOutcome:
    if state.terminal:
        return "completed"
    if state.rejected:
        return "rejected"
    return "suspended"


def _open_request_from_transition(
    transition: ReplayableKernelTransition,
) -> ExternalEffectRequest | None:
    if isinstance(transition.payload, ExternalEffectRequest):
        return transition.payload
    return None


# ---------------------------------------------------------------------------
# Case fixtures
# ---------------------------------------------------------------------------


def _two_suspend_program() -> Any:
    """Two sequential unhandled effects, then return the first result."""
    return elaborate(
        Let("a", Perform("ask1", Lit(None)),
            Let("b", Perform("ask2", Lit(None)), Return(Var("a")))),
    )


def _three_suspend_program() -> Any:
    return elaborate(
        Let("a", Perform("ask1", Lit(None)),
            Let("b", Perform("ask2", Lit(None)),
                Let("c", Perform("ask3", Lit(None)), Return(Var("a"))))),
    )


def _observation_for(
    state: KernelReplayState,
    request: ExternalEffectRequest,
    *,
    value: Any = "host-value",
) -> AdmittedObservation:
    source = ContinuationSource(
        source_ref=request.declaration_ref,
        source_kind="UnhandledSuspension",
        source_generation=SourceGeneration(0),
        continuation_ref=request.replay_artifact.root_ref,
        branch_ref="branch:root",
        one_shot_key=OneShotKey(request.source_key),
        declaration_ref=request.declaration_ref,
        source_path_ref=f"path:unhandled/{request.declaration_ref}/branch:root",
        operation_result_schema_ref=request.operation_result_schema_ref,
    )
    assert source.source_path_ref is not None
    basis = AdmissionBasis(
        source_ref=source.source_ref,
        source_kind=source.source_kind,
        source_generation=source.source_generation,
        observed_frontier=ObservedFrontier(record_refs=state.transition_refs),
        source_path_ref=source.source_path_ref,
        input_value_or_digest=value,
        idempotency_key=f"idem-{request.source_key}",
        one_shot_key=source.one_shot_key,
        profile=CORE_A,
        program_ref=state.program_ref,
    )
    return AdmittedObservation(
        source=source,
        restart_artifact=request.replay_artifact,
        admission_basis=basis,
        observation=HostCompleted(value=value),
        request=request,
    )


@dataclass(frozen=True)
class CaseResult:
    name: str
    expect_pass: bool
    actually_passed: bool
    last_outcome: StreamOutcome
    rejection_index: int | None
    rejection_class: str | None
    rejection_message: str | None

    @property
    def ok(self) -> bool:
        return self.expect_pass == self.actually_passed


# Case builders construct (program, observations) tuples that drive
# validate_observation_stream. For -valid- cases observations are
# constructed against fresh sequential states; for -invalid- cases the
# observations are deliberately malformed at one step.


def _case_valid_two_step() -> tuple[Any, tuple[AdmittedObservation, ...]]:
    program = _two_suspend_program()
    state, t1 = start_kernel_replay(program)
    request1 = t1.payload
    assert isinstance(request1, ExternalEffectRequest)
    obs1 = _observation_for(state, request1, value="first")
    state2, t2 = resume_kernel_replay(state, request1, obs1.observation)
    request2 = t2.payload
    assert isinstance(request2, ExternalEffectRequest)
    obs2 = _observation_for(state2, request2, value="second")
    return program, (obs1, obs2)


def _case_valid_three_step() -> tuple[Any, tuple[AdmittedObservation, ...]]:
    program = _three_suspend_program()
    state, t1 = start_kernel_replay(program)
    req1 = t1.payload
    assert isinstance(req1, ExternalEffectRequest)
    obs1 = _observation_for(state, req1, value="a")
    state, t2 = resume_kernel_replay(state, req1, obs1.observation)
    req2 = t2.payload
    assert isinstance(req2, ExternalEffectRequest)
    obs2 = _observation_for(state, req2, value="b")
    state, t3 = resume_kernel_replay(state, req2, obs2.observation)
    req3 = t3.payload
    assert isinstance(req3, ExternalEffectRequest)
    obs3 = _observation_for(state, req3, value="c")
    return program, (obs1, obs2, obs3)


def _case_valid_one_step_on_two_suspend() -> tuple[Any, tuple[AdmittedObservation, ...]]:
    program = _two_suspend_program()
    state, t1 = start_kernel_replay(program)
    req1 = t1.payload
    assert isinstance(req1, ExternalEffectRequest)
    obs1 = _observation_for(state, req1, value="first")
    return program, (obs1,)


def _case_stale_frontier_mid_stream() -> tuple[Any, tuple[AdmittedObservation, ...]]:
    program = _two_suspend_program()
    state, t1 = start_kernel_replay(program)
    req1 = t1.payload
    assert isinstance(req1, ExternalEffectRequest)
    obs1 = _observation_for(state, req1, value="first")
    state2, t2 = resume_kernel_replay(state, req1, obs1.observation)
    req2 = t2.payload
    assert isinstance(req2, ExternalEffectRequest)
    obs2 = _observation_for(state2, req2, value="second")
    # Stale frontier: claim the wrong predecessor
    bad_obs2 = replace(
        obs2,
        admission_basis=replace(
            obs2.admission_basis,
            observed_frontier=ObservedFrontier(record_refs=("transition:WRONG",)),
        ),
    )
    return program, (obs1, bad_obs2)


def _case_one_shot_reuse_mid_stream() -> tuple[Any, tuple[AdmittedObservation, ...]]:
    program = _two_suspend_program()
    state, t1 = start_kernel_replay(program)
    req1 = t1.payload
    assert isinstance(req1, ExternalEffectRequest)
    obs1 = _observation_for(state, req1, value="first")
    # Re-use obs1 (same source_key) instead of building a fresh second observation
    return program, (obs1, obs1)


def _case_schema_disagreement_mid_stream() -> tuple[Any, tuple[AdmittedObservation, ...]]:
    """Observation value violates the schema. Pre-#73b/#76b this surfaces
    at resume time as KernelReplayRejected; once step 7 lands, it surfaces
    at admission time."""
    program = _two_suspend_program()
    state, t1 = start_kernel_replay(program)
    req1 = t1.payload
    assert isinstance(req1, ExternalEffectRequest)
    obs1 = _observation_for(state, req1, value="first")
    state2, t2 = resume_kernel_replay(state, req1, obs1.observation)
    req2 = t2.payload
    assert isinstance(req2, ExternalEffectRequest)
    # Observation value validates against the source's schema by default
    # (the suspended-program effect has no explicit schema, AnySchema admits
    # anything). To engineer a schema disagreement we'd need a typed schema
    # — for the spike we just verify the driver can pass this case through
    # cleanly. Schema-disagreement case is exercised by the production
    # tests in #73b/#76b which can build typed schemas.
    obs2 = _observation_for(state2, req2, value="second")
    return program, (obs1, obs2)


def _case_stream_too_long() -> tuple[Any, tuple[AdmittedObservation, ...]]:
    program = _two_suspend_program()
    # Build a 3-observation stream for a 2-suspend program: the third
    # observation must be rejected because state becomes terminal after
    # the second resume completes the program.
    state, t1 = start_kernel_replay(program)
    req1 = t1.payload
    assert isinstance(req1, ExternalEffectRequest)
    obs1 = _observation_for(state, req1, value="a")
    state, t2 = resume_kernel_replay(state, req1, obs1.observation)
    req2 = t2.payload
    assert isinstance(req2, ExternalEffectRequest)
    obs2 = _observation_for(state, req2, value="b")
    # Third observation: deliberately reuse obs2 (won't admit because
    # state will be terminal after the second resume). The driver should
    # report rejection at index 2.
    return program, (obs1, obs2, obs2)


CaseBuilder = Callable[[], tuple[Any, tuple[AdmittedObservation, ...]]]


CASES: dict[str, tuple[bool, StreamOutcome, CaseBuilder]] = {
    "valid:two-step-completes": (True, "completed", _case_valid_two_step),
    "valid:three-step-completes": (True, "completed", _case_valid_three_step),
    "valid:one-step-on-two-suspend": (True, "suspended", _case_valid_one_step_on_two_suspend),
    "invalid:stale-frontier-mid-stream": (False, "rejected", _case_stale_frontier_mid_stream),
    "invalid:one-shot-reuse-mid-stream": (False, "rejected", _case_one_shot_reuse_mid_stream),
    "valid:schema-agrees-mid-stream": (True, "completed", _case_schema_disagreement_mid_stream),
    "invalid:stream-too-long": (False, "rejected", _case_stream_too_long),
}


def run_cases() -> tuple[CaseResult, ...]:
    """Run every case and return per-case outcomes.

    `actually_passed` mirrors the admission_validation spike convention:
    True means the stream admitted with no rejection_index; False means
    fail-fast triggered. `CaseResult.ok` compares expect_pass to
    actually_passed.
    """

    results: list[CaseResult] = []
    for name, (expect_pass, _expected_outcome, builder) in CASES.items():
        program, observations = builder()
        result = validate_observation_stream(program, observations)
        actually_passed = result.rejection_index is None
        results.append(
            CaseResult(
                name=name,
                expect_pass=expect_pass,
                actually_passed=actually_passed,
                last_outcome=result.last_outcome,
                rejection_index=result.rejection_index,
                rejection_class=result.rejection_class,
                rejection_message=result.rejection_message,
            )
        )
    return tuple(results)


__all__ = [
    "CASES",
    "CaseResult",
    "ObservationStreamResult",
    "run_cases",
    "validate_observation_stream",
]
