"""Profile threading on PreparedKernelProgram and KernelReplayState.

Per 2026-05-22 §"Profile attachment on PreparedKernelProgram", 2026-05-24
§"Post-#72 design pass" item A, and 2026-05-26 §"Profile admission boundary"
(Option 1: a profile that `requires_source_admission` can only be minted via
the source-level `admit_and_prepare(...)`; IR-level `prepare_kernel_program`
refuses it and defaults to the permissive `CORE_A`). Verifies:

- prepare_kernel_program(ir) defaults to CORE_A (the permissive profile).
- prepare_kernel_program(ir, profile=-lite) RAISES — -lite is not stampable
  on raw IR (its admission contract is over the source AST).
- admit_and_prepare(source, profile=-lite) is the sole minter of a -lite
  prepared program (runs validate_profile_admission, then elaborates+stamps).
- ensure_prepared_kernel_program(program) shim defaults to CORE_A.
- KernelReplayState carries a profile field that agrees with the prepared
  program (post-init validates agreement).
- KERNEL_REPLAY_STATE_SCHEMA_VERSION bumped to .v3.
- State serde round-trips the profile name through the JSON envelope.
"""

from __future__ import annotations

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.program_admission import (
    KernelProgramValidationError,
    admit_and_prepare,
    ensure_prepared_kernel_program,
    prepare_kernel_program,
)
from shepherd_kernel_v3_reference.kernel.replay import (
    KERNEL_REPLAY_STATE_SCHEMA_VERSION,
    ContinuationReplayError,
    KernelReplayState,
    kernel_replay_state_from_json,
    kernel_replay_state_to_json,
    start_kernel_replay,
)
from shepherd_kernel_v3_reference.profiles import (
    CORE_A,
    CORE_REFERENCE_V0_LITE,
    lookup_profile,
)
from shepherd_kernel_v3_reference.source.syntax import Let, Lit, Perform, Return, Var


def _suspended_source():
    """Source program that suspends on an unhandled `ask` effect.

    -lite-admissible: Let / Perform / Return / Var / Lit(null) only, no
    forbidden constructs.
    """
    return Let("y", Perform("ask", Lit(None)), Return(Var("y")))


def _suspended_program():
    return elaborate(_suspended_source())


# --- Profile attachment ------------------------------------------------


def test_prepare_kernel_program_defaults_to_core_a() -> None:
    # Option 1: the IR-level default is the permissive CORE_A, not -lite.
    prepared = prepare_kernel_program(_suspended_program())
    assert prepared.profile is CORE_A


def test_prepare_kernel_program_accepts_explicit_core_a() -> None:
    prepared = prepare_kernel_program(_suspended_program(), profile=CORE_A)
    assert prepared.profile is CORE_A


def test_prepare_kernel_program_refuses_lite_on_ir() -> None:
    # -lite requires source admission and cannot be stamped on raw IR.
    with pytest.raises(KernelProgramValidationError, match="requires source-level admission"):
        prepare_kernel_program(_suspended_program(), profile=CORE_REFERENCE_V0_LITE)


def test_admit_and_prepare_mints_lite_from_source() -> None:
    # admit_and_prepare is the sole minter of a -lite prepared program.
    prepared = admit_and_prepare(_suspended_source(), profile=CORE_REFERENCE_V0_LITE)
    assert prepared.profile is CORE_REFERENCE_V0_LITE


def test_admit_and_prepare_rejects_non_lite_source() -> None:
    # A non--lite source construct is rejected at admission, before stamping.
    from shepherd_kernel_v3_reference.profile_admission import ProfileAdmissionError
    from shepherd_kernel_v3_reference.source.syntax import RecordExpr

    bad_source = Let("y", Perform("ask", RecordExpr({"k": Lit(1)})), Return(Var("y")))
    with pytest.raises(ProfileAdmissionError):
        admit_and_prepare(bad_source, profile=CORE_REFERENCE_V0_LITE)


def test_ensure_prepared_kernel_program_shim_defaults_to_core_a() -> None:
    prepared = ensure_prepared_kernel_program(_suspended_program())
    assert prepared.profile is CORE_A


def test_ensure_prepared_kernel_program_preserves_already_prepared() -> None:
    prepared = admit_and_prepare(_suspended_source(), profile=CORE_REFERENCE_V0_LITE)
    # Shim is a no-op when input is already prepared, even if default differs
    assert ensure_prepared_kernel_program(prepared) is prepared


# --- KernelReplayState.profile agreement -------------------------------


def test_state_profile_agrees_with_prepared() -> None:
    state, _t = start_kernel_replay(_suspended_program())
    # start_kernel_replay calls ensure_prepared_kernel_program → CORE_A
    assert state.profile is CORE_A
    assert state.prepared_program.profile is CORE_A


def test_state_profile_disagreement_raises() -> None:
    prepared = admit_and_prepare(_suspended_source(), profile=CORE_REFERENCE_V0_LITE)
    from shepherd_kernel_v3_reference.kernel.program_identity import project_program_identity

    program_ref = project_program_identity(prepared).program_ref
    with pytest.raises(ContinuationReplayError, match="profile"):
        KernelReplayState(
            prepared_program=prepared,
            program_ref=program_ref,
            profile=CORE_A,
        )


# --- Schema version + serde --------------------------------------------


def test_state_schema_version_is_v3() -> None:
    assert KERNEL_REPLAY_STATE_SCHEMA_VERSION.endswith(".v3")


def test_state_serde_round_trips_profile() -> None:
    state, _t = start_kernel_replay(_suspended_program())
    js = kernel_replay_state_to_json(state)
    assert js["profile"] == CORE_A.name
    assert js["state_schema_version"] == KERNEL_REPLAY_STATE_SCHEMA_VERSION

    # Reconstruct using the same source program
    reconstructed = kernel_replay_state_from_json(_suspended_program(), js)
    assert reconstructed.profile is CORE_A


def test_state_serde_lite_round_trip() -> None:
    """A -lite profile state round-trips through JSON."""
    prepared = admit_and_prepare(_suspended_source(), profile=CORE_REFERENCE_V0_LITE)
    state, _t = start_kernel_replay(prepared)
    assert state.profile is CORE_REFERENCE_V0_LITE
    js = kernel_replay_state_to_json(state)
    assert js["profile"] == CORE_REFERENCE_V0_LITE.name
    reconstructed = kernel_replay_state_from_json(prepared, js)
    assert reconstructed.profile is CORE_REFERENCE_V0_LITE


# --- lookup_profile ----------------------------------------------------


def test_lookup_profile_returns_registered() -> None:
    assert lookup_profile("core_a") is CORE_A
    assert lookup_profile("core_reference_v0_lite") is CORE_REFERENCE_V0_LITE


def test_lookup_profile_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown profile"):
        lookup_profile("nonexistent")
