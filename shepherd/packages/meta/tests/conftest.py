"""Pytest configuration and shared fixtures for shepherd tests.

This file provides fixtures for testing the v2 architecture:
- Scope and lifecycle fixtures
- Mock provider fixtures
- Temporary workspace fixtures
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from shepherd_tests import MockProvider

# Import base fixtures from shepherd-tests (centralized test infrastructure)
from shepherd_tests.conftest import git_workspace  # noqa: F401

# Import shared test contexts

# Subprocess timeout for git operations (seconds)
_SUBPROCESS_TIMEOUT = 30


# =============================================================================
# Temporary directory and workspace fixtures
# =============================================================================


@pytest.fixture
def temp_dir() -> Path:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def temp_workspace(temp_dir: Path) -> Path:
    """Create a git-initialized temporary workspace."""
    subprocess.run(
        ["git", "init"],
        cwd=temp_dir,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=temp_dir,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=temp_dir,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )

    # Create an initial commit
    readme = temp_dir / "README.md"
    readme.write_text("# Test Workspace\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=temp_dir,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=temp_dir,
        capture_output=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )

    return temp_dir


# =============================================================================
# Provider fixtures (package-specific)
# =============================================================================


@pytest.fixture
def mock_claude_provider():
    """Create a ClaudeProvider for testing.

    Note: Currently returns mock results. For real API testing,
    set mock=False and ensure ANTHROPIC_API_KEY is set.
    """
    from shepherd_providers import ClaudeProvider

    return ClaudeProvider(name="test_claude", model="claude-sonnet-4-20250514")


@pytest.fixture
def mock_openai_provider():
    """Create an OpenAIProvider for testing.

    Note: Currently returns mock results. For real API testing,
    set mock=False and ensure OPENAI_API_KEY is set.
    """
    from shepherd_providers import OpenAIProvider

    return OpenAIProvider(name="test_openai", model="gpt-4o")


# =============================================================================
# Scope fixtures
# =============================================================================


@pytest.fixture
def scope_with_mock_provider(mock_provider: MockProvider):
    """Create a Scope with a mock provider already registered."""
    from shepherd_runtime.scope import Scope

    with Scope() as scope:
        scope.register_provider("default", mock_provider, default=True)
        yield scope
