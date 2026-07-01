"""Private ActiveSurface profiles used by early Shepherd integration spikes."""

from __future__ import annotations

from vcs_core._substrate_driver import ActiveSurface, SurfacePolicyError
from vcs_core._substrate_evidence_kinds import EvidenceKind

# Representative overlay-write evidence kind for an admission-time check on the
# session-exec/overlay capture path: we don't yet know which specific OVERLAY_*
# kind a capture will emit, so the gate tests this representative against the
# surface. `read_only_filesystem_surface()` denies it (and the capture op).
_OVERLAY_WRITE_PROBE_KIND: str = EvidenceKind.OVERLAY_WRITE_OBSERVED
_CAPTURE_REDUCTION_SEMANTIC_OP: str = "workspace-capture-reduction"

FILESYSTEM_WRITE_EVIDENCE_KINDS: tuple[str, ...] = (
    EvidenceKind.PYTHON_RUNTIME_WRITE,
    EvidenceKind.PYTHON_RUNTIME_DELETE,
    EvidenceKind.PYTHON_RUNTIME_PATCH,
    EvidenceKind.OVERLAY_FS_EVENT_BUNDLE,
    EvidenceKind.OVERLAY_WRITE_CLOSE,
    EvidenceKind.OVERLAY_WRITE_OPEN,
    EvidenceKind.OVERLAY_WRITE_OBSERVED,
    EvidenceKind.OVERLAY_METADATA_CHANGE,
    EvidenceKind.OVERLAY_UNLINK,
    EvidenceKind.SHELL_WRITE_CLOSE,
    EvidenceKind.SHELL_RENAME,
    EvidenceKind.SHELL_DELETE,
)

WORKSPACE_MUTATION_SEMANTIC_OPS: tuple[str, ...] = (
    "bootstrap",
    "import",
    "workspace-adoption",
    "workspace-capture-reduction",
    "workspace-json-revision",
    "workspace-overlay-merge",
    "workspace-scan",
)


def permissive_active_surface() -> ActiveSurface:
    """Return an explicit no-restriction surface for tests and integration seams."""
    return ActiveSurface()


def read_only_filesystem_surface() -> ActiveSurface:
    """Return the first-cut filesystem read-only surface.

    This is not the full tour-bundle ``ReadOnly`` profile. It is the narrow
    vcs-core projection needed to prove write-shaped workspace observations and
    transitions can be denied by ``ActiveSurface``.
    """
    return ActiveSurface(
        deny_evidence_kinds=frozenset(FILESYSTEM_WRITE_EVIDENCE_KINDS),
        deny_semantic_ops=frozenset(WORKSPACE_MUTATION_SEMANTIC_OPS),
    )


def check_active_surface_admits(
    surface: ActiveSurface,
    *,
    evidence_kind: str,
    semantic_op: str,
    operation: str,
) -> None:
    """Raise :class:`SurfacePolicyError` if ``surface`` denies ``evidence_kind`` or ``semantic_op``.

    Shared allow/deny core for both the python-tier write gate and the
    session-capture admission gate, so the two enforcement points apply one
    policy implementation rather than forking divergent copies.
    """
    if surface.allow_evidence_kinds is not None and evidence_kind not in surface.allow_evidence_kinds:
        raise SurfacePolicyError(
            driver_id="shepherd.workspace_ref",
            reason="observation evidence_kind not in active-surface allow set",
            offending=evidence_kind,
            operation=operation,
        )
    if evidence_kind in surface.deny_evidence_kinds:
        raise SurfacePolicyError(
            driver_id="shepherd.workspace_ref",
            reason="observation evidence_kind denied by active surface",
            offending=evidence_kind,
            operation=operation,
        )
    if surface.allow_semantic_ops is not None and semantic_op not in surface.allow_semantic_ops:
        raise SurfacePolicyError(
            driver_id="shepherd.workspace_ref",
            reason="transition semantic_op not in active-surface allow set",
            offending=semantic_op,
            operation=operation,
        )
    if semantic_op in surface.deny_semantic_ops:
        raise SurfacePolicyError(
            driver_id="shepherd.workspace_ref",
            reason="transition semantic_op denied by active surface",
            offending=semantic_op,
            operation=operation,
        )


def ensure_session_capture_admitted(
    surface: ActiveSurface | None,
    *,
    operation: str = "session exec --capture",
) -> None:
    """Refuse a capturing session exec when ``surface`` denies overlay writes.

    The overlay/session-exec path captures a child process's filesystem writes
    (``OVERLAY_*`` evidence) and reduces them to a ``workspace-capture-reduction``
    transition. This is the substrate-layer (Rung B) admission check: under a
    write-denying surface it raises *before* the subprocess runs. A ``None``
    surface (or a permissive one) admits.
    """
    if surface is None:
        return
    check_active_surface_admits(
        surface,
        evidence_kind=_OVERLAY_WRITE_PROBE_KIND,
        semantic_op=_CAPTURE_REDUCTION_SEMANTIC_OP,
        operation=operation,
    )


__all__ = [
    "FILESYSTEM_WRITE_EVIDENCE_KINDS",
    "WORKSPACE_MUTATION_SEMANTIC_OPS",
    "check_active_surface_admits",
    "ensure_session_capture_admitted",
    "permissive_active_surface",
    "read_only_filesystem_surface",
]
