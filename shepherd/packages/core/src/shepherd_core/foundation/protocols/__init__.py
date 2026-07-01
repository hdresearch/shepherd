"""Protocol definitions for the primitives layer.

These protocols define the contracts that implementations must satisfy.
They enable duck-typing and allow existing code to satisfy the protocols
without modification.

Layer Structure:
    Layer 0: fold (in fold.py, not a protocol)
    Layer 1: Effect, Stream, Scope (primitives)
    Layer 3: Device (execution backend)

See Also:
    design/syntax-api/ARCHITECTURE-layered-foundation.md - Layer architecture
"""

from __future__ import annotations

from shepherd_core.foundation.protocols.device import (
    ContextState,
    DeviceCapabilities,
    DeviceError,
    DeviceProtocol,
    EffectBundle,
    EffectExtractionError,
    ExecutionContextProtocol,
    ExecutionResult,
    ExecutionSpec,
    ResourceLimits,
    SandboxConfig,
    SandboxCreationError,
    SandboxExecutionError,
    SandboxHandle,
)
from shepherd_core.foundation.protocols.effect import EffectProtocol
from shepherd_core.foundation.protocols.scope import ScopeProtocol
from shepherd_core.foundation.protocols.stream import EffectLayerProtocol, StreamProtocol

__all__ = [
    "ContextState",
    "DeviceCapabilities",
    "DeviceError",
    # Layer 3: Device
    "DeviceProtocol",
    "EffectBundle",
    "EffectExtractionError",
    "EffectLayerProtocol",
    # Layer 1: Primitives
    "EffectProtocol",
    "ExecutionContextProtocol",
    "ExecutionResult",
    "ExecutionSpec",
    "ResourceLimits",
    "SandboxConfig",
    "SandboxCreationError",
    "SandboxExecutionError",
    "SandboxHandle",
    "ScopeProtocol",
    "StreamProtocol",
]
