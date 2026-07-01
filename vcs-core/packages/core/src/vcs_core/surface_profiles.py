"""Public active-surface profile helpers for integration callers.

The concrete profile implementation remains internal; this module is the
supported owner path for callers that need to lower external policy names into
vcs-core's ``ActiveSurface`` values or preflight session capture admission.
"""

from __future__ import annotations

from vcs_core._active_surface_profiles import (
    FILESYSTEM_WRITE_EVIDENCE_KINDS,
    WORKSPACE_MUTATION_SEMANTIC_OPS,
    check_active_surface_admits,
    ensure_session_capture_admitted,
    permissive_active_surface,
    read_only_filesystem_surface,
)
from vcs_core.spi import ActiveSurface, SurfacePolicyError

__all__ = [
    "FILESYSTEM_WRITE_EVIDENCE_KINDS",
    "WORKSPACE_MUTATION_SEMANTIC_OPS",
    "ActiveSurface",
    "SurfacePolicyError",
    "check_active_surface_admits",
    "ensure_session_capture_admitted",
    "permissive_active_surface",
    "read_only_filesystem_surface",
]
