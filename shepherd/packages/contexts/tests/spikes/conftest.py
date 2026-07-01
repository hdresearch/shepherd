"""Spike test fixtures.

Spike tests are exploratory tests that document behavior and understanding.
They may test internal implementation details and are allowed to be brittle.
"""

import subprocess
from pathlib import Path

import pytest

# Subprocess timeout for git operations (seconds)
_SUBPROCESS_TIMEOUT = 30


@pytest.fixture
def git_workspace(tmp_path: Path) -> Path:
    """Create a temporary git repository for testing.

    Provides a git-initialized directory with:
    - Configured user.email and user.name
    - An initial commit with README.md
    """
    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()

    subprocess.run(
        ["git", "init"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )

    # Create initial file and commit
    (repo_path / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )

    return repo_path
