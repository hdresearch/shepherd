"""Helper functions for git effects tests.

These are utility functions used across multiple test files.
Import directly: from .helpers import get_head_sha, create_commit
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def get_head_sha(repo: Path) -> str:
    """Get current HEAD SHA.

    Args:
        repo: Path to git repository root.

    Returns:
        40-character SHA of HEAD commit.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_branch_sha(repo: Path, branch: str) -> str | None:
    """Get SHA for a specific branch.

    Args:
        repo: Path to git repository root.
        branch: Branch name.

    Returns:
        40-character SHA, or None if branch doesn't exist.
    """
    result = subprocess.run(
        ["git", "rev-parse", branch],
        check=False,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def create_commit(repo: Path, message: str, filename: str | None = None) -> str:
    """Create a commit and return its SHA.

    Args:
        repo: Path to git repository root.
        message: Commit message.
        filename: Optional filename to create/modify. If None, creates empty commit.

    Returns:
        40-character SHA of new commit.
    """
    if filename:
        (repo / filename).write_text(f"Content for {filename}\n")
        subprocess.run(
            ["git", "add", filename],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    subprocess.run(
        ["git", "commit", "-m", message, "--allow-empty"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return get_head_sha(repo)


def create_branch(repo: Path, branch_name: str, checkout: bool = False) -> str:
    """Create a branch and optionally check it out.

    Args:
        repo: Path to git repository root.
        branch_name: Name for new branch.
        checkout: If True, checkout the branch after creating.

    Returns:
        SHA of the branch pointer.
    """
    if checkout:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    return get_branch_sha(repo, branch_name) or ""


def delete_branch(repo: Path, branch_name: str, force: bool = False) -> None:
    """Delete a branch.

    Args:
        repo: Path to git repository root.
        branch_name: Branch to delete.
        force: If True, force delete (-D) instead of safe delete (-d).
    """
    flag = "-D" if force else "-d"
    subprocess.run(
        ["git", "branch", flag, branch_name],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def checkout(repo: Path, ref: str) -> None:
    """Checkout a branch or ref.

    Args:
        repo: Path to git repository root.
        ref: Branch name or SHA to checkout.
    """
    subprocess.run(
        ["git", "checkout", ref],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def get_current_branch(repo: Path) -> str | None:
    """Get current branch name.

    Args:
        repo: Path to git repository root.

    Returns:
        Branch name, or None if in detached HEAD state.
    """
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        check=False,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    branch = result.stdout.strip()
    return branch or None
