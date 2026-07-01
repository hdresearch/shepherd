"""The authority-enforcement seam: ``may=`` / grants → a ``PermissionPlan``.

Where does authority *actually* get enforced? Not in one place. The syscall jail
denies managed writes before the action; the carrier check-at-commit gate proposes
the captured delta at the last undo point. Those are two **monitors** for the same
``may=``, and until now they were two disjoint lowering lanes wired side by side
(`confinement.py` → ``writable_roots`` on one side; ``merge_with_authority`` on the
other). This module is the seam where they meet: a *monitor-assignment compiler*
(``260628-1130-match.md`` §"Monitor Assignment", the authority-enforcement spine)
that lowers one authority declaration to a plan naming *which monitor enforces which
clause, with what completeness/tamper basis*.

It is deliberately the **minimal** form of that compiler — two monitors, one
placement — not the full Tranche-4 machinery. The point is the interposition, not
the generality: once a run lowers *through* ``install(...)``, adding a third monitor
(a transmit gate for an external substrate, a broker for egress) is a new assignment
branch, not a fourth parallel lane. The compiler is **total over fallback**
(``enforce`` | ``refuse``), never total over enforcement: when a clause has no
complete monitor, the underlying lowering fails closed rather than silently
under-enforcing (the ``_resolve_spec`` network-axis fail-close, generalized).

``plan.confinement`` returns the jail monitor's ``ConfinementSpec`` unchanged, so
routing a run through the plan is behaviour-preserving — the same spec reaches
``launch_confined``. What is new is that the plan *names both monitors* and is
citable by digest as authority evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .confinement import (
    MayResolution,
    lower_grants_to_confinement,
    lower_may_resolution_to_confinement,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vcs_core.spi import ConfinementSpec

    from .confinement import BindingRootGrant

__all__ = [
    "CARRIER_MONITOR",
    "JAIL_MONITOR",
    "CarrierCheckAuthority",
    "MonitorAssignment",
    "PermissionPlan",
    "install",
]

# Monitor identifiers. The set is open by design — a transmit gate, credential
# gateway, or egress broker join here as the plan learns to assign them.
JAIL_MONITOR = "syscall_jail"
CARRIER_MONITOR = "carrier_check_at_commit"
_FALLBACKS = frozenset({"enforce", "refuse"})
_TIMINGS = frozenset({"pre_action", "commit"})


def _require_non_empty(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True)
class CarrierCheckAuthority:
    """Authority declaration for a carrier-only check-at-commit route.

    This is the settlement/adoption half of the same monitor-assignment seam as
    ``may=`` execution. It deliberately carries the authority-surface digest
    separately from the monitor plan digest: the former describes the ``Match``
    being evaluated, while the returned :class:`PermissionPlan` describes which
    monitor evaluates it.
    """

    route: str
    effective_match_digest: str
    authority_surface_plan_digest: str
    completeness_basis: str = (
        "carrier diff / exact-tree-diff evidence at the adoption boundary; changed-path fallback is "
        "advisory and incomplete for path-sensitive grants"
    )
    tamper_basis: str = "the check runs in the coordinator/vcs-core settlement path, outside the task process"

    def __post_init__(self) -> None:
        _require_non_empty(self.route, "CarrierCheckAuthority.route")
        _require_non_empty(self.effective_match_digest, "CarrierCheckAuthority.effective_match_digest")
        _require_non_empty(
            self.authority_surface_plan_digest,
            "CarrierCheckAuthority.authority_surface_plan_digest",
        )
        _require_non_empty(self.completeness_basis, "CarrierCheckAuthority.completeness_basis")
        _require_non_empty(self.tamper_basis, "CarrierCheckAuthority.tamper_basis")


@dataclass(frozen=True)
class MonitorAssignment:
    """One monitor made responsible for a clause, with the basis that makes it real.

    ``completeness_basis`` states *which routes* the monitor covers (the load-bearing
    honesty — a monitor is only enforcement-bearing for routes that must cross it);
    ``tamper_basis`` states why the judged body cannot disable it. ``confinement`` is
    the jail monitor's lowered spec; monitors that do not lower to a ``ConfinementSpec``
    (the carrier gate, a future transmit gate) leave it ``None``.
    """

    monitor: str
    timing: str  # "pre_action" | "commit"
    completeness_basis: str
    tamper_basis: str
    confinement: ConfinementSpec | None = None
    route: str | None = None
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.monitor, "MonitorAssignment.monitor")
        _require_non_empty(self.timing, "MonitorAssignment.timing")
        if self.timing not in _TIMINGS:
            raise ValueError(f"MonitorAssignment.timing is unsupported: {self.timing!r}")
        _require_non_empty(self.completeness_basis, "MonitorAssignment.completeness_basis")
        _require_non_empty(self.tamper_basis, "MonitorAssignment.tamper_basis")
        if self.route is not None:
            _require_non_empty(self.route, "MonitorAssignment.route")
        evidence_keys: set[str] = set()
        for key, value in self.evidence:
            _require_non_empty(key, "MonitorAssignment.evidence key")
            if key in evidence_keys:
                raise ValueError(f"MonitorAssignment.evidence repeats key {key!r}")
            evidence_keys.add(key)
            _require_non_empty(value, f"MonitorAssignment.evidence[{key}]")

    def to_descriptor(self) -> dict[str, object]:
        """Stable JSON-shaped assignment descriptor used for plan digests."""
        descriptor: dict[str, object] = {
            "monitor": self.monitor,
            "timing": self.timing,
            "completeness_basis": self.completeness_basis,
            "tamper_basis": self.tamper_basis,
            "confinement": None if self.confinement is None else _confinement_descriptor(self.confinement),
        }
        if self.route is not None:
            descriptor["route"] = self.route
        if self.evidence:
            descriptor["evidence"] = dict(self.evidence)
        return descriptor


@dataclass(frozen=True)
class PermissionPlan:
    """The installed monitor assignment for one authority declaration on one placement.

    ``assignments`` names every monitor that participates in enforcing this run's
    authority. ``fallback`` is the compiler's disposition when a clause cannot be made
    enforcement-bearing — ``"enforce"`` here, because the underlying lowerings already
    fail closed (they raise) rather than degrade, so a returned plan is always one whose
    monitors are assignable. The plan is a *value*: equal declarations on equal
    placements produce equal plans, and ``digest`` cites the plan as authority evidence.
    """

    assignments: tuple[MonitorAssignment, ...]
    fallback: str = "enforce"

    def __post_init__(self) -> None:
        if self.fallback not in _FALLBACKS:
            raise ValueError(f"PermissionPlan.fallback is unsupported: {self.fallback!r}")
        if not self.assignments:
            raise ValueError("PermissionPlan.assignments must be non-empty")
        monitors: set[str] = set()
        for assignment in self.assignments:
            if assignment.monitor in monitors:
                raise ValueError(f"PermissionPlan assigns monitor {assignment.monitor!r} more than once")
            monitors.add(assignment.monitor)

    def monitor(self, name: str) -> MonitorAssignment:
        """The assignment for ``name`` (raises if this plan does not assign it)."""
        for assignment in self.assignments:
            if assignment.monitor == name:
                return assignment
        raise KeyError(f"PermissionPlan has no {name!r} assignment")

    @property
    def confinement(self) -> ConfinementSpec:
        """The jail monitor's ``ConfinementSpec`` — the behaviour-preserving output.

        This is what the run driver hands to ``launch_confined``; routing through the
        plan changes *where the spec comes from*, not the spec.
        """
        try:
            jail = self.monitor(JAIL_MONITOR)
        except KeyError as exc:
            raise ValueError("PermissionPlan has no jail monitor assignment") from exc
        spec = jail.confinement
        if spec is None:  # pragma: no cover - the jail assignment always carries a spec
            raise ValueError("jail monitor assignment is missing its ConfinementSpec")
        return spec

    @property
    def digest(self) -> str:
        """A stable content digest over the assignments, for authority-evidence citation."""
        payload = json.dumps(self.to_descriptor(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def to_descriptor(self) -> dict[str, object]:
        """Stable JSON-shaped monitor plan descriptor."""
        return {
            "schema": "shepherd.permission-plan.v1",
            "fallback": self.fallback,
            "assignments": [assignment.to_descriptor() for assignment in self.assignments],
        }


# The carrier check-at-commit monitor is placement-independent for the reversible
# filesystem lane: the wrap baselines the working tree, proposes the captured delta at
# return time (the last undo point), and merges only on approval. It is the completeness
# backstop for managed writes that bypass cooperative/handle paths.
_CARRIER_ASSIGNMENT = MonitorAssignment(
    monitor=CARRIER_MONITOR,
    timing="commit",
    completeness_basis=(
        "reversible filesystem writes captured in the carrier working-tree delta, proposed at "
        "return before merge; the completeness backstop for managed writes that bypass "
        "cooperative/handle paths"
    ),
    tamper_basis="the check runs in the coordinator/reversible wrap, outside the task process",
    confinement=None,
)

_JAIL_COMPLETENESS = (
    "managed-filesystem writes on a jailed placement (Seatbelt/Landlock deny-closed over the "
    "writable-root set); coarse deny-all/allow-all network only — host-filtered egress is the "
    "egress broker's monitor, not the jail's"
)
_JAIL_TAMPER = "kernel-enforced sandbox profile; the task process cannot alter or escape it"


def _jail_assignment(spec: ConfinementSpec) -> MonitorAssignment:
    return MonitorAssignment(
        monitor=JAIL_MONITOR,
        timing="pre_action",
        completeness_basis=_JAIL_COMPLETENESS,
        tamper_basis=_JAIL_TAMPER,
        confinement=spec,
    )


def _carrier_assignment_for_authority(authority: CarrierCheckAuthority) -> MonitorAssignment:
    return MonitorAssignment(
        monitor=CARRIER_MONITOR,
        timing="commit",
        completeness_basis=authority.completeness_basis,
        tamper_basis=authority.tamper_basis,
        confinement=None,
        route=authority.route,
        evidence=(
            ("authority_surface_plan_digest", authority.authority_surface_plan_digest),
            ("effective_match_digest", authority.effective_match_digest),
        ),
    )


def install(
    authority: MayResolution | Sequence[BindingRootGrant] | CarrierCheckAuthority,
    working_path: object | None = None,
) -> PermissionPlan:
    """Lower an authority declaration to a two-monitor ``PermissionPlan``.

    ``authority`` is either a whole-workspace :class:`~.confinement.MayResolution`
    (today's run-driver path) or a per-binding ``Sequence[BindingRootGrant]`` (the
    v0.2 signature-grant path). Either lowers to the jail monitor's ``ConfinementSpec``
    via the existing ``confinement.py`` functions — so this compiler adds no new
    enforcement mechanism, only the assignment record. Both underlying lowerings fail
    closed (raise) on an unsupported profile or overlapping bound roots; ``install``
    therefore never returns a plan whose jail clause is un-enforceable.

    The carrier check-at-commit monitor is always assigned as the reversible-write
    completeness backstop. ``working_path`` is only consulted for the whole-workspace
    lowering (per-binding grants already carry absolute roots).
    """
    if isinstance(authority, CarrierCheckAuthority):
        return PermissionPlan(assignments=(_carrier_assignment_for_authority(authority),))
    if isinstance(authority, MayResolution):
        # Whole-workspace ``may=`` — the lowering needs the run's working path.
        from pathlib import Path

        if working_path is None:
            raise ValueError("working_path is required for whole-workspace PermissionPlan lowering")
        spec = lower_may_resolution_to_confinement(authority, Path(str(working_path)))
    else:
        # Per-binding grants — roots are already absolute; disjointness is validated inside.
        spec = lower_grants_to_confinement(authority)
    return PermissionPlan(assignments=(_jail_assignment(spec), _CARRIER_ASSIGNMENT))


def _confinement_descriptor(spec: ConfinementSpec) -> dict[str, object]:
    return {
        "writable_roots": sorted(spec.writable_roots),
        "network": {
            "mode": spec.network.mode.value,
            "allowed_hosts": list(spec.network.allowed_hosts),
            "broker_port": spec.network.broker_port,
        },
    }
