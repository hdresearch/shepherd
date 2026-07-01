"""Focused tests for ScopeProxy bootstrap wiring."""

from __future__ import annotations

from shepherd_runtime._scope._bindings import BindingService
from shepherd_runtime._scope._checkpoint import CheckpointManager
from shepherd_runtime._scope._effect_materialization import EffectMaterializationManager
from shepherd_runtime._scope._emission import EmissionEngine
from shepherd_runtime._scope._hierarchy import HierarchyCoordinator
from shepherd_runtime._scope._inspection import ScopeInspectionFacade
from shepherd_runtime._scope._materialization import MaterializationManager
from shepherd_runtime._scope._provider_registry import ProviderRegistry
from shepherd_runtime._scope._resume import ResumeCoordinator
from shepherd_runtime.execution import ScopeExecutionCacheFacade
from shepherd_runtime.scope import Scope
from shepherd_runtime.session import SessionCleanupCoordinator, SessionCoordinator


def test_scope_bootstrap_installs_collaborators_and_lazy_managers() -> None:
    scope = Scope(root=True)

    assert isinstance(scope._binding_service, BindingService)
    assert isinstance(scope._provider_registry, ProviderRegistry)
    assert isinstance(scope._emission_engine, EmissionEngine)
    assert isinstance(scope._execution, ScopeExecutionCacheFacade)
    assert isinstance(scope._hierarchy, HierarchyCoordinator)
    assert isinstance(scope._inspection, ScopeInspectionFacade)
    assert isinstance(scope._resume, ResumeCoordinator)
    assert isinstance(scope._session_cleanup, SessionCleanupCoordinator)
    assert isinstance(scope._session, SessionCoordinator)

    assert isinstance(scope._get_checkpoint_manager(), CheckpointManager)
    assert isinstance(scope._get_materialization_manager(), MaterializationManager)
    assert isinstance(scope._get_effect_materialization_manager(), EffectMaterializationManager)
