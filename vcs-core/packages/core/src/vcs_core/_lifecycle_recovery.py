from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from vcs_core._lifecycle_progress import LifecycleProgress
    from vcs_core._lifecycle_run import LifecycleRun
    from vcs_core._lifecycle_state import LifecycleRunState
    from vcs_core.types import ScopeInfo


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LifecycleRecoveryResult:
    """Callback information emitted after a recovered lifecycle completes."""

    callback_kind: str
    scope_name: str


@dataclass(frozen=True)
class LifecycleRecoveryDependencies:
    """Explicit dependencies needed to resume an interrupted lifecycle run."""

    state: LifecycleRunState
    progress: LifecycleProgress
    substrates: Sequence[object]
    scope_ref_exists: Callable[[ScopeInfo], bool]
    load_context: Callable[[LifecycleRun], tuple[ScopeInfo, ScopeInfo]]
    restore_substrate_state: Callable[[LifecycleRun, ScopeInfo, ScopeInfo], None]
    snapshot_merge_effects: Callable[[ScopeInfo, ScopeInfo], None]
    snapshot_discard_effects: Callable[[ScopeInfo, ScopeInfo], None]
    complete_merge: Callable[[ScopeInfo, ScopeInfo], str]
    complete_discard: Callable[[ScopeInfo, ScopeInfo], str]
    complete_seal: Callable[[ScopeInfo, ScopeInfo], str]


class LifecycleRecovery:
    """Coordinator-owned orchestration for lifecycle recovery."""

    def __init__(self, deps: LifecycleRecoveryDependencies) -> None:
        self._deps = deps

    def recover(self, mode: str = "resume") -> LifecycleRecoveryResult:
        if mode != "resume":
            raise ValueError(f"Unknown lifecycle recovery mode: {mode!r}")

        run = self._deps.state.current()
        if run is None:
            raise RuntimeError("No lifecycle recovery run is active.")

        scope, parent = self._deps.load_context(run)
        if self._deps.scope_ref_exists(scope):
            self._deps.restore_substrate_state(run, scope, parent)

        if run.operation == "merge":
            return self._recover_merge(run, scope, parent)
        if run.operation == "discard":
            return self._recover_discard(run, scope, parent)
        if run.operation == "seal":
            return self._recover_seal(scope, parent)

        raise RuntimeError(f"Unknown lifecycle operation: {run.operation!r}")

    def _recover_merge(
        self,
        run: LifecycleRun,
        scope: ScopeInfo,
        parent: ScopeInfo,
    ) -> LifecycleRecoveryResult:
        if self._deps.scope_ref_exists(scope) and run.phase == "prepare_merge_effects":
            self._deps.snapshot_merge_effects(scope, parent)
            self._deps.state.update(phase="commit_substrates")

        for substrate in reversed(self._deps.substrates):
            if not hasattr(substrate, "commit_merge"):
                continue
            if self._is_completed(substrate):
                continue
            cast("Any", substrate).commit_merge(scope.name, parent_scope=parent)
            self._mark_completed(substrate)
        return LifecycleRecoveryResult(
            callback_kind="merge",
            scope_name=self._deps.complete_merge(scope, parent),
        )

    def _recover_discard(
        self,
        run: LifecycleRun,
        scope: ScopeInfo,
        parent: ScopeInfo,
    ) -> LifecycleRecoveryResult:
        if self._deps.scope_ref_exists(scope) and run.phase == "prepare_discard_effects":
            self._deps.snapshot_discard_effects(scope, parent)
            self._deps.state.update(phase="discard_substrates")

        failures: list[tuple[str, Exception]] = []
        for substrate in reversed(self._deps.substrates):
            if not hasattr(substrate, "discard"):
                continue
            if self._is_completed(substrate):
                continue
            try:
                cast("Any", substrate).discard(scope.name)
            except Exception as exc:  # noqa: BLE001
                failures.append((self._substrate_name(substrate), exc))
                logger.warning(
                    "Substrate %s raised during recovery discard of scope %r; continuing cleanup",
                    self._substrate_name(substrate),
                    scope.name,
                    exc_info=True,
                )
            else:
                self._mark_completed(substrate)

        if failures:
            failed = ", ".join(name for name, _error in failures)
            msg = (
                f"Discard recovery for scope {scope.name!r} failed in substrate(s): {failed}. "
                "Scope remains active for recovery."
            )
            raise RuntimeError(msg) from failures[0][1]

        return LifecycleRecoveryResult(
            callback_kind="discard",
            scope_name=self._deps.complete_discard(scope, parent),
        )

    def _recover_seal(self, scope: ScopeInfo, parent: ScopeInfo) -> LifecycleRecoveryResult:
        return LifecycleRecoveryResult(
            callback_kind="seal",
            scope_name=self._deps.complete_seal(scope, parent),
        )

    def _is_completed(self, substrate: object) -> bool:
        run = self._deps.state.current()
        return run is not None and self._substrate_name(substrate) in run.completed_substrates

    def _mark_completed(self, substrate: object) -> None:
        self._deps.progress.mark_completed_substrate(self._substrate_name(substrate))

    @staticmethod
    def _substrate_name(substrate: object) -> str:
        name = getattr(substrate, "name", None)
        if isinstance(name, str):
            return name
        return repr(substrate)
