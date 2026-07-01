"""Phase -1 oracle parity, but against GitBackend.

The original test_smoke.py runs against MemoryBackend (the Repo
default). This test re-runs the oracle-graph construction against a
fresh GitBackend over a tmp_path .git/ directory, and asserts:

- All four oracle digests reproduce byte-exact (the cluster
  byte-exact-identity claim is backend-independent — this test is
  the proof for GitBackend).
- Bidirectional cited_by queries pass through the index sidecar.
- A second commit citing the same shepherd effect (Phase B's
  coordinator commit) lands and the index correctly returns two
  citers.

Treats build_oracle_graph and run_phase_b from tests.support.oracle as
the shared harness: same inputs, different backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from commons_vcs.backends.git import GitBackend
from support import oracle

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def git_backend(tmp_path: Path) -> GitBackend:
    return GitBackend.init(tmp_path / "repo")


def test_oracle_digests_reproduce_against_git_backend(git_backend: GitBackend) -> None:
    _repo, digests = oracle.build_oracle_graph(backend=git_backend)
    for name, expected in oracle.ORACLE.items():
        if name == "TOOL_STDOUT":
            continue  # not in the four-Object graph
        assert digests[name] == expected, f"{name}: expected {expected}, got {digests[name]}"


def test_cited_by_against_git_backend(git_backend: GitBackend) -> None:
    repo, digests = oracle.build_oracle_graph(backend=git_backend)
    assert repo.cited_by(digests["SHEPHERD_EFFECT"], "effect") == [digests["VCSCORE_COMMIT"]]
    assert repo.cited_by(digests["SHEPHERD_EFFECT"], "evidence") == [digests["SGC_RECEIPT"]]
    assert repo.cited_by(digests["PARENT_COMMIT"], "executed-against") == [digests["SHEPHERD_EFFECT"]]
    assert repo.cited_by(digests["PARENT_COMMIT"], "parent") == [digests["VCSCORE_COMMIT"]]


def test_phase_b_two_citers_against_git_backend(git_backend: GitBackend) -> None:
    """A second commit citing the same shepherd effect must compose.

    The schema-scoped inverse-C1 rule (refactor.md §8.1) permits multiple
    vcscore commits citing the same cross-profile effect. Phase -1
    findings.md confirmed this; this test re-confirms it on GitBackend.
    """
    repo, digests = oracle.build_oracle_graph(backend=git_backend)
    failures, phase_b_digests = oracle.run_phase_b(repo, digests)
    assert failures == [], f"Phase B failed against GitBackend: {failures}"
    citers = repo.cited_by(digests["SHEPHERD_EFFECT"], "effect")
    assert digests["VCSCORE_COMMIT"] in citers
    assert phase_b_digests["PHASE_B_COMMIT"] in citers
    assert len(citers) == 2


def test_persistence_across_reopen(tmp_path: Path) -> None:
    """Objects, refs, and the index survive closing and reopening the repo.

    The acid test for "this is a real persistent backend, not a fancy
    in-memory dict."
    """
    repo_path = tmp_path / "repo"
    backend = GitBackend.init(repo_path)
    repo, digests = oracle.build_oracle_graph(backend=backend)
    vcscore_id = digests["VCSCORE_COMMIT"]
    effect_id = digests["SHEPHERD_EFFECT"]

    # Drop the in-memory backend handle; reopen from disk.
    del backend
    del repo
    fresh = GitBackend.open(repo_path)
    obj = fresh.read_object(vcscore_id)
    assert obj is not None
    assert obj.schema_ref == "vcscore/commit/v1"
    citers = fresh.cited_by(effect_id, "effect")
    assert citers == [vcscore_id]
