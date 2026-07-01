"""Total inventory probes for workspace-authority pending files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from vcs_core._query_inventory import (
    WORKSPACE_AUTHORITY_FILE_UNREADABLE,
    WORKSPACE_AUTHORITY_IDENTITY_MISMATCH,
    WORKSPACE_AUTHORITY_MISSING_FILE,
    WORKSPACE_AUTHORITY_PAYLOAD_CORRUPT,
    WORKSPACE_AUTHORITY_SCHEMA_MISMATCH,
    Health,
    InventoryIssue,
    InventoryItem,
    issue_id,
    missing,
    present_invalid,
    present_valid,
)
from vcs_core._query_locators import classify_locator_component
from vcs_core._workspace_authority import (
    WORKSPACE_AUTHORITY_PENDING_SCHEMA,
    WorkspaceAuthorityPending,
    _pending_path,
    _pending_root,
)
from vcs_core._world_refs import encode_ref_component


def probe_workspace_authority_pending(repo_path: str | Path) -> tuple[InventoryItem, ...]:
    """Enumerate present workspace-authority pending files without dropping invalid records."""
    root = _pending_root(repo_path)
    if not root.exists():
        return ()
    return tuple(probe_workspace_authority_pending_file(path) for path in sorted(root.glob("*.json")))


def workspace_authority_pending_label(item: InventoryItem) -> str:
    """Project one workspace-authority inventory item to the legacy label shape."""
    operation_id = item.fields.get("operation_id")
    if isinstance(operation_id, str) and operation_id:
        return operation_id
    payload_operation_id = item.fields.get("payload_operation_id")
    if isinstance(payload_operation_id, str) and payload_operation_id:
        return payload_operation_id
    return f"{Path(str(item.locator)).name} ({item.health.status})"


def probe_workspace_authority_pending_record(repo_path: str | Path, operation_id: str) -> InventoryItem:
    """Classify one expected pending record, including targeted absence."""
    path = _pending_path(repo_path, operation_id)
    if path.exists():
        return probe_workspace_authority_pending_file(path, expected_operation_id=operation_id)
    item_id = _item_id(path)
    issue = _issue(item_id, WORKSPACE_AUTHORITY_MISSING_FILE, f"workspace authority file is missing: {path}", path)
    return _item(
        item_id=item_id,
        path=path,
        health=missing(
            issue_codes=(WORKSPACE_AUTHORITY_MISSING_FILE,), lifecycle="recoverable", authority_role="authoritative"
        ),
        fields=_locator_fields(path),
        issues=(issue,),
    )


def probe_workspace_authority_pending_file(
    path: str | Path,
    *,
    expected_operation_id: str | None = None,
) -> InventoryItem:
    """Classify one concrete workspace-authority pending file."""
    file_path = Path(path)
    item_id = _item_id(file_path)
    fields = _locator_fields(file_path)
    source_identity: dict[str, object] = {"path": str(file_path)}

    try:
        stat = file_path.stat()
        raw = file_path.read_bytes()
    except OSError as exc:
        return _invalid_item(
            item_id=item_id,
            path=file_path,
            code=WORKSPACE_AUTHORITY_FILE_UNREADABLE,
            primary_issue="unreadable",
            message=str(exc),
            fields=fields,
            source_identity=source_identity,
        )
    source_identity.update(
        {
            "file_size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "content_digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        }
    )

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _invalid_item(
            item_id=item_id,
            path=file_path,
            code=WORKSPACE_AUTHORITY_PAYLOAD_CORRUPT,
            primary_issue="corrupt",
            message=str(exc),
            fields=fields,
            source_identity=source_identity,
        )
    if not isinstance(payload, dict):
        return _invalid_item(
            item_id=item_id,
            path=file_path,
            code=WORKSPACE_AUTHORITY_PAYLOAD_CORRUPT,
            primary_issue="corrupt",
            message="workspace authority pending record must be an object",
            fields=fields,
            source_identity=source_identity,
        )
    fields.update(_payload_fields(payload))
    if payload.get("schema") != WORKSPACE_AUTHORITY_PENDING_SCHEMA:
        return _invalid_item(
            item_id=item_id,
            path=file_path,
            code=WORKSPACE_AUTHORITY_SCHEMA_MISMATCH,
            primary_issue="schema_mismatch",
            message=f"unsupported workspace authority schema: {payload.get('schema')!r}",
            fields=fields,
            source_identity=source_identity,
        )
    try:
        pending = WorkspaceAuthorityPending.from_dict(payload)
    except (TypeError, ValueError) as exc:
        return _invalid_item(
            item_id=item_id,
            path=file_path,
            code=WORKSPACE_AUTHORITY_SCHEMA_MISMATCH,
            primary_issue="schema_mismatch",
            message=str(exc),
            fields=fields,
            source_identity=source_identity,
        )

    fields.update(_record_fields(pending))
    identity_issue = _identity_issue(file_path, pending.operation_id, expected_operation_id=expected_operation_id)
    if identity_issue is not None:
        fields["identity_match"] = False
        return _invalid_item(
            item_id=item_id,
            path=file_path,
            code=WORKSPACE_AUTHORITY_IDENTITY_MISMATCH,
            primary_issue="identity_mismatch",
            message=identity_issue,
            fields=fields,
            source_identity=source_identity,
        )
    fields["identity_match"] = True
    return _item(
        item_id=item_id,
        path=file_path,
        health=present_valid(authority_role="authoritative"),
        fields=fields,
        source_identity=source_identity,
    )


def _locator_fields(path: Path) -> dict[str, object]:
    stem = path.name.removesuffix(".json")
    component = classify_locator_component(stem)
    fields = {
        "filename": path.name,
        "locator_component": component.raw_component,
        "locator_encoding": component.encoding,
        "locator_reversible": component.reversible,
    }
    if component.decoded_value is not None:
        fields["locator_operation_id"] = component.decoded_value
    if component.issue is not None:
        fields["locator_issue"] = component.issue
    return fields


def _payload_fields(payload: dict[str, Any]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, bool)) or value is None:
            fields[f"payload_{key}"] = value
    return fields


def _record_fields(pending: WorkspaceAuthorityPending) -> dict[str, object]:
    return {
        "operation_id": pending.operation_id,
        "source_operation_id": pending.source_operation_id,
        "driver_command": pending.driver_command,
        "scope_name": pending.scope_name,
        "scope_ref": pending.scope_ref,
        "scope_instance_id": pending.scope_instance_id,
        "scope_world_id": pending.scope_world_id,
        "expected_input_world_oid": pending.expected_input_world_oid,
        "scalar_source_commit": pending.scalar_source_commit,
        "workspace_output_binding": pending.workspace_output_binding,
        "phase": pending.phase,
        "advance_materialized": pending.advance_materialized,
        "retry_count": pending.retry_count,
    }


def _identity_issue(path: Path, operation_id: str, *, expected_operation_id: str | None) -> str | None:
    stem = path.name.removesuffix(".json")
    component = classify_locator_component(stem)
    if expected_operation_id is not None and operation_id != expected_operation_id:
        return (
            f"workspace authority payload operation_id {operation_id!r} "
            f"disagrees with expected operation_id {expected_operation_id!r}"
        )
    if stem != encode_ref_component(operation_id):
        if component.encoding == "malformed":
            return "workspace authority pending filename is malformed"
        return "workspace authority payload operation_id disagrees with canonical locator"
    return None


def _item_id(path: Path) -> str:
    return f"workspace_authority_pending:file:{path.name}"


def _item(
    *,
    item_id: str,
    path: Path,
    health: Health,
    fields: dict[str, object],
    source_identity: dict[str, object] | None = None,
    issues: tuple[InventoryIssue, ...] = (),
) -> InventoryItem:
    return InventoryItem(
        id=item_id,
        domain="workspace_authority",
        kind="workspace_authority_pending",
        locator=str(path),
        source_kind="filesystem_file",
        source_store="coordinator",
        health=health,
        role=("authority", "recovery"),
        fields=fields,
        source_identity=dict(source_identity or {"path": str(path)}),
        issues=issues,
    )


def _invalid_item(
    *,
    item_id: str,
    path: Path,
    code: str,
    primary_issue: str,
    message: str,
    fields: dict[str, object],
    source_identity: dict[str, object],
    extra_issues: tuple[InventoryIssue, ...] = (),
) -> InventoryItem:
    issue = _issue(item_id, code, message, path)
    return _item(
        item_id=item_id,
        path=path,
        health=present_invalid(
            primary_issue=primary_issue,  # type: ignore[arg-type]
            issue_codes=(code,),
            authority_role="authoritative",
        ),
        fields=fields,
        source_identity=source_identity,
        issues=(*extra_issues, issue),
    )


def _issue(
    subject_id: str,
    code: str,
    message: str,
    path: Path,
) -> InventoryIssue:
    return InventoryIssue(
        id=issue_id(subject_id, code),
        code=code,
        message=message,
        subject_id=subject_id,
        locator=str(path),
        recovery_hint="Run `vcs-core inspect --domain workspace_authority --json` before recovery.",
    )
