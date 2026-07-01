from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcs_core._lifecycle_state import LifecycleRunState


@dataclass(frozen=True)
class LifecycleProgress:
    """Update progress fields on the active lifecycle run."""

    state: LifecycleRunState

    def mark_completed_substrate(self, substrate_name: str) -> None:
        run = self.state.current()
        if run is None:
            return
        completed = list(run.completed_substrates)
        if substrate_name in completed:
            return
        completed.append(substrate_name)
        self.state.update(completed_substrates=tuple(completed))

    def mark_prepared_substrate(self, substrate_name: str) -> None:
        run = self.state.current()
        if run is None:
            return
        prepared = list(run.prepared_substrates)
        if substrate_name in prepared:
            return
        prepared.append(substrate_name)
        self.state.update(prepared_substrates=tuple(prepared))

    def prepared_effect_count(self, substrate_name: str) -> int:
        run = self.state.current()
        if run is None:
            return 0
        for recorded_name, count in run.prepared_effect_counts:
            if recorded_name == substrate_name:
                return count
        return 0

    def mark_prepared_effect_count(self, substrate_name: str, count: int) -> None:
        run = self.state.current()
        if run is None:
            return
        prepared_counts = dict(run.prepared_effect_counts)
        if prepared_counts.get(substrate_name) == count:
            return
        prepared_counts[substrate_name] = count
        self.state.update(prepared_effect_counts=tuple(prepared_counts.items()))
