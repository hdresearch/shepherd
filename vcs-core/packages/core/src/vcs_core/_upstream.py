"""Internal upstream-aware planning DTOs.

These types are intentionally not part of the experimental substrate SPI.
They support planner/runtime work for upstream-aware substrates while the
public extension surface remains at SPI v0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BasisComparison = Literal["exact"]
BaseAvailabilitySource = Literal["live-upstream", "durable-projection", "none"]
ReconcileOutcome = Literal["unchanged", "updated", "conflicted", "unsupported"]
PreflightStatus = Literal["ready", "stale", "base-unavailable", "conflicted", "unsupported"]


def _require_non_empty(value: str | None, *, field_name: str) -> None:
    if value is not None and value == "":
        raise ValueError(f"{field_name} must not be an empty string.")


@dataclass(frozen=True)
class UpstreamBasisState:
    """Planner-visible upstream basis identity for pending local work."""

    substrate: str
    target_id: str
    basis_token: str | None
    last_observed_token: str | None
    local_frontier: str | None
    comparison: BasisComparison = "exact"

    def __post_init__(self) -> None:
        if not self.substrate:
            raise ValueError("substrate must not be empty.")
        if not self.target_id:
            raise ValueError("target_id must not be empty.")
        _require_non_empty(self.basis_token, field_name="basis_token")
        _require_non_empty(self.last_observed_token, field_name="last_observed_token")
        _require_non_empty(self.local_frontier, field_name="local_frontier")


@dataclass(frozen=True)
class UpstreamBaseAvailability:
    """Availability of the specific upstream base required for replay."""

    substrate: str
    target_id: str
    basis_token: str | None
    base_available: bool
    source: BaseAvailabilitySource

    def __post_init__(self) -> None:
        if not self.substrate:
            raise ValueError("substrate must not be empty.")
        if not self.target_id:
            raise ValueError("target_id must not be empty.")
        _require_non_empty(self.basis_token, field_name="basis_token")
        if self.source == "none" and self.base_available:
            raise ValueError("source='none' is inconsistent with base_available=True.")
        if self.source != "none" and not self.base_available:
            raise ValueError("A concrete availability source requires base_available=True.")


@dataclass(frozen=True)
class PendingSelector:
    """Stable selector for pending upstream-aware work."""

    target_id: str
    unit_id: str | None = None
    frontier: str | None = None
    scope_context: str | None = None

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("target_id must not be empty.")
        _require_non_empty(self.unit_id, field_name="unit_id")
        _require_non_empty(self.frontier, field_name="frontier")
        _require_non_empty(self.scope_context, field_name="scope_context")
        if (self.unit_id is None) == (self.frontier is None):
            raise ValueError("Exactly one stable selector is required: unit_id or frontier.")


@dataclass(frozen=True)
class ReconcileResult:
    """Durable result of an upstream-aware reconcile attempt."""

    outcome: ReconcileOutcome
    reason: str | None = None
    basis_token: str | None = None
    frontier: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.reason, field_name="reason")
        _require_non_empty(self.basis_token, field_name="basis_token")
        _require_non_empty(self.frontier, field_name="frontier")
        if self.outcome == "updated" and self.basis_token is None and self.frontier is None:
            raise ValueError("outcome='updated' requires an updated basis_token or frontier.")
        if self.outcome in {"conflicted", "unsupported"} and self.reason is None:
            raise ValueError(f"outcome={self.outcome!r} requires a reason.")


@dataclass(frozen=True)
class PreflightResult:
    """Preflight verdict for a planned upstream-aware materialization unit."""

    status: PreflightStatus
    reason: str | None = None
    observed_token: str | None = None
    base_availability: UpstreamBaseAvailability | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.reason, field_name="reason")
        _require_non_empty(self.observed_token, field_name="observed_token")
        if self.status != "ready" and self.reason is None:
            raise ValueError(f"status={self.status!r} requires a reason.")
        if self.status == "base-unavailable" and (
            self.base_availability is None or self.base_availability.base_available
        ):
            raise ValueError("status='base-unavailable' requires base_availability.base_available=False.")
        if self.base_availability is not None and self.observed_token is None:
            raise ValueError("Preflight results with base_availability require an observed_token.")

    @property
    def ok(self) -> bool:
        return self.status == "ready"
