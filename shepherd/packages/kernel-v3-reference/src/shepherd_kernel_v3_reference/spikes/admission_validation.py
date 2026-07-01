"""Admission validation spike — CI-tracked regression for the
`AdmittedObservation` bundle validator.

Promoted from the 2026-05-24 capability spike that pressure-tested the
candidate validator against a real synthetic `KernelReplayState` before
commit #73 lands. Per `260524-post-72-design-pass.md` §"Item F":
9/9 invariant cases behaved as expected; the bundle shape, check order,
and idempotency-via-source-key resolution were pinned.

This module keeps the spike artifact as a standing regression now that
the bundle and validator shape are normative for #73. The production
validator that ships in #73 should match the check order and diagnostic
shape exercised here; this module's `validate_admitted_observation(...)`
serves as the reference until #73 replaces it.

See `260521-0600-kernel.md` §"Validation Responsibilities" → "Admission
Validation" for the full normative spec.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.replay import (
    ExternalEffectRequest,
    HostCompleted,
    KernelReplayState,
    ReplayableKernelTransition,
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
from shepherd_kernel_v3_reference.source.syntax import Computation, Let, Lit, Perform, Return, Var


@dataclass(frozen=True)
class AdmittedObservation:
    """Bundle for resuming a kernel run via observed host completion.

    Per `260521-0600-kernel.md` §"API Shape" and 2026-05-24
    §"Post-#72 design pass" item F, the bundle carries `request` to bridge
    the identity gap between `request.source_key` (content-addressed; keyed
    in `state.open_requests`) and `source.source_ref` (trace-local).
    """

    source: ContinuationSource
    restart_artifact: Any  # ContinuationReplayArtifact
    admission_basis: AdmissionBasis
    observation: HostCompleted
    request: ExternalEffectRequest


class AdmittedObservationError(ValueError):
    """Raised when an AdmittedObservation does not validate against state."""


def _frontier_is_prefix(frontier: ObservedFrontier, state_transition_refs: tuple[str, ...]) -> bool:
    """Sequence-prefix relation: `observed.record_refs ⊑ state.transition_refs`.

    Per 2026-05-24 §"Post-#72 design pass" item F: sequence-prefix, NOT set
    inclusion. Empty frontier is admitted (initial run case).
    """

    if not frontier.record_refs:
        return True
    if len(frontier.record_refs) > len(state_transition_refs):
        return False
    return tuple(frontier.record_refs) == tuple(state_transition_refs[: len(frontier.record_refs)])


def validate_admitted_observation(
    observation: AdmittedObservation,
    state: KernelReplayState,
) -> None:
    """Validate an AdmittedObservation against the current replay state.

    Check order is cheapest-first; raises `AdmittedObservationError` on the
    first failure with a stable diagnostic.

      1. state-level: not terminal, not rejected
      2. source-open: observation.request.source_key open in state
      3. open-request agreement: program_ref + declaration_ref
      4. source ↔ admission_basis coherence
      5. frontier-prefix on state.transition_refs
      6. restart-artifact agreement: program_ref + source_ref + schema
      7. observation schema agreement (skipped if no schema ref)
    """

    src = observation.source
    basis = observation.admission_basis
    obs = observation.observation
    request = observation.request

    # 1. state-level
    if state.terminal:
        raise AdmittedObservationError("KernelReplayState is terminal; no admission possible")
    if state.rejected:
        raise AdmittedObservationError("KernelReplayState is rejected; no admission possible")

    # 2. source-open: one-shot + currently open
    if request.source_key in state.consumed_source_keys:
        raise AdmittedObservationError(
            f"source_key {request.source_key!r} already consumed (one-shot violation)"
        )
    open_request = state.open_requests.get(request.source_key)
    if open_request is None:
        raise AdmittedObservationError(
            f"source_key {request.source_key!r} is not open in KernelReplayState"
        )

    # 3. open-request agreement
    if open_request.program_ref != state.program_ref:
        raise AdmittedObservationError("OpenReplayRequest program_ref does not match state")
    if open_request.declaration_ref != src.declaration_ref:
        raise AdmittedObservationError(
            f"OpenReplayRequest.declaration_ref {open_request.declaration_ref!r} != "
            f"source.declaration_ref {src.declaration_ref!r}"
        )

    # 4. source ↔ admission_basis coherence
    if basis.source_ref != src.source_ref:
        raise AdmittedObservationError(
            f"AdmissionBasis.source_ref {basis.source_ref!r} != source.source_ref {src.source_ref!r}"
        )
    if basis.source_kind != src.source_kind:
        raise AdmittedObservationError("AdmissionBasis.source_kind != source.source_kind")
    if basis.source_generation != src.source_generation:
        raise AdmittedObservationError("AdmissionBasis.source_generation != source.source_generation")
    if basis.one_shot_key != src.one_shot_key:
        raise AdmittedObservationError("AdmissionBasis.one_shot_key != source.one_shot_key")
    if basis.program_ref != state.program_ref:
        raise AdmittedObservationError(
            f"AdmissionBasis.program_ref {basis.program_ref!r} != state.program_ref {state.program_ref!r}"
        )

    # 5. frontier-prefix
    if not _frontier_is_prefix(basis.observed_frontier, state.transition_refs):
        raise AdmittedObservationError(
            f"AdmissionBasis.observed_frontier is not a prefix of state.transition_refs "
            f"({list(basis.observed_frontier.record_refs)} vs {list(state.transition_refs)})"
        )

    # 6. restart-artifact agreement
    artifact = observation.restart_artifact
    if artifact.program_ref != state.program_ref:
        raise AdmittedObservationError(
            f"restart_artifact.program_ref {artifact.program_ref!r} != state.program_ref"
        )
    if artifact.source_ref != src.source_ref:
        raise AdmittedObservationError("restart_artifact.source_ref != source.source_ref")
    if artifact.operation_result_schema_ref != src.operation_result_schema_ref:
        raise AdmittedObservationError(
            "restart_artifact.operation_result_schema_ref != source.operation_result_schema_ref"
        )

    # 7. observation schema — production validator will resolve operation_result_schema_ref
    # against the program's schema catalog and call schema.validate(obs.value). For -lite,
    # schemas are (IntSchema, NullSchema, LiteralSchema(int)). The spike does not exercise
    # this check; production #73 covers it.
    _ = obs


# --- Cases --------------------------------------------------------------

def _suspended_program() -> Computation:
    return Let("y", Perform("ask", Lit(None)), Return(Var("y")))


def _setup_initial_state() -> tuple[KernelReplayState, ReplayableKernelTransition, ExternalEffectRequest]:
    program = elaborate(_suspended_program())
    state, transition = start_kernel_replay(program)
    request = transition.payload
    assert isinstance(request, ExternalEffectRequest)
    return state, transition, request


def _valid_observation(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
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
    assert source.source_path_ref is not None  # spike constructs it explicitly
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
    error_class: str | None
    error_message: str | None

    @property
    def ok(self) -> bool:
        return self.expect_pass == self.actually_passed


def _run_one(
    name: str,
    expect_pass: bool,
    observation: AdmittedObservation,
    state: KernelReplayState,
) -> CaseResult:
    try:
        validate_admitted_observation(observation, state)
        return CaseResult(
            name=name,
            expect_pass=expect_pass,
            actually_passed=True,
            error_class=None,
            error_message=None,
        )
    except AdmittedObservationError as exc:
        return CaseResult(
            name=name,
            expect_pass=expect_pass,
            actually_passed=False,
            error_class=type(exc).__name__,
            error_message=str(exc),
        )


CaseBuilder = Callable[
    [KernelReplayState, ReplayableKernelTransition, ExternalEffectRequest],
    tuple[AdmittedObservation, KernelReplayState],
]


_CaseReturn = tuple[AdmittedObservation, KernelReplayState]


def _case_valid(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    return _valid_observation(state, transition, request), state


def _case_source_already_consumed(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    consumed_state = replace(
        state,
        consumed_source_keys=(request.source_key,) + state.consumed_source_keys,
        open_requests={},
    )
    return _valid_observation(state, transition, request), consumed_state


def _case_source_ref_disagreement(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    obs = _valid_observation(state, transition, request)
    bad_basis = replace(obs.admission_basis, source_ref="source:NOT-MATCHING")
    return replace(obs, admission_basis=bad_basis), state


def _case_stale_source_generation(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    obs = _valid_observation(state, transition, request)
    stale_basis = replace(obs.admission_basis, source_generation=SourceGeneration(99))
    return replace(obs, admission_basis=stale_basis), state


def _case_frontier_too_long(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    obs = _valid_observation(state, transition, request)
    long_basis = replace(
        obs.admission_basis,
        observed_frontier=ObservedFrontier(
            record_refs=state.transition_refs + ("transition:future",)
        ),
    )
    return replace(obs, admission_basis=long_basis), state


def _case_frontier_wrong_ref(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    obs = _valid_observation(state, transition, request)
    bad_basis = replace(
        obs.admission_basis,
        observed_frontier=ObservedFrontier(record_refs=("transition:wrong",)),
    )
    return replace(obs, admission_basis=bad_basis), state


def _case_empty_frontier_valid(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    obs = _valid_observation(state, transition, request)
    empty_basis = replace(
        obs.admission_basis,
        observed_frontier=ObservedFrontier(record_refs=()),
    )
    return replace(obs, admission_basis=empty_basis), state


def _case_one_shot_disagreement(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    obs = _valid_observation(state, transition, request)
    bad_basis = replace(obs.admission_basis, one_shot_key=OneShotKey("different-key"))
    return replace(obs, admission_basis=bad_basis), state


def _case_terminal_state(
    state: KernelReplayState,
    transition: ReplayableKernelTransition,
    request: ExternalEffectRequest,
) -> _CaseReturn:
    obs = _valid_observation(state, transition, request)
    terminal_state = replace(state, open_requests={}, terminal=True)
    return obs, terminal_state


CASES: dict[str, tuple[bool, CaseBuilder]] = {
    "valid:all-four-parts-agree": (True, _case_valid),
    "invalid:source-key-already-consumed": (False, _case_source_already_consumed),
    "invalid:source-ref-disagreement": (False, _case_source_ref_disagreement),
    "invalid:stale-source-generation": (False, _case_stale_source_generation),
    "invalid:frontier-too-long": (False, _case_frontier_too_long),
    "invalid:frontier-wrong-ref": (False, _case_frontier_wrong_ref),
    "valid:empty-frontier": (True, _case_empty_frontier_valid),
    "invalid:one-shot-key-disagreement": (False, _case_one_shot_disagreement),
    "invalid:terminal-state": (False, _case_terminal_state),
}


def run_cases() -> tuple[CaseResult, ...]:
    """Run every case in CASES and return per-case outcomes.

    Setup (start_kernel_replay) happens once; per-case state mutations are
    constructed via dataclasses.replace.
    """

    state, transition, request = _setup_initial_state()
    results: list[CaseResult] = []
    for name, (expect_pass, builder) in CASES.items():
        observation, case_state = builder(state, transition, request)
        results.append(_run_one(name, expect_pass, observation, case_state))
    return tuple(results)


__all__ = [
    "AdmittedObservation",
    "AdmittedObservationError",
    "CASES",
    "CaseResult",
    "run_cases",
    "validate_admitted_observation",
]
