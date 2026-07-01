"""Pytest configuration for property-based tests.

Property-based tests use Hypothesis to generate random test inputs
and verify that invariants hold across many examples.
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "property: marks tests as property-based tests using Hypothesis",
    )
