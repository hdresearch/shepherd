"""Surface-record base shape and registry for the §07 record taxonomy.

Surface records are non-normative projections (CONTRACTS E3). The proof
envelope sits on kernel records only; surface records carry sub-tags
that filter for human readers and OTel projections.

This module exposes the shared base plus the registration model used by the
``Trace`` container. Concrete runtime subclasses, including Phase 1 callable
spine records, register themselves as their modules are imported.

Pinned by `docs/design/proposed/260505-plans/CONTRACTS.md` E3.
"""

from __future__ import annotations

from dataclasses import dataclass

from shepherd_runtime.trace.types import Ref, RunRef, SubTag  # noqa: TC001

__all__ = ["SURFACE_REGISTRY", "SurfaceBase", "SurfaceRecord"]


@dataclass(frozen=True, kw_only=True)
class SurfaceBase:
    """Shared fields for every surface record (CONTRACTS E3).

    Concrete subclasses pin ``sub_tag`` as a class default and add
    record-specific fields. The contract surface is the six fields below
    plus the requirement that all subclasses are frozen dataclasses.
    """

    ref: str
    sub_tag: SubTag
    timestamp_us: int
    run_ref: RunRef | None = None
    branch_scope_ref: Ref | None = None
    citing: tuple[Ref, ...] = ()


SurfaceRecord = SurfaceBase
"""Type alias for the surface-record union.

Tightens to a sealed Union after the runtime and proof-backed surface
families settle; for now the alias accepts any ``SurfaceBase`` subclass so the
``Trace`` container has a referenceable type.
"""


SURFACE_REGISTRY: dict[str, type[SurfaceBase]] = {}
"""Discriminator -> class registry used by ``Trace.from_json`` to
reconstruct surface records by their ``type`` field.

Concrete surface-record modules register themselves here at import time. The
registry is intentionally open so new owner-path record families can be added
without weakening the ``Trace`` container into arbitrary dictionaries.
"""
