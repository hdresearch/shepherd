"""GitBackend inverse-index concurrency tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from commons_vcs._types import Edge, Object
from commons_vcs.backends.git import GitBackend


def _target() -> Object:
    return Object(schema_ref="test/v1", body={"v": "target"}, edges=())


def _citer(target_id: str, value: str) -> Object:
    return Object(
        schema_ref="test/v1",
        body={"v": value},
        edges=(Edge(role="r", target=target_id),),
    )


def test_index_add_is_idempotent(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    target = _target()
    backend.write_object(target)

    citer = _citer(target.id, "c1")
    backend.write_object(citer)
    backend.write_object(citer)

    refs = [ref_name for ref_name in backend._repo.references if ref_name.startswith("refs/commons-vcs/index/")]
    assert len(refs) == 1
    assert backend.cited_by(target.id, "r") == [citer.id]


def test_index_roles_are_ref_escaped(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    target = _target()
    backend.write_object(target)

    role = "role/with spaces:and-symbols"
    citer = Object(
        schema_ref="test/v1",
        body={"v": "escaped"},
        edges=(Edge(role=role, target=target.id),),
    )
    backend.write_object(citer)

    assert backend.cited_by(target.id, role) == [citer.id]


def test_cited_by_survives_packed_refs(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    backend = GitBackend.init(repo_path)
    target = _target()
    backend.write_object(target)
    citers = [_citer(target.id, f"c{i}") for i in range(3)]
    for citer in citers:
        backend.write_object(citer)

    result = subprocess.run(
        ["git", "pack-refs", "--all"],
        cwd=str(repo_path),
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    fresh = GitBackend.open(repo_path)
    assert fresh.cited_by(target.id, "r") == sorted(citer.id for citer in citers)


def test_cited_by_cache_observes_external_writer(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    reader = GitBackend.init(repo_path)
    target = _target()
    reader.write_object(target)
    first = _citer(target.id, "first")
    reader.write_object(first)
    assert reader.cited_by(target.id, "r") == [first.id]

    writer = GitBackend.open(repo_path)
    second = _citer(target.id, "second")
    writer.write_object(second)

    assert reader.cited_by(target.id, "r") == sorted([first.id, second.id])


def test_concurrent_processes_do_not_lose_index_updates(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    backend = GitBackend.init(repo_path)
    target = _target()
    backend.write_object(target)

    worker = """
import sys
from commons_vcs._types import Edge, Object
from commons_vcs.backends.git import GitBackend

repo_path, target_id, value = sys.argv[1:4]
backend = GitBackend.open(repo_path)
obj = Object(
    schema_ref="test/v1",
    body={"v": value},
    edges=(Edge(role="r", target=target_id),),
)
backend.write_object(obj)
"""

    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(repo_path), target.id, f"c{i}"],
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        for i in range(24)
    ]
    failures = [p.wait(timeout=10) for p in processes]
    assert failures == [0] * len(processes)

    expected = sorted(_citer(target.id, f"c{i}").id for i in range(24))
    fresh = GitBackend.open(repo_path)
    assert fresh.cited_by(target.id, "r") == expected
