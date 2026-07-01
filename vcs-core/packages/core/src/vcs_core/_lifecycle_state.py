from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from vcs_core._lifecycle_run import LifecycleRun, clear_lifecycle_run, read_lifecycle_run, write_lifecycle_run

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class LifecycleRunState:
    """Adapter for durable lifecycle run state plus the in-memory active run."""

    repo_path: str
    current: Callable[[], LifecycleRun | None]
    set_current: Callable[[LifecycleRun | None], None]

    def read(self) -> LifecycleRun | None:
        return read_lifecycle_run(self.repo_path)

    def current_or_read(self) -> LifecycleRun | None:
        run = self.current()
        if run is not None:
            return run
        run = self.read()
        if run is not None:
            self.set_current(run)
        return run

    def persist(self, run: LifecycleRun) -> LifecycleRun:
        write_lifecycle_run(self.repo_path, run)
        self.set_current(run)
        return run

    def update(
        self,
        *,
        phase: str | None = None,
        prepared_effect_counts: tuple[tuple[str, int], ...] | None = None,
        prepared_substrates: tuple[str, ...] | None = None,
        completed_substrates: tuple[str, ...] | None = None,
    ) -> LifecycleRun:
        run = self.current()
        if run is None:
            raise RuntimeError("No lifecycle recovery run is active.")
        return self.persist(
            replace(
                run,
                phase=phase if phase is not None else run.phase,
                prepared_effect_counts=(
                    prepared_effect_counts if prepared_effect_counts is not None else run.prepared_effect_counts
                ),
                prepared_substrates=prepared_substrates if prepared_substrates is not None else run.prepared_substrates,
                completed_substrates=(
                    completed_substrates if completed_substrates is not None else run.completed_substrates
                ),
            )
        )

    def clear(self) -> None:
        clear_lifecycle_run(self.repo_path)
        self.set_current(None)
