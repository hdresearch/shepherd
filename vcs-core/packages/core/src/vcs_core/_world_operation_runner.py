"""Private operation runner for v2 world storage workflows."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vcs_core._errors import InvalidRepositoryStateError

if TYPE_CHECKING:
    from vcs_core._world_operation_builder import PreparedWorldOperation
    from vcs_core._world_storage_manager import WorldStorageManager


@dataclass(frozen=True)
class WorldOperationResult:
    """Result of one private v2 world operation runner attempt."""

    operation_id: str
    status: str
    world_oid: str | None
    published: bool
    journal_family: str
    error: str | None = None


class WorldOperationRunner:
    """Coordinate one journaled world publication attempt through the manager."""

    def __init__(self, manager: WorldStorageManager) -> None:
        self._manager = manager

    def publish_prepared_world(self, prepared: PreparedWorldOperation) -> WorldOperationResult:
        """Create, publish, and close one prepared world operation with durable journal states."""
        prepared.require_candidate_tuples()
        finalized = prepared.finalize()
        operation_id = finalized.operation_id
        self._manager.open_operation_journal(
            operation_id=operation_id,
            operation_kind=finalized.operation_kind,
            target_ref=finalized.target_ref,
            input_world_oid=finalized.input_world_oid,
        )
        try:
            self._manager.record_operation_prepared(
                operation_id,
                prepared=prepared,
            )
            self._manager.record_operation_finalized(operation_id)
            world_oid = self._manager.create_world_from_prepared(prepared)
            self._manager.record_operation_world_committed(operation_id, world_oid=world_oid)
            if finalized.input_world_oid is None:
                publication_plan = self._manager.build_root_publication_plan(
                    ref=finalized.target_ref,
                    world_oid=world_oid,
                )
            else:
                publication_plan = self._manager.build_advance_publication_plan(
                    ref=finalized.target_ref,
                    world_oid=world_oid,
                    expected_oid=finalized.input_world_oid,
                    input_world_oid=finalized.input_world_oid,
                )
            self._manager.record_operation_publishing(
                operation_id,
                world_oid=world_oid,
                publication_plan=publication_plan,
            )
            prepared_publication = self._manager.prepare_publication(publication_plan)
            published = self._manager.advance_publication(prepared_publication)
            if not published:
                self._manager.complete_publication(prepared_publication)
                error = "world authority ref changed before publication"
                self._manager.fail_operation_journal(operation_id, error=error)
                return WorldOperationResult(
                    operation_id=operation_id,
                    status="failed",
                    world_oid=world_oid,
                    published=False,
                    journal_family="open",
                    error=error,
                )
            try:
                self._manager.complete_publication(prepared_publication)
                self._manager.record_operation_published(operation_id, world_oid=world_oid)
                self._manager.close_operation_journal(
                    operation_id,
                    world_oid=world_oid,
                )
            except Exception as exc:  # noqa: BLE001
                # The authority ref may already be published; keep any bookkeeping failure recoverable.
                return WorldOperationResult(
                    operation_id=operation_id,
                    status="recovery_required",
                    world_oid=world_oid,
                    published=True,
                    journal_family=self._journal_family(operation_id),
                    error=str(exc),
                )
            return WorldOperationResult(
                operation_id=operation_id,
                status="closed",
                world_oid=world_oid,
                published=True,
                journal_family="closed",
            )
        except Exception as exc:
            self._fail_open_operation(operation_id, exc)
            raise

    def _fail_open_operation(self, operation_id: str, exc: Exception) -> None:
        with contextlib.suppress(InvalidRepositoryStateError, KeyError, TypeError, ValueError):
            self._manager.fail_operation_journal(operation_id, error=str(exc))

    def _journal_family(self, operation_id: str) -> str:
        for family in ("closed", "archived", "open"):
            with contextlib.suppress(InvalidRepositoryStateError, KeyError, TypeError, ValueError):
                self._manager.read_operation_journal(operation_id, family=family)
                return family
        return "unknown"
