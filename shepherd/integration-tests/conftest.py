"""Cross-package integration test configuration.

These tests verify that all Shepherd packages work together correctly.
"""

import pytest
from shepherd_runtime.scope import Scope
from shepherd_tests import MockProvider


@pytest.fixture
def mock_provider() -> MockProvider:
    """Pre-configured MockProvider for integration tests."""
    return MockProvider(
        name="integration-mock",
        default_output={"result": "integration_test_result"},
    )


@pytest.fixture
def integration_scope(mock_provider: MockProvider):
    """Isolated scope for integration testing."""
    with Scope(root=True) as scope:
        scope.register_provider("default", mock_provider, default=True)
        yield scope
