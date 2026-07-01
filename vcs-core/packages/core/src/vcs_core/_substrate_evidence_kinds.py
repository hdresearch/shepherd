"""Shared mechanism and evidence-kind constants for capture adapters.

Per SPI v0.1 §Q2 evidence_kind vocabulary, capture-adapter observations
declare evidence kinds using a mechanism-prefixed convention
(``"{mechanism}:{kind}"``). Centralizing these constants here prevents
naming divergence as new adapters land — each adapter imports its mechanism
identifier and evidence-kind values from this module rather than declaring
inline strings.

The module exposes two namespaces:

- :class:`Mechanism` — adapter mechanism identifiers (``overlay``,
  ``python-runtime``, ``shell``, ...). Returned from
  ``CaptureAdapter.mechanism``.
- :class:`EvidenceKind` — concrete evidence-kind constants grouped by
  mechanism. Each ``CaptureAdapter`` declares the subset it can emit via
  its ``evidence_kinds`` property.

Per-mechanism evidence-kind tuples (e.g. :data:`OVERLAY_EVIDENCE_KINDS`)
collect every kind a given adapter advertises, for convenience in the
adapter's property and in validator layer 2's reconciliation check.

This module is **private** in v0.1. Promotion to ``experimental/spi.py``
is reserved for v0.2 if external adapter authors need the constants;
until then, internal adapters import from here directly. Listed as an
implementer affordance in the SPI v0.1 bundle README.
"""

from __future__ import annotations


class Mechanism:
    """Capture-adapter mechanism identifiers (mechanism-prefix tokens).

    Used as the left-hand side of the mechanism-prefixed evidence-kind
    convention and as the value returned from ``CaptureAdapter.mechanism``.
    Each adapter implementation references its mechanism via these
    constants rather than inlining the string.
    """

    OVERLAY = "overlay"
    PYTHON_RUNTIME = "python-runtime"
    SHELL = "shell"


class EvidenceKind:
    """Evidence-kind constants for capture-adapter observations.

    Naming convention: ``"{mechanism}:{kind}"``. Grouped by mechanism for
    readability. Each adapter declares the subset it emits via its
    ``evidence_kinds`` property; validator layer 2 (per-request invariants)
    enforces that every observation's ``evidence_kind`` field appears in
    some declared adapter's set.
    """

    # ---- Overlay capture (driver-default for WorkspaceSubstrateDriver) ----
    OVERLAY_FS_EVENT_BUNDLE = "overlay:fs_event_bundle"
    OVERLAY_WRITE_CLOSE = "overlay:write-close"
    OVERLAY_WRITE_OPEN = "overlay:write-open"
    OVERLAY_WRITE_OBSERVED = "overlay:write-observed"
    OVERLAY_METADATA_CHANGE = "overlay:metadata-change"
    OVERLAY_UNLINK = "overlay:unlink"

    # ---- Python-runtime capture (registry-owned, patch-manager) ----
    PYTHON_RUNTIME_WRITE = "python-runtime:write"
    PYTHON_RUNTIME_DELETE = "python-runtime:delete"
    PYTHON_RUNTIME_PATCH = "python-runtime:patch"

    # ---- Shell capture (T3 ShellCaptureAdapter extraction) ----
    SHELL_WRITE_CLOSE = "shell:write-close"
    SHELL_RENAME = "shell:rename"
    SHELL_DELETE = "shell:delete"


# Per-mechanism evidence-kind tuples — convenient adapter ``evidence_kinds``
# declarations. Adapters may also reference individual constants directly.

OVERLAY_EVIDENCE_KINDS: tuple[str, ...] = (
    EvidenceKind.OVERLAY_FS_EVENT_BUNDLE,
    EvidenceKind.OVERLAY_WRITE_CLOSE,
    EvidenceKind.OVERLAY_WRITE_OPEN,
    EvidenceKind.OVERLAY_WRITE_OBSERVED,
    EvidenceKind.OVERLAY_METADATA_CHANGE,
    EvidenceKind.OVERLAY_UNLINK,
)

PYTHON_RUNTIME_EVIDENCE_KINDS: tuple[str, ...] = (
    EvidenceKind.PYTHON_RUNTIME_WRITE,
    EvidenceKind.PYTHON_RUNTIME_DELETE,
    EvidenceKind.PYTHON_RUNTIME_PATCH,
)

SHELL_EVIDENCE_KINDS: tuple[str, ...] = (
    EvidenceKind.SHELL_WRITE_CLOSE,
    EvidenceKind.SHELL_RENAME,
    EvidenceKind.SHELL_DELETE,
)


__all__ = [
    "OVERLAY_EVIDENCE_KINDS",
    "PYTHON_RUNTIME_EVIDENCE_KINDS",
    "SHELL_EVIDENCE_KINDS",
    "EvidenceKind",
    "Mechanism",
]
