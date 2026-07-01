"""Public configuration helpers for kernel and runtime policy toggles."""

from __future__ import annotations

import os

# Default: strict mode ON (raise exceptions for potential issues)
# Can be disabled via environment variable or set_strict_mode()
_strict_mode: bool = os.environ.get("SHEPHERD_STRICT_MODE", "true").lower() != "false"


def is_strict_mode() -> bool:
    """Check if strict mode is enabled."""
    return _strict_mode


def set_strict_mode(enabled: bool) -> None:
    """Enable or disable strict mode."""
    global _strict_mode
    _strict_mode = enabled


__all__ = ["is_strict_mode", "set_strict_mode"]
