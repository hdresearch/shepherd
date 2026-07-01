"""Pytest configuration for property-based runtime tests."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the property test marker for package-local runs."""
    config.addinivalue_line(
        "markers",
        "property: marks tests as property-based tests using Hypothesis",
    )
