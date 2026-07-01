"""GitHub utility functions.

Pure Python functions for interacting with the GitHub API.
No LLM tokens are consumed - these are deterministic operations.

Can be used directly, wrapped in tasks for effect tracking,
or called from composite task execute() methods.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import TYPE_CHECKING

from .models import (
    PRAuthor,
    PRCommit,
    PRDetails,
    PRFile,
    PRLabel,
    PRReview,
)

if TYPE_CHECKING:
    from pathlib import Path

    from github.PullRequest import PullRequest
    from github.PullRequestReview import PullRequestReview


class GitHubTokenError(ValueError):
    """Raised when no GitHub token can be found."""


class GitHubRepoError(ValueError):
    """Raised when repository cannot be determined."""


def get_github_token(token: str | None = None) -> str:
    """Get GitHub token with fallback chain.

    Resolution order:
    1. Explicit token parameter
    2. GITHUB_TOKEN environment variable
    3. gh CLI auth token (via `gh auth token` command)

    Args:
        token: Optional explicit token to use.

    Returns:
        The resolved GitHub token.

    Raises:
        GitHubTokenError: If no token can be found.
    """
    # 1. Explicit token
    if token:
        return token

    # 2. Environment variable
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        return env_token

    # 3. gh CLI token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        gh_token = result.stdout.strip()
        if gh_token:
            return gh_token
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    raise GitHubTokenError(
        "No GitHub token found. Provide a token parameter, "
        "set GITHUB_TOKEN environment variable, or run 'gh auth login'."
    )


def get_repo_from_git(cwd: Path | str | None = None) -> str:
    """Infer owner/repo from git remote URL.

    Parses formats:
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo
    - git@github.com:owner/repo.git
    - git@github.com:owner/repo
    - ssh://git@github.com/owner/repo.git

    Args:
        cwd: Working directory to run git command in. Defaults to current directory.

    Returns:
        Repository in "owner/repo" format.

    Raises:
        GitHubRepoError: If not in a git repository or remote URL cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        url = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitHubRepoError(f"Failed to get git remote URL: {e.stderr}") from e
    except FileNotFoundError as e:
        raise GitHubRepoError("git command not found") from e

    return parse_repo_from_url(url)


def parse_repo_from_url(url: str) -> str:
    """Parse owner/repo from a git remote URL.

    Args:
        url: Git remote URL.

    Returns:
        Repository in "owner/repo" format.

    Raises:
        GitHubRepoError: If URL cannot be parsed.
    """
    # Normalize: remove trailing slash and .git suffix
    url_normalized = url.rstrip("/")
    url_normalized = url_normalized.removesuffix(".git")

    # Try HTTPS format: https://github.com/owner/repo
    match = re.search(r"https?://[^/]+/([^/]+)/([^/]+)/?$", url_normalized)
    if match:
        return f"{match.group(1)}/{match.group(2)}"

    # Try SSH format: git@github.com:owner/repo or ssh://git@host/owner/repo
    match = re.search(r"[:/]([^/]+)/([^/]+)/?$", url_normalized)
    if match:
        return f"{match.group(1)}/{match.group(2)}"

    raise GitHubRepoError(f"Could not parse owner/repo from URL: {url}")


def _compute_state(pr: PullRequest) -> str:
    """Convert PyGithub PR state to standardized format."""
    if pr.merged:
        return "MERGED"
    return pr.state.upper()  # "open" -> "OPEN", "closed" -> "CLOSED"


def _compute_review_decision(reviews: list[PullRequestReview]) -> str | None:
    """Compute review decision from list of reviews.

    Algorithm:
    1. Group reviews by author, keep latest state per author
    2. Only consider APPROVED and CHANGES_REQUESTED states
    3. CHANGES_REQUESTED from any author -> CHANGES_REQUESTED
    4. All APPROVED -> APPROVED
    5. Otherwise -> None
    """
    if not reviews:
        return None

    # Get latest review state per author
    latest_by_author: dict[str, str] = {}
    for review in reviews:
        if review.user and review.state in ("APPROVED", "CHANGES_REQUESTED"):
            latest_by_author[review.user.login] = review.state

    if not latest_by_author:
        return None

    # CHANGES_REQUESTED takes precedence
    if "CHANGES_REQUESTED" in latest_by_author.values():
        return "CHANGES_REQUESTED"

    if "APPROVED" in latest_by_author.values():
        return "APPROVED"

    return None


def get_pr_details(
    pr_number: int,
    repo: str | None = None,
    token: str | None = None,
) -> PRDetails:
    """Retrieve detailed information about a GitHub pull request.

    Uses the PyGithub library to fetch PR metadata, files changed,
    commits, and review status for the specified PR number.

    Args:
        pr_number: The pull request number to retrieve.
        repo: Optional repository in "owner/repo" format. If not specified,
            infers from git remote URL of current directory.
        token: Optional GitHub token. If not specified, uses GITHUB_TOKEN
            environment variable or gh CLI authentication.

    Returns:
        PRDetails with complete PR information.

    Raises:
        GitHubTokenError: If token is not available.
        GitHubRepoError: If repo cannot be determined.
        github.UnknownObjectException: If PR or repository not found.
        github.BadCredentialsException: If authentication fails.
        github.RateLimitExceededException: If API rate limit exceeded.

    Example:
        >>> pr = get_pr_details(123, repo="owner/repo")
        >>> print(f"PR #{pr.number}: {pr.title}")
        >>> print(f"Files changed: {pr.changed_files}")

        >>> # With automatic repo detection (from git remote)
        >>> pr = get_pr_details(456)
        >>> print(f"PR from current repo: {pr.title}")
    """
    # Import PyGithub lazily (optional dependency)
    try:
        from github import Auth, Github
    except ImportError as e:
        raise ImportError(
            "PyGithub is required for GitHub operations. Install with: pip install shepherd-coding[github]"
        ) from e

    # Resolve token and repo
    resolved_token = get_github_token(token)
    resolved_repo = repo or get_repo_from_git()

    # Create client and fetch PR
    g = Github(auth=Auth.Token(resolved_token))
    try:
        repository = g.get_repo(resolved_repo)
        pr = repository.get_pull(pr_number)

        # Fetch related data
        files = list(pr.get_files())
        commits = list(pr.get_commits())
        reviews = list(pr.get_reviews())

        # Transform to Pydantic models
        return PRDetails(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            author=PRAuthor(login=pr.user.login, name=pr.user.name),
            state=_compute_state(pr),
            created_at=pr.created_at.isoformat(),
            updated_at=pr.updated_at.isoformat(),
            url=pr.html_url,
            base_ref_name=pr.base.ref,
            head_ref_name=pr.head.ref,
            additions=pr.additions,
            deletions=pr.deletions,
            changed_files=pr.changed_files,
            labels=[PRLabel(name=label.name, color=label.color, description=label.description) for label in pr.labels],
            files=[
                PRFile(
                    path=f.filename,
                    additions=f.additions,
                    deletions=f.deletions,
                    patch=f.patch,
                    status=f.status or "modified",
                    previous_path=f.previous_filename,
                )
                for f in files
            ],
            commits=[
                PRCommit(
                    oid=c.sha,
                    message_headline=c.commit.message.split("\n")[0] if c.commit.message else "",
                    authored_date=c.commit.author.date.isoformat(),
                    authors=[
                        PRAuthor(login=c.author.login, name=c.author.name)
                        if c.author
                        else PRAuthor(login="", name=c.commit.author.name)
                    ],
                )
                for c in commits
            ],
            reviews=[
                PRReview(
                    author=PRAuthor(login=r.user.login, name=r.user.name) if r.user else PRAuthor(login="", name=None),
                    state=r.state,
                    body=r.body or "",
                )
                for r in reviews
            ],
            review_decision=_compute_review_decision(reviews),
            head_sha=pr.head.sha,
            clone_url=(pr.head.repo.clone_url if pr.head.repo else pr.base.repo.clone_url),
        )
    finally:
        g.close()


__all__ = [
    "GitHubRepoError",
    # Exceptions
    "GitHubTokenError",
    # Token and repo utilities
    "get_github_token",
    # PR operations
    "get_pr_details",
    "get_repo_from_git",
    "parse_repo_from_url",
]
