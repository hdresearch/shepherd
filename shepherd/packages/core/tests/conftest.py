"""Shared fixtures for shepherd-core tests."""

# Import base fixtures from shepherd-tests
from shepherd_tests.conftest import (
    git_workspace,
    isolated_scope,
    mock_output,
    mock_provider,
    test_scope,
)

# Import shared test contexts
from shepherd_tests.contexts import CounterContext, NoOpContext, SimpleContext

# Re-export for test discovery
__all__ = [
    "CounterContext",
    "NoOpContext",
    "SimpleContext",
    "git_workspace",
    "isolated_scope",
    "mock_output",
    "mock_provider",
    "test_scope",
]
