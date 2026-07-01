"""Small cleanup guards for open operation refs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vcs_core.store import Store


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationArchiveResult:
    archive_ref: str
    recording_error: str | None = None


@dataclass
class OpenOperationGuard:
    """Best-effort abort guard for operation refs opened inside a transaction."""

    store: Store
    operation: Any | None = None

    def arm(self, operation: Any) -> Any:
        self.operation = operation
        return operation

    def disarm(self) -> None:
        self.operation = None

    def abort(self, *, metadata: dict[str, Any] | None = None, status: str = "error") -> str | None:
        operation = self.operation
        if operation is None:
            return None
        try:
            archive_ref = self.store.abort_operation(operation, metadata=metadata, status=status)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to abort open operation %s", operation.durable_id, exc_info=True)
            return None
        finally:
            self.operation = None
        return archive_ref

    def finalize(self, *, scope: Any, metadata: dict[str, Any] | None = None) -> str:
        operation = self.operation
        if operation is None:
            raise RuntimeError("Cannot finalize an open operation guard before it is armed.")
        archive_ref = self.store.finalize_operation(operation, scope=scope, metadata=metadata)
        self.operation = None
        return archive_ref


def archive_operation_with_fallback(
    store: Store,
    operation: Any,
    *,
    metadata: dict[str, Any],
    status: str,
    fallback_error_prefix: str,
) -> OperationArchiveResult:
    """Archive a completed operation, aborting with diagnostic metadata if completion fails."""
    try:
        archive_ref = store.complete_operation_to_archive(
            operation,
            metadata=metadata,
            status=status,
        )
        return OperationArchiveResult(archive_ref=archive_ref)
    except Exception as exc:  # noqa: BLE001
        fallback_error = f"{fallback_error_prefix}: {str(exc) or exc.__class__.__name__}"
        fallback_metadata = _metadata_with_recording_failure(metadata, fallback_error)
        archive_ref = _archive_ref_for_operation(operation)
        if _ref_exists(store, archive_ref):
            _delete_ref_if_required_ref_exists(store, ref=operation.ref, required_ref=archive_ref)
            return OperationArchiveResult(archive_ref=archive_ref, recording_error=fallback_error)
        fallback_operation = _find_open_operation_by_durable_id(store, operation.durable_id, kind=operation.kind)
        archive_ref = store.abort_operation(
            fallback_operation,
            metadata=fallback_metadata,
            status="error",
        )
        return OperationArchiveResult(archive_ref=archive_ref, recording_error=fallback_error)


def _metadata_with_recording_failure(metadata: dict[str, Any], error: str) -> dict[str, Any]:
    command = metadata.get("command")
    if isinstance(command, dict):
        return {
            **metadata,
            "command": {
                **command,
                "recording_status": "failed",
                "recording_error": error,
            },
        }
    return {
        **metadata,
        "recording_status": "failed",
        "recording_error": error,
    }


def _find_open_operation_by_durable_id(store: Store, durable_id: str, *, kind: str) -> Any:
    for candidate in store.list_open_operations():
        if candidate.durable_id == durable_id and candidate.kind == kind:
            return candidate
    raise ValueError(f"No open operation matches operation id {durable_id!r}.")


def _archive_ref_for_operation(operation: Any) -> str:
    return f"refs/vcscore/archive/ops/{operation.durable_id}"


def _ref_exists(store: Store, ref: str) -> bool:
    return store.ref_exists(ref)


def _delete_ref_if_required_ref_exists(store: Store, *, ref: str, required_ref: str) -> bool:
    try:
        return store._delete_ref_if_ref_exists(ref=ref, required_ref=required_ref)
    except Exception:
        logger.warning("Failed to delete duplicate open operation ref %s after archive publication", ref, exc_info=True)
        raise
