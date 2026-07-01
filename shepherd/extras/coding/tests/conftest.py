"""Shared fixtures for shepherd-coding tests."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from shepherd_coding.workflows.pr_review.config import PRReviewConfig, VerifyConfig

_SUBPROCESS_TIMEOUT = 30


@pytest.fixture
def sample_config() -> PRReviewConfig:
    """Fully-populated PRReviewConfig including nested VerifyConfig."""
    return PRReviewConfig(
        guidelines="Follow PEP 8. Prefer composition over inheritance.",
        focus_areas=["correctness", "security", "performance"],
        max_comments=8,
        file_patterns_to_skip=["*.lock", "*.generated.*", "docs/*"],
        verify=VerifyConfig(
            test_command="pytest tests/ -x",
            build_command="make typecheck",
            setup_commands=["pip install -e '.[dev]'"],
            container_image="python:3.12",
        ),
    )


@pytest.fixture
def minimal_config() -> PRReviewConfig:
    """PRReviewConfig with all defaults."""
    return PRReviewConfig()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Temporary .shepherd/ directory for persistence tests."""
    d = tmp_path / ".shepherd"
    d.mkdir()
    return d


@pytest.fixture
def git_workspace(tmp_path: Path) -> Path:
    """Temporary git repository with initial commit."""
    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()

    for cmd in [
        ["git", "init"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test User"],
    ]:
        subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            check=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )

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


@pytest.fixture
def mock_workspace(git_workspace: Path) -> Path:
    """Git repo populated with realistic codebase signals."""
    (git_workspace / "pyproject.toml").write_text(
        '[project]\nname = "example"\n\n'
        "[tool.ruff]\nline-length = 100\n\n"
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )
    ci_dir = git_workspace / ".github" / "workflows"
    ci_dir.mkdir(parents=True)
    (ci_dir / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n  test:\n    steps:\n      - run: pytest tests/ -x\n      - run: ruff check .\n"
    )
    (git_workspace / "CONTRIBUTING.md").write_text("# Contributing\n\nPlease write tests for all changes.\n")
    (git_workspace / ".gitignore").write_text("__pycache__/\n*.pyc\ndist/\n.eggs/\n")
    src_dir = git_workspace / "src" / "app"
    src_dir.mkdir(parents=True)
    (src_dir / "main.py").write_text("def main() -> None: ...\n")
    return git_workspace


@pytest.fixture
def mock_config_output() -> dict[str, Any]:
    """Structured output dict MockProvider returns for ConfigurePRReview."""
    return {
        "config": {
            "guidelines": "Follow PEP 8. Write tests for all changes.",
            "focus_areas": ["correctness", "security"],
            "max_comments": 5,
            "file_patterns_to_skip": ["*.lock", "*.generated.*"],
            "verify": {
                "test_command": "pytest tests/ -x",
                "build_command": None,
                "setup_commands": [],
                "container_image": "python:3.12",
            },
            "repo": None,
            "github_token": None,
            "clone_url": None,
        }
    }
