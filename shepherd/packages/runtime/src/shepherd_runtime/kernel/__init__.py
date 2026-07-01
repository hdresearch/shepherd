"""Kernel-layer re-exports for cross-plan trace and runtime use.

CONTRACTS E0 (`ExecutionContext`) is the load-bearing entry here: every
trace record stamped during a Run cites the active triple
``(binding_env_ref, region_ref, authority_ref)``. Plans 01 (provider
boundary) and 04 (effects nucleus) consume this re-export; the
authoritative dataclass lives in ``shepherd_kernel_v3_reference``.

Pinned by `docs/design/proposed/260505-plans/CONTRACTS.md` E0.
"""

from __future__ import annotations

from shepherd_kernel_v3_reference.kernel.context import ExecutionContext

from .canary import (
    KernelV3CanaryMismatchError,
    KernelV3CanaryMode,
    KernelV3CanaryPolicy,
    KernelV3CanaryReport,
    KernelV3CanarySpec,
    clear_kernel_v3_canary_cache,
    get_kernel_v3_canary_policy,
    kernel_v3_canary,
    kernel_v3_canary_policy,
)

__all__ = [
    "ExecutionContext",
    "KernelV3CanaryMismatchError",
    "KernelV3CanaryMode",
    "KernelV3CanaryPolicy",
    "KernelV3CanaryReport",
    "KernelV3CanarySpec",
    "clear_kernel_v3_canary_cache",
    "get_kernel_v3_canary_policy",
    "kernel_v3_canary",
    "kernel_v3_canary_policy",
]
