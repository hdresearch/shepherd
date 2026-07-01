"""Common pytest fixtures for Shepherd tests.

Import these fixtures into your conftest.py:

    from shepherd_tests.conftest import *

Or import specific fixtures:

    from shepherd_tests.conftest import mock_provider, test_scope, git_workspace
"""

import subprocess
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from shepherd_runtime.scope import Scope

from shepherd_tests.mock_provider import MockProvider

# Subprocess timeout for git operations (seconds)
_SUBPROCESS_TIMEOUT = 30


@pytest.fixture
def mock_provider() -> MockProvider:
    """Pre-configured MockProvider for testing.

    Returns a MockProvider with sensible defaults that can be
    customized per-test.

    Example:
        def test_something(mock_provider):
            mock_provider.set_output({"result": "test"})
            # ... use mock_provider
    """
    return MockProvider(
        name="test-mock",
        default_output={"result": "mock_result"},
    )


@pytest.fixture
def test_scope(mock_provider: MockProvider) -> Generator[Scope, None, None]:
    """Fresh Scope with MockProvider for each test.

    The scope is automatically entered and exited, with the
    mock provider registered as default.

    Example:
        def test_something(test_scope):
            test_scope.bind("context", my_context)
            # ... execute tasks
            assert len(test_scope.effects) > 0
    """
    with Scope() as scope:
        scope.register_provider("default", mock_provider, default=True)
        yield scope


@pytest.fixture
def isolated_scope() -> Generator[Scope, None, None]:
    """Isolated root scope (no parent, no auto-nesting).

    Use this when you need complete isolation from any
    global scope that might exist.

    Example:
        def test_isolated(isolated_scope):
            isolated_scope.register_provider("default", MockProvider())
            # Effects don't propagate anywhere
    """
    with Scope(root=True) as scope:
        yield scope


@pytest.fixture
def mock_output() -> dict[str, Any]:
    """Default mock output dictionary.

    Override this fixture to customize the default mock output
    for your test module.

    Example:
        @pytest.fixture
        def mock_output():
            return {"custom": "output", "score": 0.95}
    """
    return {"result": "mock_result"}


@pytest.fixture
def git_workspace(tmp_path: Path) -> Path:
    """Create a temporary git repository for testing.

    Provides a git-initialized directory with:
    - Configured user.email and user.name
    - An initial commit with README.md
    - Consistent test data across all tests

    Use this fixture instead of defining local temp_git_repo or git_repo
    fixtures in individual test files.

    Example:
        def test_something(git_workspace):
            # git_workspace is a Path to an initialized git repo
            (git_workspace / "new_file.txt").write_text("content")
            subprocess.run(["git", "add", "."], cwd=git_workspace)
    """
    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()

    # Initialize git repo with consistent config
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
