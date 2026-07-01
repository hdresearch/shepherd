from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._mutation_admission import MutationAdmission, RecoveryBlocker
from vcs_core._readiness_admission import require_readiness_allowed

if TYPE_CHECKING:
    from collections.abc import Callable

    from vcs_core._query_readiness import ReadinessOperationAuthority, RuntimeAdmissionContext
    from vcs_core.vcscore import VcsCore


def mutation_admission(
    owner: VcsCore,
    *,
    sibling_group_blockers: Callable[[], tuple[RecoveryBlocker, ...]],
) -> MutationAdmission:
    """Build mutation admission from the owning VcsCore coordinator."""

    def open_operation_label() -> str | None:
        operation = owner._pipeline.current_operation()
        if operation is None:
            return None
        return owner._format_operation_label(operation)

    def readiness_admission(
        command: str,
        attempted: str,
        authorized_operations: tuple[ReadinessOperationAuthority, ...],
        scope_selector: str | None,
    ) -> None:
        require_readiness_allowed(
            owner,
            command=command,
            attempted=attempted,
            authorized_operations=authorized_operations,
            scope_selector=scope_selector,
        )

    def runtime_readiness_admission(
        command: str,
        attempted: str,
        authorized_operations: tuple[ReadinessOperationAuthority, ...],
        scope_selector: str | None,
        runtime_admission_context: RuntimeAdmissionContext | None,
    ) -> None:
        require_readiness_allowed(
            owner,
            command=command,
            attempted=attempted,
            authorized_operations=authorized_operations,
            scope_selector=scope_selector,
            runtime_admission_context=runtime_admission_context,
        )

    return MutationAdmission.from_sources(
        lifecycle_run=lambda: owner._lifecycle_run,
        open_operation_label=open_operation_label,
        sibling_group_blockers=sibling_group_blockers,
        readiness_admission=readiness_admission,
        runtime_readiness_admission=runtime_readiness_admission,
    )
