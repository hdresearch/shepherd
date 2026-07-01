"""GitBackend-specific recovery tests.

The contract being tested: an interrupted multi-step write must leave
the named-ref state unchanged, and the orphan blob must be reclaimable
by `git gc`. set_ref is two-step (write value blob, then point ref at
it); a crash between the steps leaves an orphan blob in .git/objects/.
The ref's value must remain whatever it was before the interrupted call,
and the orphan blob must not be reachable from any ref.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pygit2
from commons_vcs.backends.git import GitBackend


def test_set_ref_orphan_blob_does_not_corrupt_get(tmp_path: Path) -> None:
    """A blob written but never ref-pointed must not surface via get_ref.

    Simulates the crash midpoint by writing a value blob directly without
    updating the ref. The prior ref value must be preserved; git gc must
    reclaim the orphan.
    """
    backend = GitBackend.init(tmp_path / "repo")
    backend.set_ref("foo", "v1")

    # Simulate set_ref crash: blob written, ref not yet updated.
    orphan_oid = backend._write_blob(b"v2-uncommitted")

    # Ref still resolves to the prior value.
    assert backend.get_ref("foo") == "v1"

    # The orphan blob exists in the object store...
    assert orphan_oid in backend._repo

    # ...but git gc reclaims it because no ref points at it.
    git_dir = Path(backend._repo.path).resolve()
    result = subprocess.run(
        ["git", "gc", "--prune=now", "--aggressive"],
        cwd=str(git_dir),
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, f"git gc failed: {result.stderr}"

    fresh = pygit2.Repository(str(git_dir))
    assert orphan_oid not in fresh, "orphan blob was not reclaimed by git gc"

    # Sanity: the live ref's value blob is still reachable.
    fresh_backend = GitBackend.open(git_dir)
    assert fresh_backend.get_ref("foo") == "v1"


def test_set_ref_orphan_on_unset_ref(tmp_path: Path) -> None:
    """Same contract when set_ref is creating a new ref (not overwriting).

    Crash between blob-write and ref-create leaves get_ref returning
    None; orphan reclaimed by git gc.
    """
    backend = GitBackend.init(tmp_path / "repo")

    orphan_oid = backend._write_blob(b"never-committed")
    assert backend.get_ref("never-set") is None
    assert orphan_oid in backend._repo

    git_dir = Path(backend._repo.path).resolve()
    result = subprocess.run(
        ["git", "gc", "--prune=now", "--aggressive"],
        cwd=str(git_dir),
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, f"git gc failed: {result.stderr}"

    fresh = pygit2.Repository(str(git_dir))
    assert orphan_oid not in fresh
