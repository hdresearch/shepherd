from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from commons_vcs import Repo
from commons_vcs.backends.git import GitBackend
from vcs_core._identity import read_ground_world_id
from vcs_core.commons_recording import (
    CommonsShadowConflictError,
    CommonsShadowRecorder,
    CommonsShadowRecoveryError,
    CommonsShadowUnsupportedError,
)
from vcs_core.profiles.committed_view import committed_native_effect_citers
from vcs_core.profiles.commons_vcs import profile as vcscore_profile
from vcs_core.profiles.projection import project_effect_object, project_scope_object
from vcs_core.recording import RecordingPipeline
from vcs_core.store import Store
from vcs_core.types import EffectRecord, ScopeInfo

if TYPE_CHECKING:
    from vcs_core.commons_recording import _PendingProjection, _ProjectionPlan


def _store(tmp_path: Path) -> Store:
    store = Store(str(tmp_path / ".vcscore"))
    store.create_root_commit()
    return store


def _ground_scope(store: Store, *, instance_id: str = "ground-test") -> ScopeInfo:
    return ScopeInfo(
        name="ground",
        ref=Store.GROUND_REF,
        instance_id=instance_id,
        creation_oid="",
        world_id=read_ground_world_id(store.repo_path),
    )


@dataclass(frozen=True)
class _PendingFixture:
    plan: _ProjectionPlan
    pending: _PendingProjection
    pending_json: str
    pending_ref: str


def _pending_fixture(recorder: CommonsShadowRecorder, scope: ScopeInfo, carrier_oid: str) -> _PendingFixture:
    carrier_commit = recorder._carrier_commit(carrier_oid)
    scope_object = project_scope_object(scope)
    plan = recorder._build_projection_plan(scope, scope_object, carrier_commit, expected_head=None)
    pending = recorder._pending_from_plan(plan)
    return _PendingFixture(
        plan=plan,
        pending=pending,
        pending_json=recorder._encode_pending(pending),
        pending_ref=CommonsShadowRecorder.pending_projection_ref(plan.scope_id, carrier_oid),
    )


def test_shadow_recorder_projects_one_store_commit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-record")
    oid = store._emit_effect(
        scope,
        "Marker",
        {"note": "manual"},
        workspace_changes=[("note.txt", b"hello\n")],
        substrate="marker",
    )
    recorder = CommonsShadowRecorder(store)

    result = recorder.record_carrier_commit(scope, oid)

    assert result.carrier_oid == oid
    assert result.previous_head is None
    assert result.new_head == result.commit_id
    assert recorder.backend.get_ref(result.shadow_head_ref) == result.commit_id
    assert recorder.backend.get_ref(result.carrier_commit_ref) == result.commit_id
    commit = recorder.repo.get(result.commit_id)
    assert commit is not None
    assert commit.schema_ref == "vcscore/commit/v1"
    verify = recorder.repo.verify(result.commit_id, result.commit_id, validate_trust_root=True)
    assert verify.outcome == "ok.verified"


def test_shadow_recorder_projects_ground_init_commit(tmp_path: Path) -> None:
    store = Store(str(tmp_path / ".vcscore"))
    root_oid = store.create_root_commit()
    ground = _ground_scope(store)
    recorder = CommonsShadowRecorder(store)

    result = recorder.record_carrier_commit(ground, root_oid)

    assert result.carrier_oid == root_oid
    assert result.previous_head is None
    assert result.new_head == result.commit_id
    assert recorder.backend.get_ref(result.shadow_head_ref) == result.commit_id
    assert recorder.backend.get_ref(result.carrier_commit_ref) == result.commit_id
    commit = recorder.repo.get(result.commit_id)
    assert commit is not None
    assert [edge.target for edge in commit.edges if edge.role == "parent"] == []


def test_shadow_recorder_links_first_ground_effect_to_projected_init_commit(tmp_path: Path) -> None:
    store = Store(str(tmp_path / ".vcscore"))
    root_oid = store.create_root_commit()
    ground = _ground_scope(store)
    first_oid = store._emit_effect(ground, "FirstGround", {"seq": 1}, substrate="marker")
    recorder = CommonsShadowRecorder(store)

    root = recorder.record_carrier_commit(ground, root_oid)
    first = recorder.record_carrier_commit(ground, first_oid)

    assert first.previous_head == root.commit_id
    first_commit = recorder.repo.get(first.commit_id)
    assert first_commit is not None
    assert [edge.target for edge in first_commit.edges if edge.role == "parent"] == [root.commit_id]


def test_shadow_recorder_advances_scope_head_with_parent_edge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-chain")
    first_oid = store._emit_effect(scope, "First", {"seq": 1}, substrate="marker")
    second_oid = store._emit_effect(scope, "Second", {"seq": 2}, substrate="marker")
    recorder = CommonsShadowRecorder(store)

    first = recorder.record_carrier_commit(scope, first_oid)
    second = recorder.record_carrier_commit(scope, second_oid)

    assert second.previous_head == first.commit_id
    assert recorder.backend.get_ref(second.shadow_head_ref) == second.commit_id
    second_commit = recorder.repo.get(second.commit_id)
    assert second_commit is not None
    assert any(edge.role == "parent" and edge.target == first.commit_id for edge in second_commit.edges)
    assert committed_native_effect_citers(
        recorder.repo,
        first.effect_id,
        heads=[second.commit_id],
    ) == [first.commit_id]


def test_shadow_recorder_rejects_stale_expected_head(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-conflict")
    first_oid = store._emit_effect(scope, "First", {"seq": 1}, substrate="marker")
    second_oid = store._emit_effect(scope, "Second", {"seq": 2}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    recorder.record_carrier_commit(scope, first_oid)

    with pytest.raises(CommonsShadowConflictError, match="expected None"):
        recorder.record_carrier_commit(scope, second_oid, expected_head=None)


def test_shadow_recorder_rejects_out_of_order_carrier_projection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-out-of-order")
    first_oid = store._emit_effect(scope, "First", {"seq": 1}, substrate="marker")
    second_oid = store._emit_effect(scope, "Second", {"seq": 2}, substrate="marker")
    recorder = CommonsShadowRecorder(store)

    with pytest.raises(CommonsShadowConflictError, match=r"first parent .* is not projected"):
        recorder.record_carrier_commit(scope, second_oid)

    scope_id = project_scope_object(scope).id
    assert recorder.backend.get_ref(CommonsShadowRecorder.shadow_head_ref(scope_id)) is None
    assert recorder.backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(first_oid)) is None
    assert recorder.backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(second_oid)) is None


def test_shadow_recorder_rerecords_older_reachable_carrier_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-rerecord")
    first_oid = store._emit_effect(scope, "First", {"seq": 1}, substrate="marker")
    second_oid = store._emit_effect(scope, "Second", {"seq": 2}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    first = recorder.record_carrier_commit(scope, first_oid)
    second = recorder.record_carrier_commit(scope, second_oid)

    rerecord = recorder.record_carrier_commit(scope, first_oid)

    assert rerecord.commit_id == first.commit_id
    assert rerecord.effect_id == first.effect_id
    assert rerecord.previous_head == second.commit_id
    assert rerecord.new_head == second.commit_id
    assert recorder.backend.get_ref(second.shadow_head_ref) == second.commit_id


def test_shadow_recorder_rejects_existing_mapping_to_wrong_reachable_commit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-wrong-mapping")
    first_oid = store._emit_effect(scope, "First", {"seq": 1}, substrate="marker")
    second_oid = store._emit_effect(scope, "Second", {"seq": 2}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    recorder.record_carrier_commit(scope, first_oid)
    second = recorder.record_carrier_commit(scope, second_oid)
    recorder.backend.set_ref(CommonsShadowRecorder.carrier_commit_ref(first_oid), second.commit_id)

    with pytest.raises(CommonsShadowConflictError, match="Store truth recomputes"):
        recorder.record_carrier_commit(scope, first_oid)


def test_shadow_recorder_recovers_after_head_cas_failure_without_poisoned_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-recover-head")
    oid = store._emit_effect(scope, "Marker", {"note": "recover"}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    scope_id = project_scope_object(scope).id
    head_ref = CommonsShadowRecorder.shadow_head_ref(scope_id)
    original_cas = recorder.backend.compare_and_swap_ref
    failed = False

    def fail_head_once(name: str, expected: str | None, new: str) -> bool:
        nonlocal failed
        if name == head_ref and not failed:
            failed = True
            return False
        return original_cas(name, expected, new)

    monkeypatch.setattr(recorder.backend, "compare_and_swap_ref", fail_head_once)
    with pytest.raises(CommonsShadowConflictError, match="shadow head"):
        recorder.record_carrier_commit(scope, oid)

    pending_refs = list(recorder.backend.list_refs(CommonsShadowRecorder.pending_projection_prefix(scope_id)))
    assert len(pending_refs) == 1
    assert recorder.backend.get_ref(head_ref) is None
    assert recorder.backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oid)) is None

    monkeypatch.setattr(recorder.backend, "compare_and_swap_ref", original_cas)
    result = recorder.record_carrier_commit(scope, oid)

    assert recorder.backend.get_ref(head_ref) == result.commit_id
    assert recorder.backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oid)) == result.commit_id
    assert list(recorder.backend.list_refs(CommonsShadowRecorder.pending_projection_prefix(scope_id))) == []


def test_shadow_recorder_recovers_after_head_advances_before_mapping(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-recover-mapping")
    oid = store._emit_effect(scope, "Marker", {"note": "recover"}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    fixture = _pending_fixture(recorder, scope, oid)
    assert recorder.backend.compare_and_swap_ref(fixture.pending_ref, None, fixture.pending_json)
    recorder._publish_plan_objects(fixture.plan)
    assert recorder.backend.compare_and_swap_ref(
        CommonsShadowRecorder.shadow_head_ref(fixture.plan.scope_id),
        None,
        fixture.plan.commit_id,
    )

    result = recorder.record_carrier_commit(scope, oid)

    assert result.commit_id == fixture.plan.commit_id
    assert recorder.backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oid)) == fixture.plan.commit_id
    assert recorder.backend.get_ref(fixture.pending_ref) is None


def test_shadow_recorder_recovers_after_mapping_published_before_pending_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-recover-pending-delete")
    oid = store._emit_effect(scope, "Marker", {"note": "recover"}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    fixture = _pending_fixture(recorder, scope, oid)
    assert recorder.backend.compare_and_swap_ref(fixture.pending_ref, None, fixture.pending_json)
    recorder._publish_plan_objects(fixture.plan)
    assert recorder.backend.compare_and_swap_ref(
        CommonsShadowRecorder.shadow_head_ref(fixture.plan.scope_id),
        None,
        fixture.plan.commit_id,
    )
    assert recorder.backend.compare_and_swap_ref(
        CommonsShadowRecorder.carrier_commit_ref(oid),
        None,
        fixture.plan.commit_id,
    )

    result = recorder.record_carrier_commit(scope, oid)

    assert result.commit_id == fixture.plan.commit_id
    assert result.new_head == fixture.plan.commit_id
    assert recorder.backend.get_ref(fixture.pending_ref) is None


def test_shadow_recorder_guarded_pending_delete_refuses_changed_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-guarded-delete")
    oid = store._emit_effect(scope, "Marker", {"note": "delete"}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    fixture = _pending_fixture(recorder, scope, oid)
    changed_json = recorder._encode_pending(
        type(fixture.pending)(
            scope_id=fixture.pending.scope_id,
            carrier_oid=fixture.pending.carrier_oid,
            expected_head=fixture.pending.expected_head,
            effect_id=fixture.pending.effect_id,
            commit_id=fixture.pending.commit_id,
            workspace_tree=fixture.pending.workspace_tree,
            parent_carrier_oid=fixture.pending.parent_carrier_oid,
            parent_commit_id="sha256:" + "0" * 64,
        )
    )
    recorder.backend.set_ref(fixture.pending_ref, changed_json)

    # This helper is the compare-and-delete guard. Other tests exercise the
    # recovery callers; this one isolates the changed-record protection.
    with pytest.raises(CommonsShadowRecoveryError, match="pending projection changed"):
        recorder._delete_pending_if_unchanged(fixture.pending_ref, fixture.pending_json)

    assert recorder.backend.get_ref(fixture.pending_ref) == changed_json


def test_shadow_recorder_fails_closed_on_noncanonical_pending_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-noncanonical-pending")
    oid = store._emit_effect(scope, "Marker", {"note": "json"}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    fixture = _pending_fixture(recorder, scope, oid)
    raw = json.dumps(
        {
            "version": 1,
            "scope_id": fixture.pending.scope_id,
            "carrier_oid": fixture.pending.carrier_oid,
            "expected_head": fixture.pending.expected_head,
            "effect_id": fixture.pending.effect_id,
            "commit_id": fixture.pending.commit_id,
            "workspace_tree": fixture.pending.workspace_tree,
            "parent_carrier_oid": fixture.pending.parent_carrier_oid,
            "parent_commit_id": fixture.pending.parent_commit_id,
        }
    )
    assert raw != fixture.pending_json
    recorder.backend.set_ref(fixture.pending_ref, raw)

    with pytest.raises(CommonsShadowRecoveryError, match="JSON is not canonical"):
        recorder.record_carrier_commit(scope, oid)

    assert recorder.backend.get_ref(fixture.pending_ref) == raw


def test_shadow_recorder_holds_scope_lock_while_publishing_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-lock")
    oid = store._emit_effect(scope, "Marker", {"note": "lock"}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    original_lock = recorder.backend.scope_lock
    original_cas = recorder.backend.compare_and_swap_ref
    locked = False
    observed: list[bool] = []

    @contextmanager
    def tracking_lock(scope_id: str, *, timeout: float = 30.0) -> Iterator[None]:
        nonlocal locked
        with original_lock(scope_id, timeout=timeout):
            locked = True
            try:
                yield
            finally:
                locked = False

    def tracking_cas(name: str, expected: str | None, new: str) -> bool:
        if name.startswith("vcscore/"):
            observed.append(locked)
        return original_cas(name, expected, new)

    monkeypatch.setattr(recorder.backend, "scope_lock", tracking_lock)
    monkeypatch.setattr(recorder.backend, "compare_and_swap_ref", tracking_cas)

    recorder.record_carrier_commit(scope, oid)

    assert observed
    assert all(observed)


def test_shadow_recorder_fails_closed_on_multiple_pending_refs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-multiple-pending")
    first_oid = store._emit_effect(scope, "First", {"seq": 1}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    scope_id = project_scope_object(scope).id
    recorder.backend.set_ref(CommonsShadowRecorder.pending_projection_ref(scope_id, first_oid), "{}")
    recorder.backend.set_ref(CommonsShadowRecorder.pending_projection_ref(scope_id, "f" * 40), "{}")

    with pytest.raises(CommonsShadowRecoveryError, match="multiple pending"):
        recorder.record_carrier_commit(scope, first_oid)


def test_shadow_recorder_fails_closed_on_pending_parent_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-pending-mismatch")
    oid = store._emit_effect(scope, "Marker", {"note": "mismatch"}, substrate="marker")
    recorder = CommonsShadowRecorder(store)
    carrier_commit = recorder._carrier_commit(oid)
    scope_object = project_scope_object(scope)
    plan = recorder._build_projection_plan(scope, scope_object, carrier_commit, expected_head=None)
    pending = recorder._pending_from_plan(plan)
    pending_json = recorder._encode_pending(
        type(pending)(
            scope_id=pending.scope_id,
            carrier_oid=pending.carrier_oid,
            expected_head="sha256:" + "0" * 64,
            effect_id=pending.effect_id,
            commit_id=pending.commit_id,
            workspace_tree=pending.workspace_tree,
            parent_carrier_oid=pending.parent_carrier_oid,
            parent_commit_id=pending.parent_commit_id,
        )
    )
    pending_ref = CommonsShadowRecorder.pending_projection_ref(plan.scope_id, oid)
    recorder.backend.set_ref(pending_ref, pending_json)

    with pytest.raises(CommonsShadowRecoveryError, match="expected head"):
        recorder.record_carrier_commit(scope, oid)

    assert recorder.backend.get_ref(pending_ref) == pending_json


def test_shadow_recorder_pins_workspace_tree_across_gc_and_reopen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-shadow-pins")
    oid = store._emit_effect(
        scope,
        "Patch",
        {"note": "pin"},
        workspace_changes=[("bin/run", b"#!/bin/sh\necho hi\n", 0o100755)],
        substrate="filesystem",
    )
    recorder = CommonsShadowRecorder(store)
    result = recorder.record_carrier_commit(scope, oid)
    commit = recorder.repo.get(result.commit_id)
    assert commit is not None
    tree_oid = str(commit.body["workspace_tree"])

    git_dir = Path(store._repo.path).resolve()
    gc = subprocess.run(
        ["git", "gc", "--prune=now", "--aggressive"],
        cwd=str(git_dir),
        capture_output=True,
        check=False,
        text=True,
    )
    assert gc.returncode == 0, gc.stderr

    fresh_backend = GitBackend.open(git_dir)
    fresh_repo = Repo(profiles=[vcscore_profile], backend=fresh_backend)
    assert fresh_repo.get(result.commit_id) is not None
    assert tree_oid in fresh_backend._repo


def test_recording_pipeline_leaves_shadow_disabled_by_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-pipeline-disabled")
    pipeline = RecordingPipeline(store)
    pipeline.set_scope(scope)

    oids = pipeline.record(
        [EffectRecord("Marker", {"note": "disabled"})],
        substrate="marker",
    )

    assert len(oids) == 1
    backend = GitBackend.open(Path(store._repo.path).resolve())
    assert list(backend.list_refs("vcscore/")) == []


def test_recording_pipeline_records_shadow_when_enabled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-pipeline-shadow")
    pipeline = RecordingPipeline(store, commons_shadow=True)
    pipeline.set_scope(scope)

    oids = pipeline.record(
        [
            EffectRecord("First", {"seq": 1}, workspace_changes=(("one.txt", b"one"),)),
            EffectRecord("Second", {"seq": 2}, workspace_changes=(("two.txt", b"two"),)),
        ],
        substrate="marker",
    )

    backend = GitBackend.open(Path(store._repo.path).resolve())
    repo = Repo(profiles=[vcscore_profile], backend=backend)
    scope_id = project_scope_object(scope).id
    shadow_head = backend.get_ref(CommonsShadowRecorder.shadow_head_ref(scope_id))
    assert shadow_head is not None
    first_commit = backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oids[0]))
    second_commit = backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oids[1]))
    assert first_commit is not None
    assert second_commit == shadow_head
    second_obj = repo.get(second_commit)
    assert second_obj is not None
    assert any(edge.role == "parent" and edge.target == first_commit for edge in second_obj.edges)


def test_recording_pipeline_projects_first_ground_effect_without_projecting_init_commit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ground = ScopeInfo(
        name="ground",
        ref=Store.GROUND_REF,
        instance_id="ground-test",
        creation_oid="",
        world_id="world-ground",
    )
    pipeline = RecordingPipeline(store, commons_shadow=True)
    pipeline.set_scope(ground)

    first_oid = pipeline.record_one(EffectRecord("FirstGround", {"seq": 1}), substrate="marker")
    second_oid = pipeline.record_one(EffectRecord("SecondGround", {"seq": 2}), substrate="marker")

    backend = GitBackend.open(Path(store._repo.path).resolve())
    repo = Repo(profiles=[vcscore_profile], backend=backend)
    scope_id = project_scope_object(ground).id
    shadow_head = backend.get_ref(CommonsShadowRecorder.shadow_head_ref(scope_id))
    first_commit_id = backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(first_oid))
    second_commit_id = backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(second_oid))
    assert first_commit_id is not None
    assert second_commit_id == shadow_head
    first_commit = repo.get(first_commit_id)
    second_commit = repo.get(second_commit_id)
    assert first_commit is not None
    assert second_commit is not None
    assert [edge.target for edge in first_commit.edges if edge.role == "parent"] == []
    assert [edge.target for edge in second_commit.edges if edge.role == "parent"] == [first_commit_id]


def test_recording_pipeline_shadow_can_be_enabled_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VCS_CORE_COMMONS_SHADOW", "1")
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-pipeline-env")
    pipeline = RecordingPipeline(store)
    pipeline.set_scope(scope)

    oid = pipeline.record_one(EffectRecord("Marker", {"note": "env"}), substrate="marker")

    backend = GitBackend.open(Path(store._repo.path).resolve())
    assert backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oid)) is not None


def test_recording_pipeline_fails_closed_on_shadow_recovery_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-pipeline-shadow-sidecar-failure")
    pipeline = RecordingPipeline(store, commons_shadow=True)
    pipeline.set_scope(scope)
    backend = GitBackend.open(Path(store._repo.path).resolve())
    scope_id = project_scope_object(scope).id
    backend.set_ref(CommonsShadowRecorder.pending_projection_ref(scope_id, "f" * 40), "{}")

    with pytest.raises(CommonsShadowRecoveryError, match="unsupported version"):
        pipeline.record_one(EffectRecord("Marker", {"note": "sidecar-failure"}), substrate="marker")

    oid = store.log(ref=scope.ref, max_count=1)[0].oid
    assert store.log(ref=scope.ref, max_count=1)[0].oid == oid
    assert backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oid)) is None
    diagnostics = pipeline.commons_shadow_diagnostics
    assert len(diagnostics) == 1
    assert diagnostics[0].carrier_oid == oid
    assert diagnostics[0].scope_ref == scope.ref
    assert diagnostics[0].error_type == "CommonsShadowRecoveryError"
    assert "unsupported version" in diagnostics[0].message


def test_recording_pipeline_fails_closed_on_projection_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-pipeline-shadow-projection-exception")
    pipeline = RecordingPipeline(store, commons_shadow=True)
    pipeline.set_scope(scope)

    def fail_projection(
        _self: CommonsShadowRecorder,
        _scope: ScopeInfo,
        _carrier_oid: str,
        *,
        expected_head: str | None | object = None,
    ) -> object:
        _ = expected_head
        raise ValueError("synthetic profile projection failure")

    monkeypatch.setattr(CommonsShadowRecorder, "record_carrier_commit", fail_projection)

    with pytest.raises(ValueError, match="synthetic profile projection failure"):
        pipeline.record_one(EffectRecord("Marker", {"note": "projection-failure"}), substrate="marker")

    oid = store.log(ref=scope.ref, max_count=1)[0].oid
    assert store.log(ref=scope.ref, max_count=1)[0].oid == oid
    backend = GitBackend.open(Path(store._repo.path).resolve())
    assert backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(oid)) is None
    diagnostics = pipeline.commons_shadow_diagnostics
    assert len(diagnostics) == 1
    assert diagnostics[0].carrier_oid == oid
    assert diagnostics[0].scope_ref == scope.ref
    assert diagnostics[0].error_type == "ValueError"
    assert diagnostics[0].message == "synthetic profile projection failure"


def test_recording_pipeline_shadow_chain_matches_store_first_parent_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-pipeline-compare")
    pipeline = RecordingPipeline(store, commons_shadow=True)
    pipeline.set_scope(scope)

    carrier_oids = pipeline.record(
        [
            EffectRecord("First", {"seq": 1}, workspace_changes=(("one.txt", b"one"),)),
            EffectRecord("Second", {"seq": 2}, workspace_changes=(("two.txt", b"two"),)),
            EffectRecord("Third", {"seq": 3}, workspace_changes=(("three.txt", b"three"),)),
        ],
        substrate="marker",
    )

    store_chain: list[str] = []
    cursor = carrier_oids[-1]
    while cursor != scope.creation_oid:
        store_chain.append(cursor)
        commit = store._repo[cursor]
        assert len(commit.parent_ids) == 1
        cursor = str(commit.parent_ids[0])
    store_chain.reverse()
    assert store_chain == carrier_oids

    backend = GitBackend.open(Path(store._repo.path).resolve())
    repo = Repo(profiles=[vcscore_profile], backend=backend)
    shadow_head = backend.get_ref(CommonsShadowRecorder.shadow_head_ref(project_scope_object(scope).id))
    assert shadow_head is not None
    commons_chain: list[str] = []
    cursor = shadow_head
    while cursor is not None:
        commons_chain.append(cursor)
        commit = repo.get(cursor)
        assert commit is not None
        parents = [edge.target for edge in commit.edges if edge.role == "parent"]
        cursor = parents[0] if parents else None
    commons_chain.reverse()

    projected_chain = [
        backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(carrier_oid)) for carrier_oid in store_chain
    ]
    assert projected_chain == commons_chain


def test_recording_pipeline_shadow_refuses_operation_spans_before_store_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-pipeline-operation")
    pipeline = RecordingPipeline(store, commons_shadow=True)
    pipeline.set_scope(scope)

    with pytest.raises(CommonsShadowUnsupportedError, match="operation spans"):
        pipeline.begin_operation(handle_id="op-shadow", kind="test.operation")

    backend = GitBackend.open(Path(store._repo.path).resolve())
    scope_id = project_scope_object(scope).id
    assert store.operation_ref("op-shadow") not in store._repo.references
    assert backend.get_ref(CommonsShadowRecorder.shadow_head_ref(scope_id)) is None


def test_store_begin_operation_is_unchanged_when_pipeline_shadow_would_refuse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scope = store.fork(Store.GROUND_REF, "task-store-operation")

    op = store.begin_operation(
        scope.ref,
        handle_id="op-store",
        kind="test.operation",
        world_id=str(scope.world_id),
        scope_instance_id=scope.instance_id,
    )

    assert op.ref in store._repo.references


def test_recording_pipeline_excludes_scope_attribution_from_effect_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_scope = store.fork(Store.GROUND_REF, "task-effect-identity-a")
    second_scope = store.fork(Store.GROUND_REF, "task-effect-identity-b")
    first_pipeline = RecordingPipeline(store, commons_shadow=True)
    first_pipeline.set_scope(first_scope)
    second_pipeline = RecordingPipeline(store, commons_shadow=True)
    second_pipeline.set_scope(second_scope)

    first_oid = first_pipeline.record_one(EffectRecord("Marker", {"semantic": "same"}), substrate="marker")
    second_oid = second_pipeline.record_one(EffectRecord("Marker", {"semantic": "same"}), substrate="marker")

    first_effect = project_effect_object(store._repo, store._repo[first_oid])
    second_effect = project_effect_object(store._repo, store._repo[second_oid])
    assert first_effect.id == second_effect.id
    backend = GitBackend.open(Path(store._repo.path).resolve())
    repo = Repo(profiles=[vcscore_profile], backend=backend)
    first_commit_id = backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(first_oid))
    second_commit_id = backend.get_ref(CommonsShadowRecorder.carrier_commit_ref(second_oid))
    assert first_commit_id is not None
    assert second_commit_id is not None
    assert first_commit_id != second_commit_id
    first_commit = repo.get(first_commit_id)
    second_commit = repo.get(second_commit_id)
    assert first_commit is not None
    assert second_commit is not None
    first_scope = [edge.target for edge in first_commit.edges if edge.role == "scope"]
    second_scope = [edge.target for edge in second_commit.edges if edge.role == "scope"]
    assert first_scope != second_scope
