"""Standalone-core fallback for container preflight validation (D44).

This module provides structured validation before container execution
to catch configuration issues early with clear, actionable error messages.

Pre-flight checks are composable:
- Core checks (tools, capabilities, VM runner)
- Context-contributed checks via preflight_check() method

Example:
    result = preflight_check(spec, bindings, vm_runner=runner, platform="darwin")
    for warning in result.warnings:
        logger.warning(f"[Pre-flight] {warning}")
    result.raise_if_errors()

See Also:
    PROPOSED-DX-IMPROVEMENTS.md - Original proposal
    design/DECISIONS.md#d44 - Decision record
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shepherd_core.foundation.protocols.device import ExecutionSpec
    from shepherd_core.types import ProviderBinding

    from shepherd_runtime.device.container.vm_paths import VMCommandRunner

logger = logging.getLogger(__name__)


class PreflightError(Exception):
    """Pre-flight validation failed with errors.

    Raised by PreflightResult.raise_if_errors() when there are
    blocking configuration errors.

    Attributes:
        errors: Tuple of error messages.
        warnings: Tuple of warning messages (non-blocking).
    """

    def __init__(self, errors: tuple[str, ...], warnings: tuple[str, ...] = ()):
        self.errors = errors
        self.warnings = warnings
        message = "Pre-flight validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        if warnings:
            message += "\n\nWarnings:\n" + "\n".join(f"  - {w}" for w in warnings)
        super().__init__(message)


@dataclass(frozen=True)
class PreflightResult:
    """Result of pre-flight validation checks.

    Provides a structured way to collect warnings and errors from
    validation, with helpers for logging and raising on errors.

    Attributes:
        warnings: Non-blocking issues that should be logged.
        errors: Blocking issues that should halt execution.
    """

    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def is_ok(self) -> bool:
        """True if no errors (warnings are allowed)."""
        return len(self.errors) == 0

    def raise_if_errors(self) -> None:
        """Raise PreflightError if there are any errors.

        Raises:
            PreflightError: If self.errors is non-empty.
        """
        if self.errors:
            raise PreflightError(self.errors, self.warnings)

    def log_warnings(self, logger: logging.Logger | None = None) -> None:
        """Log all warnings using the provided logger.

        Args:
            logger: Logger to use. Defaults to module logger.
        """
        log = logger or globals()["logger"]
        for warning in self.warnings:
            log.warning("[Pre-flight] %s", warning)

    def merge(self, other: PreflightResult) -> PreflightResult:
        """Combine two preflight results.

        Args:
            other: Another PreflightResult to merge.

        Returns:
            New PreflightResult with combined warnings and errors.
        """
        return PreflightResult(
            warnings=self.warnings + other.warnings,
            errors=self.errors + other.errors,
        )

    @classmethod
    def ok(cls) -> PreflightResult:
        """Create an empty (successful) result."""
        return cls()

    @classmethod
    def warning(cls, message: str) -> PreflightResult:
        """Create a result with a single warning."""
        return cls(warnings=(message,))

    @classmethod
    def error(cls, message: str) -> PreflightResult:
        """Create a result with a single error."""
        return cls(errors=(message,))


def preflight_check(
    spec: ExecutionSpec,
    bindings: Sequence[ProviderBinding],
    *,
    vm_runner: VMCommandRunner | None = None,
    platform: str | None = None,
) -> PreflightResult:
    """Pre-flight checks for container execution.

    Validates the execution specification and bindings before
    starting container execution. Returns a structured result
    with warnings and errors.

    Args:
        spec: The execution specification to validate.
        bindings: Provider bindings to validate.
        vm_runner: VM runner instance (required on macOS).
        platform: Platform identifier (for testing, defaults to sys.platform).

    Returns:
        PreflightResult with any warnings and errors.

    Example:
        result = preflight_check(spec, bindings, vm_runner=runner)
        result.log_warnings()
        result.raise_if_errors()
    """
    if platform is None:
        platform = sys.platform

    warnings: list[str] = []
    errors: list[str] = []

    # Check tools
    if not spec.tools:
        warnings.append(
            "No tools passed to container. "
            "LLM won't be able to use file tools. "
            "Check that context capabilities include 'read'/'write'."
        )

    # Check capabilities on bindings
    for binding in bindings:
        if hasattr(binding, "capabilities") and not binding.capabilities:
            warnings.append(f"Binding '{binding.context_id}' has no capabilities")

    # Check VM runner for macOS (this is an error, not warning)
    is_macos = platform == "darwin"
    if is_macos and vm_runner is None:
        errors.append(
            "Running on macOS but no VM runner configured. "
            "Overlay extraction will fail. "
            "Ensure PodmanSandboxManager has a valid VMCommandRunner."
        )

    result = PreflightResult(
        warnings=tuple(warnings),
        errors=tuple(errors),
    )

    # Collect context-contributed checks
    for binding in bindings:
        ctx = getattr(binding, "context", None)
        if ctx is not None and hasattr(ctx, "preflight_check"):
            try:
                ctx_result = ctx.preflight_check()
                if isinstance(ctx_result, PreflightResult):
                    result = result.merge(ctx_result)
            except Exception as e:  # noqa: BLE001
                # Don't let a broken preflight_check block execution
                logger.warning(
                    "Context %s.preflight_check() raised: %s",
                    type(ctx).__name__,
                    e,
                )

    return result


def preflight_check_spec(spec: ExecutionSpec) -> PreflightResult:
    """Validate ExecutionSpec fields.

    Lighter-weight check that only validates the spec itself,
    without needing bindings or VM runner.

    Args:
        spec: The execution specification to validate.

    Returns:
        PreflightResult with any warnings and errors.
    """
    warnings: list[str] = []
    errors: list[str] = []

    # Task-spec path: skip LLM prompt/provider validation
    if spec.task_spec is not None:
        if not spec.task_spec.task_source:
            errors.append("TaskSpec.task_source is required but empty")
        return PreflightResult(
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    # LLM path: validate prompt and provider_config
    if not spec.prompt:
        errors.append("ExecutionSpec.prompt is required but empty")

    if not spec.provider_config:
        errors.append("ExecutionSpec.provider_config is required but empty")

    return PreflightResult(
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


__all__ = [
    "PreflightError",
    "PreflightResult",
    "preflight_check",
    "preflight_check_spec",
]
