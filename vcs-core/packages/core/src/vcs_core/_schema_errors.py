"""Shared validation error types for runtime ingress contracts."""

from __future__ import annotations


class SchemaValidationError(ValueError):
    """Raised when a runtime ingress payload violates its declared schema."""
