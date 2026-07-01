from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from vcs_core._errors import (
    LifecycleRecoveryRequiredError,
    OpenScopeError,
    SiblingGroupRecoveryRequiredError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from vcs_core._lifecycle_run import LifecycleRun
    from vcs_core._query_readiness import ReadinessOperationAuthority, RuntimeAdmissionContext


RecoveryBlockerKind = Literal["sibling_group"]


@dataclass(frozen=True)
class RecoveryBlocker:
    """Typed internal recovery blocker consumed by mutation admission."""

    kind: RecoveryBlockerKind
    subject: str
    status: str
    group_id: str | None = None
    ref: str | None = None
    reason: str | None = None


def blocker_subjects(blockers: tuple[RecoveryBlocker, ...]) -> tuple[str, ...]:
    return tuple(blocker.subject for blocker in blockers)


@dataclass(frozen=True, kw_only=True)
class MutationAdmission:
    """Coordinator-owned admission checks for durable mutations."""

    ensure_no_interrupted_lifecycle: Callable[[str], None] = lambda _attempted: None
    ensure_no_open_operation: Callable[[str], None] = lambda _attempted: None
    sibling_group_blockers: Callable[[], tuple[RecoveryBlocker, ...]] = lambda: ()
    active_scope_names: Callable[[], tuple[str, ...]] = lambda: ()
    # Invoked for side effect (raises if the command class is blocked). Required:
    # readiness is the sole admission path (the legacy manual checks were removed
    # when the strangler was finished), so a None here would silently admit.
    readiness_admission: Callable[[str, str, tuple[ReadinessOperationAuthority, ...], str | None], None]
    runtime_readiness_admission: (
        Callable[[str, str, tuple[ReadinessOperationAuthority, ...], str | None, RuntimeAdmissionContext | None], None]
        | None
    ) = None

    @classmethod
    def from_sources(
        cls,
        *,
        lifecycle_run: Callable[[], LifecycleRun | None],
        open_operation_label: Callable[[], str | None],
        sibling_group_blockers: Callable[[], tuple[RecoveryBlocker, ...]] = lambda: (),
        readiness_admission: Callable[[str, str, tuple[ReadinessOperationAuthority, ...], str | None], None],
        runtime_readiness_admission: Callable[
            [str, str, tuple[ReadinessOperationAuthority, ...], str | None, RuntimeAdmissionContext | None],
            None,
        ]
        | None = None,
    ) -> MutationAdmission:
        def ensure_no_interrupted_lifecycle(attempted: str) -> None:
            run = lifecycle_run()
            if run is None:
                return
            raise LifecycleRecoveryRequiredError(
                attempted=attempted,
                operation=run.operation,
                scope_name=run.scope.name,
                phase=run.phase,
            )

        def ensure_no_open_operation(attempted: str) -> None:
            operation_label = open_operation_label()
            if operation_label is None:
                return
            raise RuntimeError(f"Cannot {attempted} while operation {operation_label!r} is open.")

        return cls(
            ensure_no_interrupted_lifecycle=ensure_no_interrupted_lifecycle,
            ensure_no_open_operation=ensure_no_open_operation,
            sibling_group_blockers=sibling_group_blockers,
            readiness_admission=readiness_admission,
            runtime_readiness_admission=runtime_readiness_admission,
        )

    def require_no_sibling_group_blockers(self, attempted: str) -> None:
        blockers = self.sibling_group_blockers()
        if not blockers:
            return
        raise SiblingGroupRecoveryRequiredError(attempted=attempted, groups=list(blocker_subjects(blockers)))

    def require_no_interrupted_lifecycle(self, attempted: str) -> None:
        self.ensure_no_interrupted_lifecycle(attempted)

    def require_no_open_operation(self, attempted: str) -> None:
        self.ensure_no_open_operation(attempted)

    def require_lifecycle_mutation_allowed(self, attempted: str) -> None:
        self.ensure_no_interrupted_lifecycle(attempted)
        self.ensure_no_open_operation(attempted)
        self.readiness_admission("vcscore.lifecycle", attempted, (), None)

    def require_retained_output_selection_allowed(self, *, scope_selector: str | None = None) -> None:
        attempted = "select retained output"
        self.ensure_no_interrupted_lifecycle(attempted)
        self.ensure_no_open_operation(attempted)
        self.readiness_admission("vcscore.retained-output-selection", attempted, (), scope_selector)

    def require_runtime_mutation_allowed(
        self,
        attempted: str,
        *,
        authorized_operations: tuple[ReadinessOperationAuthority, ...] = (),
        scope_selector: str | None = None,
        runtime_admission_context: RuntimeAdmissionContext | None = None,
    ) -> None:
        self.ensure_no_interrupted_lifecycle(attempted)
        if self.runtime_readiness_admission is not None:
            self.runtime_readiness_admission(
                "vcscore.runtime",
                attempted,
                authorized_operations,
                scope_selector,
                runtime_admission_context,
            )
            return
        self.readiness_admission("vcscore.runtime", attempted, authorized_operations, scope_selector)

    def require_push_allowed(self) -> None:
        self.ensure_no_interrupted_lifecycle("push")
        self.ensure_no_open_operation("push")
        self._require_no_active_scopes_for_push()
        self.readiness_admission("vcscore.push-status", "push", (), None)

    def _require_no_active_scopes_for_push(self) -> None:
        active_scope_names = self.active_scope_names()
        if active_scope_names:
            msg = "push() requires no live child branches. Merge or discard child scopes before materializing."
            raise OpenScopeError(msg)

    def require_reset_to_materialized_allowed(self) -> None:
        self.ensure_no_interrupted_lifecycle("reset to materialized")
        self.readiness_admission("vcscore.reset-materialized", "reset to materialized", (), None)

    def require_recovery_cleanup_allowed(self, attempted: str) -> None:
        self.ensure_no_interrupted_lifecycle(attempted)
        self.require_no_sibling_group_blockers(attempted)
