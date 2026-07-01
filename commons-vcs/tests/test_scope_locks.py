"""Per-scope advisory lock tests."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from commons_vcs.backends.git import GitBackend, ScopeLockTimeoutError

SCOPE_A = "sha256:" + "a" * 64
SCOPE_B = "sha256:" + "b" * 64


def test_same_scope_lock_serializes_processes(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    GitBackend.init(repo_path)
    marker = tmp_path / "locked"

    holder = """
import sys
import time
from pathlib import Path
from commons_vcs.backends.git import GitBackend

repo_path, scope_id, marker = sys.argv[1:4]
backend = GitBackend.open(repo_path)
with backend.scope_lock(scope_id):
    Path(marker).write_text("locked")
    time.sleep(0.35)
"""
    contender = """
import sys
import time
from commons_vcs.backends.git import GitBackend

repo_path, scope_id = sys.argv[1:3]
backend = GitBackend.open(repo_path)
started = time.monotonic()
with backend.scope_lock(scope_id, timeout=2.0):
    elapsed = time.monotonic() - started
print(f"{elapsed:.3f}")
"""

    cwd = str(Path(__file__).resolve().parent.parent)
    p1 = subprocess.Popen([sys.executable, "-c", holder, str(repo_path), SCOPE_A, str(marker)], cwd=cwd)
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()

    p2 = subprocess.run(
        [sys.executable, "-c", contender, str(repo_path), SCOPE_A],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    assert p1.wait(timeout=5) == 0
    assert float(p2.stdout.strip()) >= 0.2


def test_scope_lock_timeout(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    with backend.scope_lock(SCOPE_A, timeout=0.1):
        second = GitBackend.open(tmp_path / "repo")
        with pytest.raises(ScopeLockTimeoutError), second.scope_lock(SCOPE_A, timeout=0.05):
            pass


def test_distinct_scope_locks_do_not_block(tmp_path: Path) -> None:
    backend = GitBackend.init(tmp_path / "repo")
    with backend.scope_lock(SCOPE_A, timeout=0.1):
        other = GitBackend.open(tmp_path / "repo")
        started = time.monotonic()
        with other.scope_lock(SCOPE_B, timeout=0.1):
            pass
        assert time.monotonic() - started < 0.05
