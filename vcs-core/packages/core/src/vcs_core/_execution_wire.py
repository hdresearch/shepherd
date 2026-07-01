"""Explicit execution-surface IPC serialization helpers."""

from __future__ import annotations

from typing import Any

from vcs_core.types import CommitInfo, OperationHistory, OperationSummary, RecoverySnapshot, normalize_command_value


def serialize_operation_summary(summary: OperationSummary) -> dict[str, Any]:
    """Serialize one operation summary with an explicit transport field set."""
    return {
        "operation_id": normalize_command_value(summary.operation_id),
        "label": normalize_command_value(summary.label),
        "kind": normalize_command_value(summary.kind),
        "status": normalize_command_value(summary.status),
        "visibility": normalize_command_value(summary.visibility),
        "world_id": normalize_command_value(summary.world_id),
        "world_name": normalize_command_value(summary.world_name),
        "world_ref": normalize_command_value(summary.world_ref),
        "carrier_ref": normalize_command_value(summary.carrier_ref),
        "anchor_oid": normalize_command_value(summary.anchor_oid),
        "effect_count": normalize_command_value(summary.effect_count),
        "parent_operation_id": normalize_command_value(summary.parent_operation_id),
        "final_phase": normalize_command_value(summary.final_phase),
        "archived_via": normalize_command_value(summary.archived_via),
    }


def serialize_operation_summaries(
    summaries: tuple[OperationSummary, ...] | list[OperationSummary],
) -> list[dict[str, Any]]:
    """Serialize a sequence of operation summaries."""
    return [serialize_operation_summary(summary) for summary in summaries]


def serialize_commit_info(commit: CommitInfo) -> dict[str, Any]:
    """Serialize one committed effect entry."""
    return {
        "oid": normalize_command_value(commit.oid),
        "message": normalize_command_value(commit.message),
        "timestamp": normalize_command_value(commit.timestamp),
        "metadata": normalize_command_value(commit.metadata),
        "parent_oids": normalize_command_value(commit.parent_oids),
    }


def serialize_operation_history(history: OperationHistory) -> dict[str, Any]:
    """Serialize one operation history response body."""
    return {
        "summary": serialize_operation_summary(history.summary),
        "commits": [serialize_commit_info(commit) for commit in history.commits],
    }


def serialize_recovery_snapshot(snapshot: RecoverySnapshot) -> dict[str, Any]:
    """Serialize one recovery snapshot response body."""
    return {
        "orphaned_scope_refs": normalize_command_value(snapshot.orphaned_scope_refs),
        "open_operations": serialize_operation_summaries(snapshot.open_operations),
        "archived_recovery_operations": serialize_operation_summaries(snapshot.archived_recovery_operations),
        "orphaned_operations": serialize_operation_summaries(snapshot.orphaned_operations),
        "workspace_authority_pending": normalize_command_value(snapshot.workspace_authority_pending),
    }
