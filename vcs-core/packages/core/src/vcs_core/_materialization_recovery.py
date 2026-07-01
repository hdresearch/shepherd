"""Tolerant materialization recovery state probes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vcs_core._dirty_flag import read_dirty_flag
from vcs_core._materialization_run import MaterializationRun, read_materialization_run

# Deliberately narrower than, and intentionally NOT imported from, the inventory
# Health vocabulary (_query_inventory). This is a low-level artifact-probe module (the
# dirty-flag and run-ledger files), so it must not depend on the query/inventory layer
# that consumes it -- adopting Health's Literals here would invert the layering.
# Stated map (applied where _recovery_inventory lifts these into Health-backed items):
# "present"/"absent" correspond to HealthPresence; "valid"/"corrupt" correspond to
# Health "valid"/"invalid". The narrower set is intentional -- a file is definitively
# present or absent and either parses or does not, so there is no "unknown" state.
Presence = Literal["absent", "present"]
Validity = Literal["valid", "corrupt"]


@dataclass(frozen=True)
class DirtyFlagStatus:
    """Presence and parse status for the dirty-push flag."""

    presence: Presence
    validity: Validity | None = None
    session_id: str | None = None
    timestamp: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class MaterializationRunStatus:
    """Presence and parse status for the materialization run ledger."""

    presence: Presence
    validity: Validity | None = None
    run: MaterializationRun | None = None
    error: str | None = None


@dataclass(frozen=True)
class MaterializationRecoveryState:
    """Tolerant recovery state shared by inventory and execution."""

    dirty: DirtyFlagStatus
    run: MaterializationRunStatus

    @property
    def required(self) -> bool:
        return self.dirty_present or self.run_present

    @property
    def dirty_present(self) -> bool:
        return self.dirty.presence == "present"

    @property
    def run_present(self) -> bool:
        return self.run.presence == "present"


_PROBE_EXCEPTIONS = (OSError, TypeError, ValueError, KeyError)


def probe_dirty_flag(repo_path: str | Path) -> DirtyFlagStatus:
    """Read dirty-flag state without letting corrupt metadata escape."""
    path = Path(repo_path) / "dirty"
    if not path.exists():
        return DirtyFlagStatus(presence="absent")
    try:
        result = read_dirty_flag(str(repo_path))
    except _PROBE_EXCEPTIONS as exc:
        return DirtyFlagStatus(presence="present", validity="corrupt", error=str(exc))
    if result is None:
        return DirtyFlagStatus(presence="absent")
    session_id, timestamp = result
    return DirtyFlagStatus(
        presence="present",
        validity="valid",
        session_id=session_id,
        timestamp=timestamp,
    )


def probe_materialization_run(repo_path: str | Path) -> MaterializationRunStatus:
    """Read materialization run state without letting corrupt metadata escape."""
    path = Path(repo_path) / "materialization-run.json"
    if not path.exists():
        return MaterializationRunStatus(presence="absent")
    try:
        run = read_materialization_run(str(repo_path))
    except _PROBE_EXCEPTIONS as exc:
        return MaterializationRunStatus(presence="present", validity="corrupt", error=str(exc))
    if run is None:
        return MaterializationRunStatus(presence="absent")
    return MaterializationRunStatus(presence="present", validity="valid", run=run)


def probe_materialization_recovery_state(repo_path: str | Path) -> MaterializationRecoveryState:
    """Return the tolerant materialization recovery state for a repository."""
    return MaterializationRecoveryState(
        dirty=probe_dirty_flag(repo_path),
        run=probe_materialization_run(repo_path),
    )
