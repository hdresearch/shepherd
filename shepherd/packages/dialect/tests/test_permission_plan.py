"""The authority-enforcement seam: ``may=`` / grants → ``PermissionPlan`` (monitor assignment).

These prove the seam is (1) behaviour-preserving — ``plan.confinement`` byte-equals the direct
``confinement.py`` lowering, so routing a run through the plan changes only *where the spec comes
from* — and (2) real — the plan names both the syscall-jail and carrier-check-at-commit monitors
with completeness/tamper basis, and fails closed (via the underlying lowerings) on unsupported
authority. This is the interposition point that turns the two disjoint enforcement lanes into one
compiler consumer; a third monitor later is a new assignment branch, not a fourth lane.
"""

from __future__ import annotations

import os

import pytest

from shepherd_dialect.confinement import (
    BindingRootGrant,
    OverlappingBoundRootsError,
    UnsupportedMayProfileError,
    lower_grants_to_confinement,
    lower_may_resolution_to_confinement,
    resolve_may,
)
from shepherd_dialect.permission_plan import (
    CARRIER_MONITOR,
    JAIL_MONITOR,
    CarrierCheckAuthority,
    MonitorAssignment,
    PermissionPlan,
    install,
)


def _real(path) -> str:
    return os.path.realpath(str(path))


# --- behaviour preservation: the plan's confinement IS the direct lowering ------------------


def test_permissive_plan_confinement_byte_equals_direct_lowering(tmp_path) -> None:
    resolution = resolve_may("Permissive")
    plan = install(resolution, tmp_path)
    assert plan.confinement == lower_may_resolution_to_confinement(resolution, tmp_path)


def test_readonly_plan_confinement_byte_equals_direct_lowering(tmp_path) -> None:
    resolution = resolve_may("ReadOnly")
    plan = install(resolution, tmp_path)
    assert plan.confinement == lower_may_resolution_to_confinement(resolution, tmp_path)
    # ReadOnly = no managed writable root.
    assert plan.confinement.writable_roots == ()


def test_defaulted_may_lowers_through_the_plan(tmp_path) -> None:
    # may=None → defaulted Permissive; the seam preserves the whole-workspace writable root.
    plan = install(resolve_may(None), tmp_path)
    assert plan.confinement.writable_roots == (str(tmp_path),)


def test_per_binding_grants_plan_confinement_byte_equals_direct_lowering(tmp_path) -> None:
    backend = tmp_path / "backend"
    docs = tmp_path / "docs"
    grants = [
        BindingRootGrant(binding="backend", root=str(backend), writable=True),
        BindingRootGrant(binding="docs", root=str(docs), writable=False),
    ]
    plan = install(grants, tmp_path)
    assert plan.confinement == lower_grants_to_confinement(grants)
    # deny-closed: only the ReadWrite-granted root is writable.
    assert plan.confinement.writable_roots == (_real(backend),)


# --- the plan names BOTH monitors, with basis ------------------------------------------------


def test_plan_assigns_jail_and_carrier_monitors(tmp_path) -> None:
    plan = install(resolve_may("ReadOnly"), tmp_path)
    monitors = {a.monitor for a in plan.assignments}
    assert monitors == {JAIL_MONITOR, CARRIER_MONITOR}


def test_jail_monitor_is_pre_action_carrier_is_commit(tmp_path) -> None:
    plan = install(resolve_may("Permissive"), tmp_path)
    assert plan.monitor(JAIL_MONITOR).timing == "pre_action"
    assert plan.monitor(CARRIER_MONITOR).timing == "commit"


def test_every_assignment_records_completeness_and_tamper_basis(tmp_path) -> None:
    plan = install(resolve_may("ReadOnly"), tmp_path)
    for assignment in plan.assignments:
        assert assignment.completeness_basis.strip()
        assert assignment.tamper_basis.strip()


def test_only_the_jail_monitor_carries_a_confinement_spec(tmp_path) -> None:
    plan = install(resolve_may("ReadOnly"), tmp_path)
    assert plan.monitor(JAIL_MONITOR).confinement is not None
    assert plan.monitor(CARRIER_MONITOR).confinement is None


def test_retained_output_selection_installs_carrier_only_plan() -> None:
    plan = install(
        CarrierCheckAuthority(
            route="retained_output_selection",
            effective_match_digest="effective-match-digest",
            authority_surface_plan_digest="authority-surface-plan-digest",
        )
    )

    assert [assignment.monitor for assignment in plan.assignments] == [CARRIER_MONITOR]
    assert plan.monitor(CARRIER_MONITOR).route == "retained_output_selection"
    assert plan.monitor(CARRIER_MONITOR).evidence == (
        ("authority_surface_plan_digest", "authority-surface-plan-digest"),
        ("effective_match_digest", "effective-match-digest"),
    )
    with pytest.raises(ValueError, match="no jail monitor"):
        _ = plan.confinement


def test_retained_output_selection_plan_descriptor_cites_route_and_surface() -> None:
    plan = install(
        CarrierCheckAuthority(
            route="retained_output_selection",
            effective_match_digest="effective-match-digest",
            authority_surface_plan_digest="authority-surface-plan-digest",
        )
    )
    descriptor = plan.to_descriptor()
    (assignment,) = descriptor["assignments"]

    assert descriptor["schema"] == "shepherd.permission-plan.v1"
    assert assignment["monitor"] == CARRIER_MONITOR
    assert assignment["route"] == "retained_output_selection"
    assert assignment["confinement"] is None
    assert assignment["evidence"] == {
        "authority_surface_plan_digest": "authority-surface-plan-digest",
        "effective_match_digest": "effective-match-digest",
    }


def test_permission_plan_rejects_unsupported_fallback(tmp_path) -> None:
    assignment = install(resolve_may("ReadOnly"), tmp_path).monitor(JAIL_MONITOR)
    with pytest.raises(ValueError, match="fallback"):
        PermissionPlan(assignments=(assignment,), fallback="maybe")


def test_permission_plan_rejects_duplicate_monitor_assignments(tmp_path) -> None:
    assignment = install(resolve_may("ReadOnly"), tmp_path).monitor(JAIL_MONITOR)
    with pytest.raises(ValueError, match="more than once"):
        PermissionPlan(assignments=(assignment, assignment))


def test_monitor_assignment_rejects_unsupported_timing() -> None:
    with pytest.raises(ValueError, match="timing"):
        MonitorAssignment(
            monitor="custom_monitor",
            timing="later",
            completeness_basis="complete for this test",
            tamper_basis="outside the test body",
        )


def test_monitor_assignment_rejects_duplicate_evidence_keys() -> None:
    with pytest.raises(ValueError, match="repeats key"):
        MonitorAssignment(
            monitor=CARRIER_MONITOR,
            timing="commit",
            completeness_basis="complete for this test",
            tamper_basis="outside the test body",
            evidence=(("digest", "a"), ("digest", "b")),
        )


def test_unknown_monitor_lookup_raises(tmp_path) -> None:
    plan = install(resolve_may("ReadOnly"), tmp_path)
    with pytest.raises(KeyError):
        plan.monitor("transmit_gate")


# --- fail-closed: the compiler is total over fallback, not over enforcement ------------------


def test_unsupported_may_profile_fails_closed(tmp_path) -> None:
    with pytest.raises(UnsupportedMayProfileError):
        install(resolve_may("Standard"), tmp_path)


def test_overlapping_bound_roots_fail_closed(tmp_path) -> None:
    backend = tmp_path / "backend"
    nested = backend / "vendor"
    grants = [
        BindingRootGrant(binding="backend", root=str(backend), writable=True),
        BindingRootGrant(binding="vendor", root=str(nested), writable=False),
    ]
    with pytest.raises(OverlappingBoundRootsError):
        install(grants, tmp_path)


# --- plan is a value, citable by digest ------------------------------------------------------


def test_equal_declarations_produce_equal_plans(tmp_path) -> None:
    a = install(resolve_may("ReadOnly"), tmp_path)
    b = install(resolve_may("ReadOnly"), tmp_path)
    assert a == b
    assert a.digest == b.digest


def test_digest_distinguishes_readonly_from_permissive(tmp_path) -> None:
    ro = install(resolve_may("ReadOnly"), tmp_path)
    rw = install(resolve_may("Permissive"), tmp_path)
    assert ro.digest != rw.digest


def test_digest_is_hex_sha256(tmp_path) -> None:
    plan = install(resolve_may("ReadOnly"), tmp_path)
    assert len(plan.digest) == 64
    int(plan.digest, 16)  # hex-parseable


# --- the facade re-exports the seam ----------------------------------------------------------


def test_seam_is_exported_from_the_dialect_facade() -> None:
    import shepherd_dialect

    assert shepherd_dialect.CarrierCheckAuthority is CarrierCheckAuthority
    assert shepherd_dialect.PermissionPlan is PermissionPlan
    assert shepherd_dialect.MonitorAssignment is MonitorAssignment
    assert shepherd_dialect.install_permission_plan is install
