"""Layer 1: Scope substrate.

This package provides the immutable scope kernel and related substrate types:
- ImmutableScope: immutable core with pure transformation methods
- ContextRef: live reference to bound contexts
- Stream: ordered, queryable sequence of effects
- MaterializationSummary: result of scope.materialize() (Phase 1b)
"""

from __future__ import annotations

from .context_ref import (
    ContextAccessor,
    ContextRef,
    T_Context,
)
from .model import (
    ContextBinding,
    ImmutableScope,
)
from .stream import (
    EffectLayer,
    Stream,
)
from .types import (
    MaterializationSummary,
)

__all__ = [
    # Context ref
    "ContextAccessor",
    "ContextBinding",
    "ContextRef",
    "EffectLayer",
    "ImmutableScope",
    # Types (Phase 1b)
    "MaterializationSummary",
    # Stream
    "Stream",
    "T_Context",
]
