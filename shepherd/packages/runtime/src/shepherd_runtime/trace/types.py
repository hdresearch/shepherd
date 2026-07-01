"""Shared trace identity types.

The ``Trace`` container in §07 of the 260503 syntax proposal partitions
records into a kernel half (proof-bearing, normative) and a surface half
(projection, non-normative). This module defines the small set of identity
and tag types that both halves share.

`Ref` is intentionally a re-export of the kernel-v3-reference alias rather
than a parallel definition, so kernel records continue to typecheck unchanged
when imported through ``shepherd_runtime.trace``.

`RunRef` is shared with the syntax nucleus. There is one public class object
for run identity across ``shepherd`` and ``shepherd_runtime.trace``.

`SubTag` is the §07 sub-tag taxonomy used by surface records to filter for
human readers and OTel projections. It is an enum, not an inheritance
hierarchy.
"""

from __future__ import annotations

from enum import Enum

from shepherd_kernel_v3_reference.kernel.ir import Ref

from shepherd_runtime.identities import RunRef

__all__ = ["Ref", "RunRef", "SubTag"]


class SubTag(Enum):
    """The §07 surface-record sub-tag taxonomy.

    Sub-tags are filters for human readers and OTel projections. The proof
    envelope sits on kernel records only; sub-tags do not encode invariants.
    """

    control = "control"
    run = "run"
    branch = "branch"
    proposal = "proposal"
    artifact = "artifact"
