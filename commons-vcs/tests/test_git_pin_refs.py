"""GitBackend-specific test: explicit pin refs survive `git gc`.

The danger this test guards against: Git's reachability traversal follows
commit->tree->blob and tag->commit, but does not parse commons-vcs object
blobs looking for embedded Git OIDs. Without an explicit pin ref,
`git gc --prune=now` would prune a tree that is only mentioned in an
Object body.

GitBackend exposes caller-managed pin refs under refs/commons-vcs/pins/.
Domain coordinators decide what to pin; the backend only keeps the selected
Git object reachable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pygit2
import pytest
from commons_vcs._types import Object
from commons_vcs.backends.git import GitBackend, RefInstallError


def _make_workspace_tree(repo: pygit2.Repository, files: dict[str, bytes]) -> str:
    """Stage `files` into a fresh Git tree and return its OID hex."""
    builder = repo.TreeBuilder()
    for path, content in files.items():
        blob_oid = repo.create_blob(content)
        builder.insert(path, blob_oid, pygit2.GIT_FILEMODE_BLOB)
    return str(builder.write())


def test_workspace_tree_survives_git_gc(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    repo = backend._repo

    # Stage real workspace content into a Git tree.
    tree_oid = _make_workspace_tree(repo, {"hello.txt": b"hello\n"})
    blob_oid_for_hello = str(repo[tree_oid]["hello.txt"].id)

    # Append an Object that references the tree OID in its body.
    obj = Object(
        schema_ref="vcscore/commit/v1",
        body={"workspace_tree": tree_oid},
        edges=(),
    )
    digest = backend.write_object(obj)
    backend.pin_git_object(f"workspace-trees/{digest.replace(':', '/')}", tree_oid)

    # Sanity: pin ref exists and points at the tree.
    pin_refs = [r for r in repo.references if r.startswith("refs/commons-vcs/pins/workspace-trees/")]
    assert len(pin_refs) == 1
    pinned_oid = str(repo.references[pin_refs[0]].target)
    assert pinned_oid == tree_oid

    # The ground truth: invoke `git gc --prune=now` and verify the tree
    # and its inner blob both survive. The commons-vcs blob (canonical
    # bytes of the Object) is also pinned via refs/commons-vcs/objects/...
    # and survives independently.
    result = subprocess.run(
        ["git", "gc", "--prune=now", "--aggressive"],
        cwd=str(Path(repo.path).resolve()),
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, f"git gc failed: {result.stderr}"

    # Re-open the repo so pygit2 picks up packed objects.
    fresh = pygit2.Repository(str(Path(repo.path).resolve()))

    # Tree must still be present.
    assert tree_oid in fresh, "workspace tree was pruned despite pin ref"
    # Inner blob must still be present (Git reachability follows tree -> blob).
    assert blob_oid_for_hello in fresh, "inner blob was pruned"
    # Object must still be readable through the backend.
    fresh_backend = GitBackend.open(Path(repo.path).resolve())
    fetched = fresh_backend.read_object(digest)
    assert fetched is not None
    assert fetched.body["workspace_tree"] == tree_oid


def test_write_object_does_not_infer_pins_from_body_fields(tmp_path: Path) -> None:
    """Pinning is explicit; body field names are domain/profile-owned."""
    backend = GitBackend.init(tmp_path / "repo")
    obj = Object(
        schema_ref="vcscore/commit/v1",
        body={"workspace_tree": "0" * 40},
        edges=(),
    )
    backend.write_object(obj)
    pin_refs = [r for r in backend._repo.references if r.startswith("refs/commons-vcs/pins/")]
    assert pin_refs == []


def test_read_object_rejects_ref_digest_mismatch(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    first = Object(schema_ref="test/v1", body={"v": "first"})
    second = Object(schema_ref="test/v1", body={"v": "second"})
    backend.write_object(first)
    second_blob_oid = backend._write_blob(second.canonical_bytes())
    backend._set_ref_to_oid(backend._object_ref_name(first.id), second_blob_oid)

    with pytest.raises(ValueError, match="integrity failure"):
        backend.read_object(first.id)


def test_immutable_ref_install_refuses_to_overwrite_unexpected_ref(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    ref_name = "refs/commons-vcs/objects/sha256/" + "a" * 64
    expected_oid = backend._write_blob(b"expected")
    unexpected_oid = backend._write_blob(b"unexpected")
    backend._set_ref_to_oid(ref_name, unexpected_oid)

    with pytest.raises(RefInstallError, match="unexpected object"):
        backend._ensure_immutable_ref(ref_name, expected_oid)

    assert backend._ref_target_oid(ref_name) == unexpected_oid


def test_pin_git_object_rejects_missing_oid(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")

    with pytest.raises(ValueError, match="missing Git object"):
        backend.pin_git_object("workspace-trees/missing", "0" * 40)


def test_pin_git_object_rejects_invalid_pin_name(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")

    tree_oid = _make_workspace_tree(backend._repo, {"hello.txt": b"hello\n"})
    with pytest.raises(ValueError, match="invalid pin name"):
        backend.pin_git_object("bad..name", tree_oid)


def test_unpin_git_object_removes_pin_ref(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    tree_oid = _make_workspace_tree(backend._repo, {"hello.txt": b"hello\n"})
    backend.pin_git_object("workspace-trees/example", tree_oid)
    backend.unpin_git_object("workspace-trees/example")

    pin_refs = [r for r in backend._repo.references if r.startswith("refs/commons-vcs/pins/")]
    assert pin_refs == []
