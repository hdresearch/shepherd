"""Shepherd Foundation - The Irreducible Primitives.

This module exports the foundational primitives that form Shepherd's core:

Layer 0 - The Fold:
    fold()          Compute state from effects (the core invariant)
    fold_with_index()  Fold with effect index available
    scan()          Yield intermediate states (time-travel debugging)
    fold_until()    Fold until predicate satisfied

Layer 1 - Primitives (Protocols):
    Effect      Immutable description of a state change
    Stream      Ordered sequence of effects with queries
    Scope       Container with fork/merge/discard/materialize

Layer 3 - Device (Protocol):
    Device      Execution backend that provides isolated environments

Errors:
    ScopeError          Error related to scope operations
    ContainmentError    Error when effects have escaped containment
    DeviceError         Error related to device operations

Invariant:
    state(t) = fold(apply_effect, effects[0:t], initial_state)

This layer has no internal dependencies except Python stdlib.

See Also:
    design/syntax-api/ARCHITECTURE-layered-foundation.md - Layer architecture
    design/syntax-api/DESIGN-primitives-layer.md - Primitives specification
"""

from __future__ import annotations

# Errors
from shepherd_core.foundation.errors import (
    ContainmentError,
    ScopeError,
)

# Layer 0: The fold
from shepherd_core.foundation.fold import (
    fold,
    fold_until,
    fold_with_index,
    scan,
)

# Layer 1: Primitives (Protocols)
# Layer 3: Device (Protocol)
from shepherd_core.foundation.protocols import (
    ContextState,
    DeviceCapabilities,
    DeviceError,
    DeviceProtocol,
    EffectBundle,
    EffectExtractionError,
    EffectLayerProtocol,
    EffectProtocol,
    ExecutionContextProtocol,
    ExecutionResult,
    ExecutionSpec,
    ResourceLimits,
    SandboxConfig,
    SandboxCreationError,
    SandboxExecutionError,
    SandboxHandle,
    ScopeProtocol,
    StreamProtocol,
)

__all__ = [
    "ContainmentError",
    "ContextState",
    "DeviceCapabilities",
    "DeviceError",
    # Layer 3: Device (Protocol)
    "DeviceProtocol",
    "EffectBundle",
    "EffectExtractionError",
    "EffectLayerProtocol",
    # Layer 1: Primitives (Protocols)
    "EffectProtocol",
    "ExecutionContextProtocol",
    "ExecutionResult",
    "ExecutionSpec",
    "ResourceLimits",
    "SandboxConfig",
    "SandboxCreationError",
    "SandboxExecutionError",
    "SandboxHandle",
    # Errors (Layer 1)
    "ScopeError",
    "ScopeProtocol",
    "StreamProtocol",
    # Layer 0: The fold
    "fold",
    "fold_until",
    "fold_with_index",
    "scan",
]
