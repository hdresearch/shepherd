"""Private collaborator host adapters for ScopeProxy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .substrate import ContextRef

if TYPE_CHECKING:
    from shepherd_core.context.kernel import ExecutionContext
    from shepherd_core.effects import Effect
    from shepherd_core.foundation.protocols.device import DeviceProtocol
    from shepherd_core.provider import Provider

    from shepherd_runtime.cache import CacheStore
    from shepherd_runtime.checkpoint import Checkpoint
    from shepherd_runtime.effect_materialization import MaterializerRegistry
    from shepherd_runtime.persistence import PersistenceConfig

    from ._binding_registry import BindingWithState
    from ._provider_state import ProviderState
    from .scope import ScopeProxy
    from .substrate import ContextBinding, EffectLayer, ImmutableScope

__all__ = [
    "ScopeBindingHostAdapter",
    "ScopeCheckpointHostAdapter",
    "ScopeEffectMaterializationHostAdapter",
    "ScopeEmissionHostAdapter",
    "ScopeExecutionHostAdapter",
    "ScopeHierarchyHostAdapter",
    "ScopeInspectionHostAdapter",
    "ScopeMaterializationHostAdapter",
    "ScopeSessionHostAdapter",
]


class ScopeExecutionHostAdapter:
    """Execution/device/cache host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    @property
    def execution_parent(self) -> ScopeProxy | None:
        return self._owner._parent_proxy

    @property
    def execution_device_override(self) -> DeviceProtocol | None:
        return self._owner._device

    @execution_device_override.setter
    def execution_device_override(self, value: DeviceProtocol | None) -> None:
        self._owner._device = value

    def execution_ambient_device(self) -> DeviceProtocol | None:
        from ..device import get_current_device

        return get_current_device()

    def resolve_execution_provider(self, provider: Provider | str | None) -> Provider:
        if provider is None:
            return self._owner.get_provider()
        if isinstance(provider, str):
            return self._owner.get_provider(provider)
        return provider

    def execution_cache_view(self) -> CacheStore | None:
        return self._owner._persistence_manager.cache_store

    def execution_cache_store(self) -> CacheStore | None:
        return self._owner._persistence_manager.get_cache_store()

    def execution_cache_config(self) -> PersistenceConfig:
        return self._owner._persistence_manager.get_cache_config()


class ScopeBindingHostAdapter:
    """Binding host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    @property
    def _binding_parent(self) -> ScopeProxy | None:
        return self._owner._parent_proxy

    def _binding_snapshot(self) -> ImmutableScope:
        return self._owner._scope

    def _replace_binding_snapshot(self, scope: ImmutableScope) -> None:
        self._owner._scope = scope

    def _has_resumed_binding_layers(self) -> bool:
        return self._owner._resumed_layers is not None

    def _apply_resumed_binding_effects(
        self,
        binding_name: str,
        context: ExecutionContext,
    ) -> ExecutionContext:
        return self._owner._apply_resumed_effects(binding_name, context)

    def _create_context_ref(self, name: str) -> ContextRef[Any]:
        return ContextRef(self._owner, name)


class ScopeEmissionHostAdapter:
    """Emission host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    @property
    def emission_lock(self) -> Any:
        return self._owner._emit_lock

    @property
    def emission_scope_id(self) -> str:
        return self._owner._scope._id

    @property
    def emission_depth(self) -> int:
        return self._owner._depth

    def emission_snapshot(self) -> ImmutableScope:
        return self._owner._scope

    def replace_emission_snapshot(self, scope: ImmutableScope) -> None:
        self._owner._scope = scope

    def persist_emitted_layer(self, layer: EffectLayer) -> None:
        self._owner._persistence_manager.append_layer(layer)

    def propagate_emitted_layer(self, layer: EffectLayer) -> None:
        if self._owner._parent_proxy is not None:
            self._owner._parent_proxy._receive_layer(layer)


class ScopeHierarchyHostAdapter:
    """Hierarchy host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    def hierarchy_snapshot(self) -> ImmutableScope:
        return self._owner._scope

    def replace_hierarchy_snapshot(self, scope: ImmutableScope) -> None:
        self._owner._scope = scope

    def create_hierarchy_scope(
        self,
        scope: ImmutableScope,
        *,
        root: bool = False,
        provider_state: ProviderState | None = None,
    ) -> ScopeProxy:
        return type(self._owner)(_scope=scope, _provider_state=provider_state, root=root)

    def hierarchy_provider_state(self) -> ProviderState:
        return self._owner._provider_state

    def replace_hierarchy_provider_state(self, state: ProviderState) -> None:
        self._owner._provider_state = state

    def effective_hierarchy_provider_state(self) -> ProviderState:
        return self._owner._provider_registry.effective_snapshot()  # type: ignore[attr-defined, no-any-return]

    @property
    def hierarchy_parent(self) -> ScopeProxy | None:
        return self._owner._parent_proxy

    @hierarchy_parent.setter
    def hierarchy_parent(self, value: ScopeProxy | None) -> None:
        self._owner._parent_proxy = value

    @property
    def hierarchy_depth(self) -> int:
        return self._owner._depth

    @hierarchy_depth.setter
    def hierarchy_depth(self, value: int) -> None:
        self._owner._depth = value

    def set_hierarchy_sandbox_parent(self, parent: ScopeProxy) -> None:
        self._owner._sandbox_tracker.set_parent_tracker(parent._sandbox_tracker)

    @property
    def hierarchy_has_project_path(self) -> bool:
        return self._owner._persistence_manager.project_path is not None

    @property
    def hierarchy_persistence_requested(self) -> bool:
        return self._owner._persistence_requested

    @property
    def hierarchy_is_global(self) -> bool:
        return self._owner._is_global

    def initialize_requested_root_persistence(self) -> None:
        self._owner._persistence_manager.initialize()

    def finalize_forked_scope(self, forked: ScopeProxy) -> None:
        self._owner._binding_service.copy_lifecycle_state_to(forked._binding_service)  # type: ignore[attr-defined]
        forked._binding_service.reset_lifecycle_for_fork()  # type: ignore[attr-defined]

    @property
    def hierarchy_is_discarded(self) -> bool:
        return self._owner._is_discarded

    @hierarchy_is_discarded.setter
    def hierarchy_is_discarded(self, value: bool) -> None:
        self._owner._is_discarded = value

    @property
    def hierarchy_is_materialized(self) -> bool:
        return self._owner._is_materialized

    def cleanup_registered_sandboxes(self) -> None:
        self._owner._sandbox_tracker.cleanup()

    def emit_merged_effect(self, effect: Effect) -> None:
        self._owner.emit(effect)


class ScopeSessionHostAdapter:
    """Combined session activation and cleanup host."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    @property
    def session_is_root(self) -> bool:
        return self._owner._is_root

    @property
    def session_is_global(self) -> bool:
        return self._owner._is_global

    @property
    def session_parent(self) -> ScopeProxy | None:
        return self._owner._parent_proxy

    def validate_session_auto_nesting_configuration(self) -> None:
        self._owner._hierarchy.validate_auto_nesting_configuration()  # type: ignore[attr-defined]

    def attach_session_to_parent(self, parent: ScopeProxy) -> None:
        self._owner._hierarchy.attach_to_parent(parent)  # type: ignore[attr-defined]

    def initialize_session_root_persistence(self) -> None:
        self._owner._hierarchy.initialize_root_persistence()  # type: ignore[attr-defined]

    @property
    def session_token(self) -> Any:
        return self._owner._token

    @session_token.setter
    def session_token(self, value: Any) -> None:
        self._owner._token = value

    @property
    def session_token_depth(self) -> int:
        return len(self._owner._runtime.token_stack)

    def pop_session_token(self) -> Any | None:
        if not self._owner._runtime.token_stack:
            return None
        return self._owner._runtime.token_stack.pop()

    @property
    def session_exited(self) -> bool:
        return self._owner._exited

    @session_exited.setter
    def session_exited(self, value: bool) -> None:
        self._owner._exited = value

    def iter_session_local_bindings(self) -> list[BindingWithState]:
        return self._owner._binding_service.local_bindings()  # type: ignore[attr-defined, no-any-return]

    def mark_session_binding_cleaned(self, name: str) -> None:
        self._owner._binding_service.mark_lifecycle(name, is_prepared=False)  # type: ignore[attr-defined]

    def close_session_persistence(self) -> None:
        self._owner._persistence_manager.close_stream()


class ScopeInspectionHostAdapter:
    """Inspection host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    def inspection_snapshot(self) -> ImmutableScope:
        return self._owner._scope

    def inspection_resolve_provider_id(self, provider: str) -> str:
        try:
            return self._owner.get_provider(provider).provider_id
        except Exception:  # noqa: BLE001
            return provider


class ScopeCheckpointHostAdapter:
    """Checkpoint host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    def checkpoint_snapshot(self) -> ImmutableScope:
        return self._owner.snapshot()

    def replace_checkpoint_snapshot(self, scope: ImmutableScope) -> None:
        self._owner._scope = scope

    def snapshot(self) -> ImmutableScope:
        return self._owner.snapshot()

    def restore(self, checkpoint: Checkpoint) -> None:
        self._owner.restore(checkpoint)

    @property
    def checkpoint_materialized_index(self) -> int:
        return self._owner._materialized_index


class ScopeMaterializationHostAdapter:
    """Context-based materialization host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    @property
    def materialization_parent(self) -> ScopeProxy | None:
        return self._owner._parent_proxy

    def materialization_snapshot(self) -> ImmutableScope:
        return self._owner._scope

    def replace_materialization_snapshot(self, scope: ImmutableScope) -> None:
        self._owner._scope = scope

    def emit(self, effect: Effect) -> None:
        self._owner.emit(effect)

    def ordered_materialization_bindings(self) -> list[ContextBinding]:
        return self._owner._ordered_by_reversibility()

    def mark_escaped(self) -> None:
        """Record that effects have escaped containment via commit()."""
        self._owner._is_materialized = True
        self._owner._materialized_index = len(self._owner.effects.layers)


class ScopeEffectMaterializationHostAdapter:
    """Effect-based materialization host backed by one ScopeProxy."""

    __slots__ = ("_owner",)

    def __init__(self, owner: ScopeProxy) -> None:
        self._owner = owner

    @property
    def effect_materialization_is_root(self) -> bool:
        return self._owner._parent_proxy is None

    @property
    def effect_materialization_is_discarded(self) -> bool:
        return self._owner._is_discarded

    def effect_materialization_layers(self) -> list[EffectLayer]:
        return self._owner.effects.layers  # type: ignore[return-value]

    @property
    def effect_materialization_watermark(self) -> int:
        return self._owner._materialized_index

    def advance_effect_materialization_watermark(self, up_to: int) -> None:
        self._owner._materialized_index = up_to
        self._owner._is_materialized = True

    def default_effect_materializer_registry(self) -> MaterializerRegistry:
        from shepherd_runtime.effect_materialization import get_materializer_registry_with_builtins

        return get_materializer_registry_with_builtins(scope=self._owner)
