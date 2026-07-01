"""Bootstrap wiring for ScopeProxy collaborators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..execution import ScopeExecutionCacheFacade
from ..session import SessionCleanupCoordinator, SessionCoordinator
from ._binding_registry import BindingRegistry
from ._bindings import BindingService
from ._emission import EmissionEngine
from ._hierarchy import HierarchyCoordinator
from ._hosts import (
    ScopeBindingHostAdapter,
    ScopeCheckpointHostAdapter,
    ScopeEffectMaterializationHostAdapter,
    ScopeEmissionHostAdapter,
    ScopeExecutionHostAdapter,
    ScopeHierarchyHostAdapter,
    ScopeInspectionHostAdapter,
    ScopeMaterializationHostAdapter,
    ScopeSessionHostAdapter,
)
from ._inspection import ScopeInspectionFacade
from ._provider_registry import ProviderRegistry
from ._resume import ResumeCoordinator

if TYPE_CHECKING:
    from .scope import ScopeProxy

__all__ = ["install_scope_bootstrap"]


def install_scope_bootstrap(owner: ScopeProxy) -> None:
    """Install collaborator graph on a fresh ScopeProxy instance."""
    owner._binding_registry = BindingRegistry(lambda: owner._scope.bindings)  # type: ignore[attr-defined]

    owner._execution_host = ScopeExecutionHostAdapter(owner)  # type: ignore[attr-defined]
    owner._binding_host = ScopeBindingHostAdapter(owner)  # type: ignore[attr-defined]
    owner._emission_host = ScopeEmissionHostAdapter(owner)  # type: ignore[attr-defined]
    owner._hierarchy_host = ScopeHierarchyHostAdapter(owner)  # type: ignore[attr-defined]
    owner._session_host = ScopeSessionHostAdapter(owner)  # type: ignore[attr-defined]
    owner._inspection_host = ScopeInspectionHostAdapter(owner)  # type: ignore[attr-defined]
    owner._checkpoint_host = ScopeCheckpointHostAdapter(owner)  # type: ignore[attr-defined]
    owner._materialization_host = ScopeMaterializationHostAdapter(owner)  # type: ignore[attr-defined]
    owner._effect_materialization_host = ScopeEffectMaterializationHostAdapter(owner)  # type: ignore[attr-defined]

    owner._binding_service = BindingService(owner._binding_host, owner._binding_registry)  # type: ignore[attr-defined]
    owner._provider_registry = ProviderRegistry(  # type: ignore[attr-defined]
        state_getter=lambda: owner._provider_state,
        state_setter=lambda state: setattr(owner, "_provider_state", state),
        parent_registry_getter=lambda: getattr(owner._parent_proxy, "_provider_registry", None),
    )
    owner._emission_engine = EmissionEngine(owner._emission_host)  # type: ignore[attr-defined]
    owner._execution = ScopeExecutionCacheFacade(owner, owner._execution_host)  # type: ignore[attr-defined]
    owner._hierarchy = HierarchyCoordinator(owner, owner._hierarchy_host)  # type: ignore[attr-defined]
    owner._inspection = ScopeInspectionFacade(owner._inspection_host)  # type: ignore[attr-defined]
    owner._resume = ResumeCoordinator(owner)  # type: ignore[attr-defined]
    owner._session_cleanup = SessionCleanupCoordinator(owner._session_host)  # type: ignore[attr-defined]
    owner._session = SessionCoordinator(owner, owner._session_host, owner._session_cleanup)  # type: ignore[attr-defined]
