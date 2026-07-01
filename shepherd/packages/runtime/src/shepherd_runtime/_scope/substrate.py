"""Runtime-internal scope substrate hub.

This module centralises all ``shepherd_core.scope.*`` imports used by the
``_scope/*`` implementation cluster so that the rest of the cluster can import
from here instead of reaching into the kernel directly.

**This is not a public API module.**  Do not re-export these symbols from
``shepherd_runtime.__init__`` or any other public surface.  The canonical
public types remain kernel-owned under ``shepherd_core.scope.*``.

Why this exists
---------------
Before this hub, fourteen ``_scope/*`` files each imported directly from
``shepherd_core.scope.{context_ref,model,stream,types}``.  That fan-out made
it hard to reason about the kernel/runtime boundary and meant that any future
extraction or substrate change required touching every file in the cluster.

After this hub, only *this* file imports the kernel substrate directly.  The
rest of ``_scope/*`` imports from ``shepherd_runtime._scope.substrate``.

See ``P0C-0-SCOPE-IMPORT-CONTRACTION-PLAN.md`` (PR 1) for the full rationale.
"""

from shepherd_core.scope.context_ref import ContextRef, T_Context
from shepherd_core.scope.model import ContextBinding, ImmutableScope
from shepherd_core.scope.stream import EffectLayer, Stream
from shepherd_core.scope.types import MaterializationSummary

__all__ = [
    "ContextBinding",
    "ContextRef",
    "EffectLayer",
    "ImmutableScope",
    "MaterializationSummary",
    "Stream",
    "T_Context",
]
