"""Public runtime effect-materialization owner paths.

This surface is intentionally distinct from `shepherd_runtime.materialization`,
which owns context-level materialization (`commit()`-style context escape).
This module owns effect-level materialization used by `scope.materialize()`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._effect_materialization_builtin import GitWorkspacePatchMaterializer, create_workspace_materializer
from ._effect_materialization_impl import (
    MaterializationError,
    MaterializationResult,
    Materializer,
    MaterializerRegistry,
    ReversalError,
    get_materializer,
    get_materializer_registry,
    register_materializer,
    reset_materializer_registry,
)

MaterializationError.__module__ = __name__
MaterializationResult.__module__ = __name__
MaterializerRegistry.__module__ = __name__
ReversalError.__module__ = __name__
GitWorkspacePatchMaterializer.__module__ = __name__

if TYPE_CHECKING:
    from shepherd_runtime.scope import ScopeProxy


def get_materializer_registry_with_builtins(
    scope: ScopeProxy | None = None,
) -> MaterializerRegistry:
    """Return a fresh effect-materializer registry with runtime built-ins."""
    registry = MaterializerRegistry()

    if scope is not None:
        registry.register(create_workspace_materializer(scope))

    return registry


__all__ = [
    "GitWorkspacePatchMaterializer",
    "MaterializationError",
    "MaterializationResult",
    "Materializer",
    "MaterializerRegistry",
    "ReversalError",
    "create_workspace_materializer",
    "get_materializer",
    "get_materializer_registry",
    "get_materializer_registry_with_builtins",
    "register_materializer",
    "reset_materializer_registry",
]
