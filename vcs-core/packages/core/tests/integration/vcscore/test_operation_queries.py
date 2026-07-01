"""VcsCore execution/recovery read-surface integration tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from vcs_core._recovery_inventory import recovery_orphaned_operation_ids
from vcs_core._runtime_types import OperationRefInfo

if TYPE_CHECKING:
    from vcs_core.vcscore import VcsCore


def test_visible_operations_exclude_staged_runtime_activity(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-visible-queries")
    marker = mg.lifecycle_substrates[0]

    with mg.runtime_activity(
        scope=task,
        operation_label="visible-queries",
        operation_kind="marker.runtime",
    ):
        marker.mark("inside-visible", scope=task)  # type: ignore[attr-defined]

        assert mg.visible_operations(ref=task.ref) == []
        open_operations = mg.open_operations(scope=task)
        assert len(open_operations) == 1
        assert open_operations[0].visibility == "staged"
        assert open_operations[0].status == "open"

        snapshot = mg.recovery_snapshot()
        assert len(snapshot.open_operations) == 1
        assert snapshot.open_operations[0].operation_id == open_operations[0].operation_id

    visible = mg.visible_operations(ref=task.ref)
    assert len(visible) == 1
    assert visible[0].visibility == "visible"
    assert visible[0].status == "ok"
    assert visible[0].label == "visible-queries"

    resolved = mg.resolve_operation_history(visible[0].operation_id, scope=task)
    assert resolved.summary.operation_id == visible[0].operation_id
    assert resolved.summary.label == visible[0].label


def test_resolve_operation_history_allows_single_visible_carrier_ref(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-single-visible-carrier")

    with mg.runtime_activity(
        scope=task,
        operation_label="single-visible",
        operation_kind="marker.runtime",
    ):
        pass

    history = mg.resolve_operation_history(task.ref, scope=task)

    assert history.summary.carrier_ref == task.ref
    assert history.summary.label == "single-visible"


def test_archived_operations_surface_failed_runtime_activity(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-archived-queries")
    marker = mg.lifecycle_substrates[0]

    with (
        pytest.raises(RuntimeError, match="boom"),
        mg.runtime_activity(
            scope=task,
            operation_label="archived-queries",
            operation_kind="marker.runtime",
            failure_policy="abort_archive",
        ),
    ):
        marker.mark("inside-archived", scope=task)  # type: ignore[attr-defined]
        raise RuntimeError("boom")

    assert mg.visible_operations(ref=task.ref) == []

    archived = mg.archived_operations(world_id=task.world_id)
    assert len(archived) == 1
    assert archived[0].visibility == "archived"
    assert archived[0].status == "error"
    assert archived[0].label == "archived-queries"

    snapshot = mg.recovery_snapshot()
    assert any(summary.operation_id == archived[0].operation_id for summary in snapshot.archived_recovery_operations)


def test_archived_operations_include_discarded_world_visible_history(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-discarded-world-queries")
    marker = mg.lifecycle_substrates[0]

    with mg.runtime_activity(
        scope=task,
        operation_id="discarded-world-op-id",
        operation_label="discarded-world-queries",
        operation_kind="marker.runtime",
    ):
        marker.mark("inside-discarded-world", scope=task)  # type: ignore[attr-defined]

    archive_name = mg.discard(task)
    assert archive_name == task.name

    archived = mg.archived_operations(world_id=task.world_id)
    assert len(archived) == 1
    assert archived[0].visibility == "archived"
    assert archived[0].status == "ok"
    assert archived[0].label == "discarded-world-queries"
    assert archived[0].archived_via == "discarded_world_ref"
    assert archived[0].carrier_ref.startswith("refs/vcscore/archive/task-discarded-world-queries-")

    history = mg.resolve_operation_history("discarded-world-op-id")
    assert history.summary.operation_id == "discarded-world-op-id"
    assert history.summary.visibility == "archived"
    assert history.summary.archived_via == "discarded_world_ref"
    assert history.summary.carrier_ref.startswith("refs/vcscore/archive/task-discarded-world-queries-")
    assert all(entry.metadata["type"] not in {"Init", "ScopeMerge", "DiscardSnapshot"} for entry in history.commits)

    archive_ref = next(
        ref
        for ref in mg.store.list_archive_refs()
        if ref.startswith("refs/vcscore/archive/task-discarded-world-queries-")
    )
    archived_log = mg.log(ref=archive_ref, max_count=10)
    assert any(entry.metadata["type"] == "DiscardSnapshot" for entry in archived_log)

    snapshot = mg.recovery_snapshot()
    assert snapshot.archived_recovery_operations == ()


def test_resolve_operation_history_finds_discarded_world_history_by_operation_id(
    mg: VcsCore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = mg.fork(mg.ground, "task-capped-discarded-history-target")
    with mg.runtime_activity(
        scope=target,
        operation_id="old-discarded-op-id",
        operation_label="old-discarded-op",
        operation_kind="marker.runtime",
    ):
        pass
    mg.discard(target)
    archive_ref = next(
        ref
        for ref in mg.store.list_archive_refs()
        if ref.startswith("refs/vcscore/archive/task-capped-discarded-history-target-")
    )

    def fail_if_full_projection_rebuild() -> tuple[object, ...]:
        raise AssertionError("discarded-history append must not rebuild all archived projection entries")

    monkeypatch.setattr(mg.store, "_build_archived_operation_projection_entries", fail_if_full_projection_rebuild)

    for idx in range(2):
        task = mg.fork(mg.ground, f"task-capped-discarded-history-{idx}")
        with mg.runtime_activity(
            scope=task,
            operation_id=f"newer-discarded-op-{idx}",
            operation_label=f"newer-discarded-op-{idx}",
            operation_kind="marker.runtime",
        ):
            pass
        mg.discard(task)

    by_id = mg.resolve_operation_history("old-discarded-op-id")
    by_ref = mg.resolve_operation_history(archive_ref)

    assert by_id.summary.operation_id == "old-discarded-op-id"
    assert by_id.summary.archived_via == "discarded_world_ref"
    assert by_ref.summary.operation_id == "old-discarded-op-id"
    assert by_ref.summary.carrier_ref == archive_ref


def test_resolve_operation_history_rejects_invalid_duplicate_visible_operation_id(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-duplicate-operation-id")
    marker = mg.lifecycle_substrates[0]

    with mg.runtime_activity(
        scope=task,
        operation_id="shared-operation-id",
        operation_label="duplicate-one",
        operation_kind="marker.runtime",
    ):
        marker.mark("first", scope=task)  # type: ignore[attr-defined]

    history = mg.resolve_operation_history("shared-operation-id", scope=task)
    assert history.commits[1].metadata["label"] == "first"

    second = mg.store.begin_operation(
        task.ref,
        handle_id="shared-operation-id",
        kind="marker.runtime",
        world_id=task.world_id or "",
        scope_instance_id=task.instance_id,
        operation_id="shared-operation-id",
        operation_label="shared-operation-id",
    )
    mg.store.append_operation_effect(second, "Marker", {"label": "second"}, substrate="marker")
    mg.store.finalize_operation(second, scope=task)

    with pytest.raises(
        RuntimeError, match="multiple visible operations share durable operation_id 'shared-operation-id'"
    ):
        mg.resolve_operation_history("shared-operation-id", scope=task)


def test_resolve_operation_history_rejects_ambiguous_visible_carrier_ref(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-ambiguous-visible-carrier")

    with mg.runtime_activity(
        scope=task,
        operation_label="first-visible",
        operation_kind="marker.runtime",
    ):
        pass

    with mg.runtime_activity(
        scope=task,
        operation_label="second-visible",
        operation_kind="marker.runtime",
    ):
        pass

    with pytest.raises(ValueError, match="Ambiguous operation selector"):
        mg.resolve_operation_history(task.ref, scope=task)


def test_runtime_activity_rejects_explicit_duplicate_operation_id(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-duplicate-operation-id-reject")

    with mg.runtime_activity(
        scope=task,
        operation_id="shared-operation-id",
        operation_label="first",
        operation_kind="marker.runtime",
    ):
        pass

    with (
        pytest.raises(ValueError, match="already present in repository history"),
        mg.runtime_activity(
            scope=task,
            operation_id="shared-operation-id",
            operation_label="second",
            operation_kind="marker.runtime",
        ),
    ):
        pass


def test_runtime_activity_rejects_duplicate_operation_id_present_on_discarded_world_archive(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-duplicate-operation-id-discarded")

    with mg.runtime_activity(
        scope=task,
        operation_id="shared-operation-id",
        operation_label="first",
        operation_kind="marker.runtime",
    ):
        pass

    mg.discard(task)

    next_task = mg.fork(mg.ground, "task-duplicate-operation-id-discarded-next")
    with (
        pytest.raises(ValueError, match="already present in repository history"),
        mg.runtime_activity(
            scope=next_task,
            operation_id="shared-operation-id",
            operation_label="second",
            operation_kind="marker.runtime",
        ),
    ):
        pass


def test_resolve_operation_history_excludes_promoted_child_commits_from_parent(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-nested-visible-history")
    marker = mg.lifecycle_substrates[0]

    with mg.runtime_activity(
        scope=task,
        operation_id="parent-op",
        operation_label="parent",
        operation_kind="marker.runtime",
    ):
        marker.mark("parent-1", scope=task)  # type: ignore[attr-defined]
        with mg.runtime_activity(
            scope=task,
            operation_id="child-op",
            operation_label="child",
            operation_kind="marker.runtime",
            boundary_policy="forced_child",
        ):
            marker.mark("child-1", scope=task)  # type: ignore[attr-defined]
        marker.mark("parent-2", scope=task)  # type: ignore[attr-defined]

    parent_history = mg.resolve_operation_history("parent-op", scope=task)
    child_history = mg.resolve_operation_history("child-op", scope=task)

    assert [(entry.metadata["type"], entry.metadata.get("label")) for entry in parent_history.commits] == [
        ("OperationCompleted", None),
        ("Marker", "parent-2"),
        ("Marker", "parent-1"),
        ("OperationStarted", None),
    ]
    assert [(entry.metadata["type"], entry.metadata.get("label")) for entry in child_history.commits] == [
        ("OperationCompleted", None),
        ("Marker", "child-1"),
        ("OperationStarted", None),
    ]


def test_resolve_operation_history_rejects_ambiguous_discarded_world_carrier_ref(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-ambiguous-discarded-carrier")

    with mg.runtime_activity(
        scope=task,
        operation_id="discarded-one",
        operation_label="first-discarded",
        operation_kind="marker.runtime",
    ):
        pass

    with mg.runtime_activity(
        scope=task,
        operation_id="discarded-two",
        operation_label="second-discarded",
        operation_kind="marker.runtime",
    ):
        pass

    mg.discard(task)
    archive_ref = next(
        ref
        for ref in mg.store.list_archive_refs()
        if ref.startswith("refs/vcscore/archive/task-ambiguous-discarded-carrier-")
    )

    with pytest.raises(ValueError, match="Ambiguous operation selector"):
        mg.resolve_operation_history(archive_ref)


def test_recovery_snapshot_derives_orphaned_world_id_from_live_scope(mg: VcsCore) -> None:
    task = mg.fork(mg.ground, "task-orphan-world-id")

    orphan = OperationRefInfo(
        handle_id="orphan-op",
        kind="marker.runtime",
        ref="refs/vcscore/ops/orphan-op",
        scope_ref=task.ref,
        scope_instance_id=task.instance_id,
        parent_op_ref=None,
        base_oid=task.creation_oid,
        operation_id="op_orphan",
        operation_label="orphan-op",
        world_id=None,
    )

    mg._orphaned_operations = [orphan]

    def fail_legacy_discovery() -> None:
        pytest.fail("legacy orphaned-operation discovery was used")

    mg._orphaned_operation_summaries = fail_legacy_discovery  # type: ignore[method-assign]

    snapshot = mg.recovery_snapshot()
    inventory = mg.recovery_inventory()

    assert len(snapshot.orphaned_operations) == 1
    assert snapshot.orphaned_operations[0].world_id == task.world_id
    assert recovery_orphaned_operation_ids(inventory) == ("op_orphan",)
    assert any(item.kind == "orphaned_operation_ref" for item in inventory.items)
    assert mg.list_orphaned_operations() == snapshot.orphaned_operations


def test_recovery_snapshot_projects_malformed_workspace_authority_inventory(mg: VcsCore) -> None:
    pending_root = Path(mg._repo_path) / "workspace-authority" / "pending"
    pending_root.mkdir(parents=True)
    (pending_root / "broken.json").write_text("not json")

    snapshot = mg.recovery_snapshot()

    assert snapshot.workspace_authority_pending == ("broken.json (present_corrupt)",)
    assert mg.list_workspace_authority_pending() == snapshot.workspace_authority_pending


def test_recovery_snapshot_marks_orphaned_world_id_unknown_when_scope_is_unavailable(mg: VcsCore) -> None:
    orphan = OperationRefInfo(
        handle_id="orphan-op",
        kind="marker.runtime",
        ref="refs/vcscore/ops/orphan-op",
        scope_ref="refs/vcscore/scopes/missing",
        scope_instance_id="scope-missing",
        parent_op_ref=None,
        base_oid="deadbeef",
        operation_id="op_orphan",
        operation_label="orphan-op",
        world_id=None,
    )

    mg._orphaned_operations = [replace(orphan)]

    snapshot = mg.recovery_snapshot()

    assert len(snapshot.orphaned_operations) == 1
    assert snapshot.orphaned_operations[0].world_id == "unknown"


def test_recovery_snapshot_keeps_archived_failures_when_clean_discards_exceed_cap(mg: VcsCore) -> None:
    failed = mg.fork(mg.ground, "task-archived-recovery-crowding")
    with (
        pytest.raises(RuntimeError, match="boom"),
        mg.runtime_activity(
            scope=failed,
            operation_id="failed-archived-op-id",
            operation_label="failed-archived-op",
            operation_kind="marker.runtime",
            failure_policy="abort_archive",
        ),
    ):
        raise RuntimeError("boom")
    mg.discard(failed)

    for idx in range(60):
        task = mg.fork(mg.ground, f"task-clean-discard-crowding-{idx}")
        with mg.runtime_activity(
            scope=task,
            operation_id=f"clean-discard-crowding-op-{idx}",
            operation_label=f"clean-discard-crowding-op-{idx}",
            operation_kind="marker.runtime",
        ):
            pass
        mg.discard(task)

    snapshot = mg.recovery_snapshot(archived_max_count=20)

    assert len(snapshot.archived_recovery_operations) == 1
    assert snapshot.archived_recovery_operations[0].operation_id == "failed-archived-op-id"
    assert snapshot.archived_recovery_operations[0].archived_via == "operation_ref"
