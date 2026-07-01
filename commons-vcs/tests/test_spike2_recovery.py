"""Spike 2 coordinator concurrency and crash-recovery tests."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from commons_vcs import Object, Repo
from commons_vcs.backends.git import GitBackend
from support import profiles
from support.spike2_coordinator import Spike2Coordinator

SCOPE_A = "sha256:" + "a" * 64
SCOPE_B = "sha256:" + "b" * 64
SCOPE_C = "sha256:" + "c" * 64
ZERO_DIGEST = "sha256:" + "0" * 64


def _repo(backend: GitBackend) -> Repo:
    return Repo(profiles=[profiles.vcscore.profile, profiles.shepherd.profile], backend=backend)


def _effect(value: str) -> Object:
    return Object(
        schema_ref="shepherd/effect/v1",
        body={
            "type": "tool_call_completed",
            "tool_call_id": f"tool-{value}",
            "tool_name": "bash",
            "params": {"command": f"echo {value}"},
            "success": True,
            "output": "",
            "output_digest": ZERO_DIGEST,
            "output_bytes_len": 0,
            "duration_ms": 1,
            "started_at_ns": 1,
            "completed_at_ns": 2,
            "task_name": "spike2",
            "provider_id": "test",
            "scope_id": "phase-minus-1",
        },
        edges=(),
    )


def _workspace_tree(i: int) -> str:
    return f"{i:040x}"


def _fresh_coordinator(repo_path: Path) -> Spike2Coordinator:
    backend = GitBackend.open(repo_path)
    return Spike2Coordinator(_repo(backend), backend)


def _wait_for_marker(marker: Path) -> None:
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists(), "worker did not reach requested cutpoint"


WORKER = """
import sys
import time
from pathlib import Path

from commons_vcs import Object, Repo
from commons_vcs.backends.git import GitBackend

sys.path.insert(0, str(Path.cwd() / "tests"))

from support import profiles
from support.spike2_coordinator import Spike2Coordinator

ZERO_DIGEST = "sha256:" + "0" * 64

def effect(value):
    return Object(
        schema_ref="shepherd/effect/v1",
        body={
            "type": "tool_call_completed",
            "tool_call_id": f"tool-{value}",
            "tool_name": "bash",
            "params": {"command": f"echo {value}"},
            "success": True,
            "output": "",
            "output_digest": ZERO_DIGEST,
            "output_bytes_len": 0,
            "duration_ms": 1,
            "started_at_ns": 1,
            "completed_at_ns": 2,
            "task_name": "spike2",
            "provider_id": "test",
            "scope_id": "phase-minus-1",
        },
        edges=(),
    )

repo_path, scope_id, value, workspace_tree, crash_at, marker = sys.argv[1:7]
backend = GitBackend.open(repo_path)
repo = Repo(profiles=[profiles.vcscore.profile, profiles.shepherd.profile], backend=backend)
coordinator = Spike2Coordinator(repo, backend)

def cutpoint(name):
    if name == crash_at:
        Path(marker).write_text(name)
        while True:
            time.sleep(1)

commit_id = coordinator.append_observed_effect(
    scope_id=scope_id,
    effect=effect(value),
    workspace_tree=workspace_tree,
    cutpoint=cutpoint,
)
print(commit_id, flush=True)
"""


def _run_worker(
    repo_path: Path,
    *,
    scope_id: str,
    value: str,
    workspace_tree: str,
    crash_at: str = "never",
    marker: Path | None = None,
) -> subprocess.Popen[str]:
    if marker is None:
        marker = repo_path.parent / f"{value}.marker"
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            WORKER,
            str(repo_path),
            scope_id,
            value,
            workspace_tree,
            crash_at,
            str(marker),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _kill_at_cutpoint(repo_path: Path, crash_at: str) -> tuple[Spike2Coordinator, str]:
    marker = repo_path.parent / f"{crash_at}.marker"
    process = _run_worker(
        repo_path,
        scope_id=SCOPE_A,
        value=crash_at,
        workspace_tree=_workspace_tree(1),
        crash_at=crash_at,
        marker=marker,
    )
    _wait_for_marker(marker)
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=5)
    coordinator = _fresh_coordinator(repo_path)
    effect_id = _effect(crash_at).id
    return coordinator, effect_id


def test_recovery_clears_pending_after_crash_before_commit(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    GitBackend.init(repo_path)
    coordinator, _effect_id = _kill_at_cutpoint(repo_path, "after_pending")

    with coordinator.backend.scope_lock(SCOPE_A):
        outcome = coordinator.recover_scope(SCOPE_A)

    assert outcome == "cleared_uncommitted_pending"
    assert coordinator.backend.get_ref(coordinator.pending_ref(SCOPE_A)) is None
    assert coordinator.backend.get_ref(coordinator.head_ref(SCOPE_A)) is None


def test_recovery_ignores_orphan_commit_after_crash_before_head_cas(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    GitBackend.init(repo_path)
    coordinator, effect_id = _kill_at_cutpoint(repo_path, "after_commit")

    assert coordinator.repo.cited_by(effect_id, "effect") != []
    with coordinator.backend.scope_lock(SCOPE_A):
        outcome = coordinator.recover_scope(SCOPE_A)

    assert outcome == "cleared_uncommitted_pending"
    assert coordinator.backend.get_ref(coordinator.pending_ref(SCOPE_A)) is None
    assert coordinator.backend.get_ref(coordinator.head_ref(SCOPE_A)) is None


def test_recovery_clears_pending_after_crash_following_head_cas(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    GitBackend.init(repo_path)
    coordinator, effect_id = _kill_at_cutpoint(repo_path, "after_head")

    head = coordinator.backend.get_ref(coordinator.head_ref(SCOPE_A))
    assert head is not None
    assert coordinator.head_chain_contains_effect(head, effect_id)

    with coordinator.backend.scope_lock(SCOPE_A):
        outcome = coordinator.recover_scope(SCOPE_A)

    assert outcome == "cleared_committed_pending"
    assert coordinator.backend.get_ref(coordinator.pending_ref(SCOPE_A)) is None
    assert coordinator.backend.get_ref(coordinator.head_ref(SCOPE_A)) == head


def test_same_scope_concurrent_appends_form_linear_head_chain(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    GitBackend.init(repo_path)
    processes = [
        _run_worker(repo_path, scope_id=SCOPE_A, value=f"same-{i}", workspace_tree=_workspace_tree(i + 1))
        for i in range(2)
    ]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        outputs.append(stdout.strip())

    coordinator = _fresh_coordinator(repo_path)
    head = coordinator.backend.get_ref(coordinator.head_ref(SCOPE_A))
    assert head in outputs

    chain = []
    cursor = head
    while cursor is not None:
        chain.append(cursor)
        obj = coordinator.repo.get(cursor)
        assert obj is not None
        parents = [e.target for e in obj.edges if e.role == "parent"]
        cursor = parents[0] if parents else None

    assert sorted(chain) == sorted(outputs)
    assert coordinator.backend.get_ref(coordinator.pending_ref(SCOPE_A)) is None


def test_cross_scope_appends_share_index_without_lost_update(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    GitBackend.init(repo_path)
    scopes = [SCOPE_A, SCOPE_B, SCOPE_C]
    processes = [
        _run_worker(repo_path, scope_id=scope, value="shared", workspace_tree=_workspace_tree(i + 10))
        for i, scope in enumerate(scopes)
    ]
    commit_ids = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        commit_ids.append(stdout.strip())

    coordinator = _fresh_coordinator(repo_path)
    effect_id = _effect("shared").id
    assert coordinator.repo.cited_by(effect_id, "effect") == sorted(commit_ids)
    for scope, commit_id in zip(scopes, commit_ids, strict=True):
        assert coordinator.backend.get_ref(coordinator.head_ref(scope)) == commit_id
