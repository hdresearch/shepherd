"""Secure task reconstruction helpers."""

from __future__ import annotations

from typing import Any

from shepherd_runtime.task.reconstruction import ReconstructionError
from shepherd_runtime.task.source_validation import SourceValidationError

from ._task_reconstruction import secure_reconstruct_task_class as _secure_reconstruct_task_class

__all__ = [
    "SecurityError",
    "secure_reconstruct_task_class",
]


class SecurityError(Exception):
    """Raised when restricted reconstruction rejects a source string."""


def _translate_reconstruction_exception(exc: Exception) -> Exception:
    """Map canonical reconstruction errors onto the public runtime API surface."""
    if isinstance(exc, SourceValidationError):
        syntax_errors = [v for v in exc.violations if v.lower().startswith("syntax error:")]
        if syntax_errors:
            return SyntaxError(syntax_errors[0])
        return SecurityError("; ".join(exc.violations))

    if isinstance(exc, ReconstructionError):
        if exc.error_type == "MISSING_TASK_DECORATOR":
            return ValueError("No @task class found in restricted source")
        if exc.error_type == "SYNTAX_ERROR":
            return SyntaxError(exc.message)
        return SecurityError(exc.message)

    return exc


def secure_reconstruct_task_class(
    source: str,
    imports: list[str] | None = None,
    extra_namespace: dict[str, Any] | None = None,
    *,
    allowed_imports: frozenset[str] = frozenset(),
) -> type:
    """Securely reconstruct a task class using the canonical implementation."""
    try:
        return _secure_reconstruct_task_class(
            source,
            imports,
            extra_namespace,
            allowed_imports=allowed_imports,
        )
    except Exception as exc:
        translated = _translate_reconstruction_exception(exc)
        if translated is exc:
            raise
        raise translated from exc
