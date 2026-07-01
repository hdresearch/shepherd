"""Legacy private shim for shared task source-validation helpers."""

from __future__ import annotations

from .source_validation import (
    FORBIDDEN_ATTRIBUTES,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_NAMES,
    SourceValidationError,
    validate_task_source,
)

__all__ = [
    "FORBIDDEN_ATTRIBUTES",
    "FORBIDDEN_IMPORTS",
    "FORBIDDEN_NAMES",
    "SourceValidationError",
    "validate_task_source",
]
