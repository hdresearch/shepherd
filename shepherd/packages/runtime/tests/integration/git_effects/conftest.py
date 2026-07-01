"""Pytest fixtures for git effects testing.

Provides:
- temp_git_repo: Fresh git repository for each test
- workspace_fixture: Git repo suitable for LocalSimulatedDevice
- temp_git_repo_with_branches: Repo with multiple branches

Helper functions are in helpers.py for direct import.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# Re-export helpers for convenience (can also import from helpers directly)


@pytest.fixture
def temp_git_repo() -> Generator[Path, None, None]:
    """Create a temporary git repository for testing.

    The repository is initialized with:
    - A single initial commit
    - A README.md file
    - User config set for commits

    Yields:
        Path to the repository root (not .git).
    """
    with tempfile.TemporaryDirectory(prefix="shepherd-test-repo-") as tmpdir:
        repo = Path(tmpdir)

        # Initialize repo
        subprocess.run(
            ["git", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Configure user (required for commits)
        subprocess.run(
            ["git", "config", "user.email", "test@shepherd.test"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Shepherd Test"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Create initial commit
        readme = repo / "README.md"
        readme.write_text("# Test Repository\n\nCreated for shepherd git effects testing.\n")

        subprocess.run(
            ["git", "add", "."],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        yield repo


@pytest.fixture
def temp_git_repo_with_branches(temp_git_repo: Path) -> Path:
    """Git repository with multiple branches for testing.

    Creates:
    - main (default)
    - feature-a (1 commit ahead)
    - feature-b (1 commit ahead)

    Returns:
        Path to repository root.
    """
    repo = temp_git_repo

    # Create feature-a branch with a commit
    subprocess.run(
        ["git", "checkout", "-b", "feature-a"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "feature-a.txt").write_text("Feature A content\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add feature A"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Create feature-b branch (from main)
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature-b"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "feature-b.txt").write_text("Feature B content\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add feature B"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Return to main
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    return repo


@pytest.fixture
def workspace_fixture(temp_git_repo: Path) -> Path:
    """Alias for temp_git_repo, suitable for LocalSimulatedDevice.

    Returns:
        Path to repository root.
    """
    return temp_git_repo


# =============================================================================
# Pytest markers
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "container: marks test as requiring Podman container runtime",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks test as slow (>1s execution)",
    )
    config.addinivalue_line(
        "markers",
        "integration: marks test as integration test (needs filesystem)",
    )


@pytest.fixture
def requires_podman() -> None:
    """Skip test if Podman is not available."""
    import shutil

    if not shutil.which("podman"):
        pytest.skip("Podman not available")
