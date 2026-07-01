"""Durable pending records for required v2 workspace authority."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._world_refs import encode_ref_component

WORKSPACE_AUTHORITY_PENDING_SCHEMA = "vcscore/workspace-authority-pending/v1"

WorkspaceAuthorityPhase = Literal["opened", "scalar_committed", "v2_selected"]


@dataclass(frozen=True)
class WorkspaceAuthorityPending:
    """One scalar byte-journal mutation that still requires v2 workspace authority."""

    operation_id: str
    source_operation_id: str
    driver_command: str
    scope_name: str
    scope_ref: str
    scope_instance_id: str
    scope_world_id: str | None
    expected_input_world_oid: str | None
    scalar_source_commit: str | None
    workspace_output_binding: str = "workspace"
    phase: WorkspaceAuthorityPhase = "opened"
    advance_materialized: bool = False
    retry_count: int = 0
    created_at_unix_ns: int = 0
    updated_at_unix_ns: int = 0
    schema: str = WORKSPACE_AUTHORITY_PENDING_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORKSPACE_AUTHORITY_PENDING_SCHEMA:
            raise ValueError("workspace authority pending record has unsupported schema")
        for field_name in (
            "operation_id",
            "source_operation_id",
            "driver_command",
            "scope_name",
            "scope_ref",
            "scope_instance_id",
            "workspace_output_binding",
        ):
            _require_non_empty_str(getattr(self, field_name), field_name)
        if self.phase not in {"opened", "scalar_committed", "v2_selected"}:
            raise ValueError(f"workspace authority phase is unsupported: {self.phase!r}")
        if self.scope_world_id is not None:
            _require_non_empty_str(self.scope_world_id, "scope_world_id")
        if self.expected_input_world_oid is not None:
            _require_non_empty_str(self.expected_input_world_oid, "expected_input_world_oid")
        if self.scalar_source_commit is not None:
            _require_non_empty_str(self.scalar_source_commit, "scalar_source_commit")
        if self.retry_count < 0:
            raise ValueError("workspace authority retry_count must be non-negative")

    def with_update(self, **changes: object) -> WorkspaceAuthorityPending:
        now = time.time_ns()
        if self.created_at_unix_ns == 0 and "created_at_unix_ns" not in changes:
            changes["created_at_unix_ns"] = now
        changes["updated_at_unix_ns"] = now
        # ``replace`` is generic on field types; ``**changes`` arrives as
        # ``dict[str, object]`` because callers pass heterogeneous values
        # (str / Literal / int / bool). Callers are responsible for value
        # types; the cast type-erases at this boundary only.
        return replace(self, **cast("dict[str, Any]", changes))

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}

    @classmethod
    def from_dict(cls, data: object) -> WorkspaceAuthorityPending:
        if not isinstance(data, dict):
            raise TypeError("workspace authority pending record must be an object")
        return cls(
            operation_id=_required_str(data, "operation_id"),
            source_operation_id=_required_str(data, "source_operation_id"),
            driver_command=_required_str(data, "driver_command"),
            scope_name=_required_str(data, "scope_name"),
            scope_ref=_required_str(data, "scope_ref"),
            scope_instance_id=_required_str(data, "scope_instance_id"),
            scope_world_id=_optional_str(data, "scope_world_id"),
            expected_input_world_oid=_optional_str(data, "expected_input_world_oid"),
            scalar_source_commit=_optional_str(data, "scalar_source_commit"),
            workspace_output_binding=_optional_str(data, "workspace_output_binding") or "workspace",
            phase=_phase(data.get("phase", "opened")),
            advance_materialized=_bool(data.get("advance_materialized", False), "advance_materialized"),
            retry_count=_int(data.get("retry_count", 0), "retry_count"),
            created_at_unix_ns=_int(data.get("created_at_unix_ns", 0), "created_at_unix_ns"),
            updated_at_unix_ns=_int(data.get("updated_at_unix_ns", 0), "updated_at_unix_ns"),
            schema=_required_str(data, "schema"),
        )


def pending_workspace_authority_records(repo_path: str | Path) -> tuple[WorkspaceAuthorityPending, ...]:
    from vcs_core._workspace_authority_inventory import probe_workspace_authority_pending

    records: list[WorkspaceAuthorityPending] = []
    for item in probe_workspace_authority_pending(repo_path):
        if item.health.validity != "valid":
            continue
        locator = item.locator
        if locator is None:
            continue
        records.append(WorkspaceAuthorityPending.from_dict(json.loads(Path(locator).read_text())))
    return tuple(records)


def read_pending_workspace_authority(repo_path: str | Path, operation_id: str) -> WorkspaceAuthorityPending:
    return WorkspaceAuthorityPending.from_dict(json.loads(_pending_path(repo_path, operation_id).read_text()))


def pending_workspace_authority_records_for_scope(
    repo_path: str | Path,
    scope_ref: str,
) -> tuple[WorkspaceAuthorityPending, ...]:
    return tuple(record for record in pending_workspace_authority_records(repo_path) if record.scope_ref == scope_ref)


def write_pending_workspace_authority(repo_path: str | Path, pending: WorkspaceAuthorityPending) -> None:
    _reject_pending_locator_collision(repo_path, pending)
    path = _pending_path(repo_path, pending.operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(pending.to_dict(), sort_keys=True, separators=(",", ":"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload)
    tmp.replace(path)


def clear_pending_workspace_authority(repo_path: str | Path, operation_id: str) -> None:
    _pending_path(repo_path, operation_id).unlink(missing_ok=True)


def clear_pending_workspace_authority_for_scope(repo_path: str | Path, scope_ref: str) -> tuple[str, ...]:
    cleared: list[str] = []
    for record in pending_workspace_authority_records_for_scope(repo_path, scope_ref):
        clear_pending_workspace_authority(repo_path, record.operation_id)
        cleared.append(record.operation_id)
    return tuple(cleared)


def workspace_authority_operation_labels(repo_path: str | Path) -> tuple[str, ...]:
    from vcs_core._workspace_authority_inventory import probe_workspace_authority_pending

    labels: list[str] = []
    for item in probe_workspace_authority_pending(repo_path):
        operation_id = item.fields.get("operation_id")
        if isinstance(operation_id, str) and operation_id:
            labels.append(operation_id)
            continue
        payload_operation_id = item.fields.get("payload_operation_id")
        if isinstance(payload_operation_id, str) and payload_operation_id:
            labels.append(payload_operation_id)
            continue
        labels.append(f"{Path(str(item.locator)).name} ({item.health.status})")
    return tuple(labels)


def _pending_root(repo_path: str | Path) -> Path:
    return Path(repo_path) / "workspace-authority" / "pending"


def _pending_path(repo_path: str | Path, operation_id: str) -> Path:
    return _pending_root(repo_path) / f"{encode_ref_component(operation_id)}.json"


def _reject_pending_locator_collision(repo_path: str | Path, pending: WorkspaceAuthorityPending) -> None:
    path = _pending_path(repo_path, pending.operation_id)
    if not path.exists():
        return
    try:
        existing = WorkspaceAuthorityPending.from_dict(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise InvalidRepositoryStateError(
            f"Cannot write workspace authority {pending.operation_id!r}: pending locator {path} is unreadable"
        ) from exc
    if existing.operation_id != pending.operation_id:
        raise InvalidRepositoryStateError(
            "Cannot write workspace authority "
            f"{pending.operation_id!r}: pending locator {path} already claims {existing.operation_id!r}"
        )


def _require_non_empty_str(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"workspace authority field {field_name!r} must be a non-empty string")


def _required_str(data: dict[str, object], field_name: str) -> str:
    value = data.get(field_name)
    _require_non_empty_str(value, field_name)
    return str(value)


def _optional_str(data: dict[str, object], field_name: str) -> str | None:
    value = data.get(field_name)
    if value is None:
        return None
    _require_non_empty_str(value, field_name)
    return str(value)


def _bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError(f"workspace authority field {field_name!r} must be boolean")


def _int(value: object, field_name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"workspace authority field {field_name!r} must be an integer")


def _phase(value: object) -> WorkspaceAuthorityPhase:
    if value in {"opened", "scalar_committed", "v2_selected"}:
        return value  # type: ignore[return-value]
    raise ValueError(f"workspace authority phase is unsupported: {value!r}")
