"""Scope: Layer 1 - Resource Container.

Mental Model
------------
Think of Scope as a "workspace session" - it holds your resources (contexts like
workspaces, databases, APIs) and accumulates a record of everything that happens
(the effect stream). ExecutionLifecycle is a "single task" within that session.

    Scope = Your workspace session (long-lived, accumulates state)
    ExecutionLifecycle = A single task execution (short-lived, atomic)

This owner-path API is for lower-level runtime and migration work. First-run
public docs should start with the callable spine; use explicit Scope control
when you need provider registration, context binding, or effect-stream access:

    with Scope() as scope:
        scope.register_provider("default", provider, default=True)
        workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))

        # Simple one-liner execution
        result, outputs = await scope.execute("Fix the bug")

        # workspace is a ContextRef that auto-updates as effects are applied
        print(workspace.pending_patches)  # Always current state

For advanced use cases (sequential multi-step workflows, accessing intermediate
state), use ExecutionLifecycle directly. See the "Advanced Usage" section below.

This module defines:
- ImmutableScope: Frozen dataclass with pure transformation methods (v2 core)
- ScopeProxy: Mutable facade over ImmutableScope (backward-compatible API)
- Scope: Alias for ScopeProxy (runtime owner-path API)
- ContextBinding: Immutable binding record

Three-Layer Model
-----------------
- Layer 1 (Scope): Resource container - this module
- Layer 2 (ExecutionLifecycle): Orchestrates configure/prepare/execute/extract/apply/cleanup
- Layer 3 (Provider): Translates binding to SDK config, executes

Effect-Sourced Architecture
---------------------------
The core invariant is:
    state(t) = fold(apply_effect, effects[0:t], initial_state)

ImmutableScope provides pure transformation methods (with_binding, with_effect,
apply_effect) that return new scope instances. ScopeProxy wraps an ImmutableScope
and provides the familiar imperative API (bind, emit) by updating its internal
reference to the immutable core.

This enables:
- Time-travel debugging (reconstruct any historical state)
- Speculative execution (fork, run, approve/reject)
- Safe parallel execution (immutable state, no races)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, overload

from typing_extensions import Self

from ..session import (
    current_scope as _session_current_scope,
)
from ..session import (
    require_scope as _session_require_scope,
)
from ._binding_registry import BindingWithState, LifecycleState
from ._bootstrap import install_scope_bootstrap
from ._checkpoint import CheckpointManager
from ._effect_materialization import EffectMaterializationManager
from ._materialization import (
    MaterializationManager,
    order_bindings_by_reversibility,
    rollback_completed_materializations,
)
from ._persistence import ScopePersistence
from ._provider_state import ProviderState
from ._resume import resume_scope
from ._sandbox import SandboxTracker
from .runtime import ScopeRuntimeState
from .substrate import ContextBinding, ImmutableScope

if TYPE_CHECKING:
    import types
    from pathlib import Path

    from shepherd_core.context.kernel import ExecutionContext
    from shepherd_core.effects import Effect
    from shepherd_core.foundation.protocols.device import DeviceProtocol
    from shepherd_core.provider import Provider
    from shepherd_core.types import ReversibilityLevel

    from shepherd_runtime.cache import CacheStore
    from shepherd_runtime.checkpoint import Checkpoint
    from shepherd_runtime.effect_materialization import MaterializerRegistry
    from shepherd_runtime.persistence import PersistenceConfig

    from .substrate import ContextRef, MaterializationSummary, Stream, T_Context

logger = logging.getLogger(__name__)


# =============================================================================
# Scope Proxy (Mutable Facade)
# =============================================================================


def current_scope() -> ScopeProxy | None:
    """Get the current active scope, if any."""
    return _session_current_scope()


def require_scope() -> ScopeProxy:
    """Get the current scope, raising if none active."""
    return _session_require_scope()


class ScopeProxy:
    """Mutable facade over immutable Scope.

    Provides the familiar imperative API (bind, emit, register_provider) while
    maintaining an immutable core. All mutations update the internal reference
    to a new ImmutableScope instance.

    This is the public-facing Scope class. The `Scope` name is an alias for
    this class for backward compatibility.

    Scopes nest automatically. When you create a Scope() inside another active
    runtime scope, it becomes a child of that scope. Effects propagate up to
    parent scopes.

    Basic usage:

        with Scope() as scope:
            scope.register_provider("default", provider, default=True)
            workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))
            result, outputs = await scope.execute("Fix the bug")
            # workspace is a ContextRef - always reflects current state
            print(workspace.pending_patches)

    For isolated execution (testing, library code):

        with Scope(root=True) as isolated:
            # Independent scope, no parent, no propagation
            result = MyTask(input="...")

    v2 Feature - Snapshot:

        # Get immutable state for time-travel or analysis
        snapshot = scope.snapshot()
        # snapshot is an ImmutableScope that won't change
    """

    def __init__(
        self,
        *,
        root: bool = False,
        project_path: Path | None = None,
        persistence: bool | None = None,
        _scope: ImmutableScope | None = None,
        _provider_state: ProviderState | None = None,
        _is_global: bool = False,
    ):
        """Initialize a scope proxy.

        Args:
            root: If True, create an independent root scope that doesn't nest
                within the current scope. Use for testing or library isolation.
            project_path: Path to the project directory. Required for persistence.
                If not specified, persistence is disabled.
            persistence: Whether to enable persistence. Defaults to True if
                project_path is provided, False otherwise.
            _scope: Internal - initial immutable scope state.
            _is_global: Internal - True only for the global scope.
        """
        self._runtime = ScopeRuntimeState()
        self._scope = _scope or ImmutableScope()
        self._provider_state = _provider_state or ProviderState()
        install_scope_bootstrap(self)
        self._is_root = root
        self._is_global = _is_global

        # Persistence and caching (root scope only)
        self._persistence_manager = ScopePersistence(project_path)
        self._persistence_requested = persistence if persistence is not None else (project_path is not None)

        # Materialization managers (deferred initialization to avoid circular ref)
        self._materialization_manager: MaterializationManager | None = None
        self._effect_materialization_manager: EffectMaterializationManager | None = None

        # Checkpoint manager (deferred initialization to avoid circular ref)
        self._checkpoint_manager: CheckpointManager | None = None

        # Container sandbox tracking for overlay layering
        # See: PLAN-workspace-patch-layering.md (Change 8)
        # Extracted to SandboxTracker for better separation of concerns
        self._sandbox_tracker = SandboxTracker(
            parent_tracker=None,  # Set by child() for child scopes
            stream_accessor=lambda: self._scope._stream,
            scope_id=self._scope._id,
        )

        # Persistence initialization is deferred until __enter__ when the scope's
        # parent/root identity is known. Nested `with Scope():` blocks become true
        # child scopes and must not initialize their own persistence streams.

    # --- Properties ---

    @property
    def effects(self) -> Stream:
        """The effect stream, bound to this scope for query methods like direct().

        The returned stream has scope context set, enabling:
        - effects.direct() - only effects emitted by this scope
        - effects.by_depth(n) - effects up to n levels deep
        - effects.summarized() - only task boundary effects
        """
        return self._scope._stream.with_scope_context(self._scope._id, self._depth)

    @property
    def id(self) -> str:
        """Unique identifier for this scope."""
        return self._scope._id

    @property
    def project_path(self) -> Path | None:
        """Resolved persistence project path for this scope tree."""
        if self._parent_proxy is not None:
            return self._parent_proxy.project_path
        return self._persistence_manager.project_path

    @property
    def is_closed(self) -> bool:
        """Whether the scope has been closed.

        Implements ContextAccessor protocol for ContextRef.
        Returns True after __exit__ has been called.
        """
        return self._exited

    @property
    def _parent_proxy(self) -> ScopeProxy | None:
        return self._runtime.parent_proxy

    @_parent_proxy.setter
    def _parent_proxy(self, value: ScopeProxy | None) -> None:
        self._runtime.parent_proxy = value

    @property
    def _token(self) -> Any:
        if not self._runtime.token_stack:
            return None
        return self._runtime.token_stack[-1]

    @_token.setter
    def _token(self, value: Any) -> None:
        if value is None:
            self._runtime.token_stack.clear()
        else:
            self._runtime.token_stack.append(value)

    @property
    def _depth(self) -> int:
        return self._runtime.depth

    @_depth.setter
    def _depth(self, value: int) -> None:
        self._runtime.depth = value

    @property
    def _resumed_layers(self) -> list[Any] | None:
        return self._runtime.resumed_layers

    @_resumed_layers.setter
    def _resumed_layers(self, value: list[Any] | None) -> None:
        self._runtime.resumed_layers = value

    @property
    def _exited(self) -> bool:
        return self._runtime.exited

    @_exited.setter
    def _exited(self, value: bool) -> None:
        self._runtime.exited = value

    @property
    def _is_discarded(self) -> bool:
        return self._runtime.is_discarded

    @_is_discarded.setter
    def _is_discarded(self, value: bool) -> None:
        self._runtime.is_discarded = value

    @property
    def _is_materialized(self) -> bool:
        return self._runtime.is_materialized

    @_is_materialized.setter
    def _is_materialized(self, value: bool) -> None:
        self._runtime.is_materialized = value

    @property
    def _materialized_index(self) -> int:
        return self._runtime.materialized_index

    @_materialized_index.setter
    def _materialized_index(self, value: int) -> None:
        self._runtime.materialized_index = value

    @property
    def _device(self) -> DeviceProtocol | None:
        return self._runtime.device

    @_device.setter
    def _device(self, value: DeviceProtocol | None) -> None:
        self._runtime.device = value

    @property
    def _emit_lock(self) -> Any:
        return self._runtime.emit_lock

    @property
    def _provider_state(self) -> ProviderState:
        return self._runtime.provider_state

    @_provider_state.setter
    def _provider_state(self, value: ProviderState) -> None:
        self._runtime.provider_state = value

    @property
    def is_discarded(self) -> bool:
        """Whether this scope has been discarded.

        A discarded scope cannot be merged or used further.
        Part of the foundation primitives layer.
        """
        return self._is_discarded

    @property
    def is_materialized(self) -> bool:
        """Whether this scope's effects have been materialized.

        A materialized scope's effects have escaped containment.
        Part of the foundation primitives layer.
        """
        return self._is_materialized

    @property
    def current_device(self) -> DeviceProtocol | None:
        """Get the device for this scope.

        Resolution order:
        1. Explicit device set via set_device()
        2. Device from context var (innermost Device() context manager)
        3. None (no device, use default in-process execution)

        Returns:
            DeviceProtocol instance or None

        Example:
            with Scope() as scope:
                with Device("container"):
                    print(scope.current_device.name)  # "container"
        """
        return self._execution.current_device  # type: ignore[attr-defined, no-any-return]

    def set_device(self, device: DeviceProtocol) -> None:
        """Set an explicit device for this scope.

        Overrides the device from context var. Use this for scopes that
        need a specific device regardless of the surrounding context.

        Args:
            device: Device instance to use for this scope

        Example:
            from shepherd_runtime.device import get_device

            scope.set_device(get_device("container"))
        """
        self._execution.set_device(device)  # type: ignore[attr-defined]

    # --- Persistence (delegation to ScopePersistence) ---

    @classmethod
    def resume(
        cls,
        project_path: Path,
        stream_id: str | None = None,
        *,
        continues_from: bool = True,
    ) -> ScopeProxy:
        """Resume a scope from persisted effects.

        Loads effects from the persisted stream and reconstructs scope state.
        State derivation is deferred until bind() is called - this handles
        the chicken-and-egg problem where contexts don't exist yet on resume.

        Args:
            project_path: Path to the project directory
            stream_id: Specific stream to resume from (default: latest)
            continues_from: If True, new effects continue from the loaded stream.
                If False, start a fresh stream (loaded effects are read-only).

        Returns:
            A new ScopeProxy with loaded effects and persistence enabled

        Example:
            # Resume from previous session
            scope = Scope.resume(Path.cwd())

            # Re-bind contexts (effects are applied during bind)
            workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))

            # workspace now has state reconstructed from persisted effects
            print(workspace.pending_patches)

            # Continue working - new effects are persisted
            scope.emit(TaskStarted(...))
        """
        return resume_scope(cls, project_path, stream_id, continues_from=continues_from)

    def _apply_resumed_effects(
        self,
        binding_name: str,
        context: ExecutionContext,
    ) -> ExecutionContext:
        """Apply matching effects from resumed stream to a context.

        Delegates to the apply_resumed_effects function from _scope_persistence.
        """
        return self._resume.apply_resumed_effects(binding_name, context)  # type: ignore[attr-defined, no-any-return]

    def clear_resumed_layers(self) -> None:
        """Clear resumed layers to free memory after all bindings are done.

        Call this after all contexts have been re-bound to release
        the reference to the loaded layers.
        """
        self._resume.clear_resumed_layers()  # type: ignore[attr-defined]

    @property
    def resume_layers(self) -> list[Any] | None:
        return self._resumed_layers

    @resume_layers.setter
    def resume_layers(self, value: list[Any] | None) -> None:
        self._resumed_layers = value

    # --- Cache (delegation to ScopePersistence) ---

    @property
    def cache(self) -> CacheStore | None:
        """Access the cache store for manual operations.

        Returns None if caching is not enabled or if this is a child scope.
        Cache is only available on root scopes with project_path set.

        Example:
            scope.cache.invalidate()
            scope.cache.invalidate(task=FindImprovements)
            scope.cache.invalidate(older_than=timedelta(hours=24))
            print(scope.cache.stats())
        """
        return self._execution.cache  # type: ignore[attr-defined, no-any-return]

    def _get_cache_store(self) -> CacheStore | None:
        """Get the cache store, initializing if needed.

        Returns None if caching is disabled or no project_path is set.
        This is called internally during task execution.
        """
        return self._execution.get_cache_store()  # type: ignore[attr-defined, no-any-return]

    def _get_cache_config(self) -> PersistenceConfig:
        """Get the cache configuration.

        Returns the persistence config which contains cache settings.
        This is called internally during task execution.
        """
        return self._execution.get_cache_config()  # type: ignore[attr-defined, no-any-return]

    # --- Effect Emission ---

    def emit(self, effect: Effect) -> None:
        """Emit an effect to the stream.

        This method:
        1. Creates an EffectLayer with scope metadata (scope_id, scope_depth)
        2. Appends the layer to the stream
        3. Derives new state from the effect (apply_effect)
        4. Persists layer to disk (if persistence enabled)
        5. Propagates layer to parent scope if present

        The scope metadata enables:
        - stream.direct() - filter to only this scope's effects
        - stream.by_depth(n) - filter by hierarchy depth

        Thread-safety:
            This method is protected by a lock to ensure thread-safe emission
            when multiple threads emit effects concurrently. The lock protects
            the read-modify-write sequence (sequence number assignment and
            scope state updates). Persistence and parent propagation occur
            outside the lock.
        """
        self._emission_engine.emit(effect)  # type: ignore[attr-defined]

    def _receive_layer(self, layer: Any) -> None:
        """Receive layer from child scope (record AND derive state).

        The layer's scope_id and scope_depth are preserved - they track
        the original emitting scope, not the receiving scope.

        State derivation is performed so that parent bindings receive
        effects emitted by child scopes (e.g., workspace effects from
        container execution propagate to parent workspace binding).

        If this is the root scope with persistence enabled, the layer
        is persisted to disk.

        Thread-safety:
            Protected by _emit_lock to ensure thread-safe propagation
            when child scopes emit concurrently.
        """
        self._emission_engine.receive_layer(layer)  # type: ignore[attr-defined]

    def emit_all(self, effects: tuple[Effect, ...] | list[Effect]) -> None:
        """Emit multiple effects."""
        self._emission_engine.emit_all(effects)  # type: ignore[attr-defined]

    # --- Provider Registry (delegation to ProviderRegistry) ---

    def register_provider(
        self,
        name: str,
        provider: Provider,
        *,
        default: bool = False,
    ) -> None:
        """Register a provider by name.

        Args:
            name: Name/role for the provider (e.g., "analyst", "fetcher")
            provider: The provider instance
            default: If True, set as default provider
        """
        self._provider_registry.register(name, provider, default=default)  # type: ignore[attr-defined]

    def get_provider(self, name: str | None = None) -> Provider:
        """Get a provider by name, or the default provider.

        Provider lookup follows inheritance: if a provider isn't found locally,
        the parent scope is checked.
        """
        return self._provider_registry.get(name)  # type: ignore[attr-defined, no-any-return]

    def has_provider(self, name: str) -> bool:
        """Check if a provider is registered."""
        return self._provider_registry.has(name)  # type: ignore[attr-defined, no-any-return]

    # --- Context Binding ---

    @overload
    def bind(self, context: T_Context) -> ContextRef[T_Context]: ...

    @overload
    def bind(self, name: str, context: T_Context) -> ContextRef[T_Context]: ...

    @overload
    def bind(self, target_type: type[T_Context], context: T_Context) -> ContextRef[T_Context]: ...

    def bind(  # type: ignore[misc]
        self,
        name_or_context: Any,
        context: Any = None,
    ) -> ContextRef[Any]:
        """Bind a context to this scope and return a live reference.

        Three accepted forms (DECISIONS D2 / CONTRACTS C5):

        - ``scope.bind(value)`` — bare; reads ``value.__binding_name__``.
        - ``scope.bind("name", value)`` — name-keyed (deletion target
          per D5; retained until extras migration completes).
        - ``scope.bind(T, value)`` — type-keyed; value must be a ``T``.
          ``current_binding(T)`` resolves these by matching context
          type, not the registry name.
        """
        return self._binding_service.bind(name_or_context, context)  # type: ignore[attr-defined, no-any-return]

    def get_binding(self, name: str) -> BindingWithState:
        """Get a context binding by name."""
        return self._binding_service.get_binding(name)  # type: ignore[attr-defined, no-any-return]

    def get_context(self, name: str) -> ExecutionContext:
        """Get the current context for a binding."""
        return self._binding_service.get_context(name)  # type: ignore[attr-defined, no-any-return]

    def update_context(self, name: str, new_context: ExecutionContext) -> None:
        """Update a context after capture."""
        self._binding_service.update_context(name, new_context)  # type: ignore[attr-defined]

    def all_bindings(self) -> list[BindingWithState]:
        """Get all context bindings (including inherited from parent)."""
        return self._binding_service.all_bindings()  # type: ignore[attr-defined, no-any-return]

    def mark_binding_lifecycle(
        self,
        name: str,
        *,
        is_prepared: bool | None = None,
        in_lifecycle: bool | None = None,
    ) -> None:
        """Update binding lifecycle state."""
        self._binding_service.mark_lifecycle(name, is_prepared=is_prepared, in_lifecycle=in_lifecycle)  # type: ignore[attr-defined]

    # --- Reversibility ---

    def composite_reversibility(self) -> ReversibilityLevel:
        """Compute composite reversibility from all bound contexts."""
        from shepherd_core.context.kernel import compute_composite_reversibility

        contexts = [b.context for b in self._scope._bindings]
        return compute_composite_reversibility(contexts)

    # --- Child Scopes ---

    def child(self) -> ScopeProxy:
        """Create a child scope that inherits from this scope.

        Child scopes:
        - Inherit provider registry (read-only lookup)
        - Inherit context bindings (read-only lookup)
        - Have their own effect stream (effects propagate to parent)
        - Have their own context bindings (can shadow parent)
        - Are one level deeper in the scope hierarchy

        Used by @task execution to create task-owned scopes.
        """
        return self._hierarchy.child()  # type: ignore[attr-defined, no-any-return]

    def _attach_to_parent(self, parent: ScopeProxy) -> None:
        """Attach this scope to a live parent proxy using child-scope semantics."""
        self._hierarchy.attach_to_parent(parent)  # type: ignore[attr-defined]

    def _validate_auto_nesting_configuration(self) -> None:
        """Reject root-only configuration on implicitly nested scopes."""
        self._hierarchy.validate_auto_nesting_configuration()  # type: ignore[attr-defined]

    def _initialize_root_persistence(self) -> None:
        """Initialize stream persistence once the scope is known to be root-like."""
        self._hierarchy.initialize_root_persistence()  # type: ignore[attr-defined]

    # --- Speculative Execution (v2) ---

    def fork(self) -> ScopeProxy:
        """Create an independent fork for speculative execution.

        Unlike child(), fork() creates a completely independent scope:
        - No parent link - effects do NOT propagate
        - Copies current bindings (snapshot at fork time)
        - Copies provider registry
        - Independent effect stream

        Use fork() for speculative execution with LLM-as-judge:

            with scope.fork() as speculative:
                result = await speculative.execute("Make changes")

                # Evaluate with LLM-as-judge
                if judge_approves(result):
                    scope.merge_effects(speculative.effects)
                # Otherwise, fork is discarded - no side effects
        """
        return self._hierarchy.fork()  # type: ignore[attr-defined, no-any-return]

    # --- Container Sandbox Tracking (delegation to SandboxTracker) ---

    def register_sandbox(self, sandbox: Any) -> None:
        """Register a container sandbox for parent tracking.

        Called by ContainerDevice after creating a sandbox. Enables subsequent
        tasks to find parent sandboxes for overlay layering.

        Args:
            sandbox: ContainerSandbox instance with sandbox_id attribute.

        See Also:
            PLAN-workspace-patch-layering.md (Change 8)
        """
        self._sandbox_tracker.register(sandbox)

    def get_sandbox(self, sandbox_id: str) -> Any | None:
        """Get a sandbox by ID, searching up the scope hierarchy.

        Args:
            sandbox_id: The sandbox ID to look up.

        Returns:
            ContainerSandbox if found, None otherwise.
        """
        return self._sandbox_tracker.get(sandbox_id)

    def get_latest_sandbox_for_context(self, context_name: str) -> Any | None:
        """Get the most recent sandbox for a given context name.

        Searches the effect stream for ContainerExecutionCompleted effects
        and returns the sandbox associated with the most recent one matching
        the context name.

        Args:
            context_name: The context binding name (e.g., "workspace").

        Returns:
            ContainerSandbox if found, None otherwise.

        See Also:
            PLAN-workspace-patch-layering.md (Change 8)
        """
        return self._sandbox_tracker.get_latest_for_context(context_name)

    def _cleanup_sandboxes(self) -> None:
        """Clean up all registered sandboxes.

        Called during discard() to unmount overlays and delete temp directories.
        Errors are logged but don't prevent cleanup of other sandboxes.
        """
        self._hierarchy.cleanup_sandboxes()  # type: ignore[attr-defined]

    def merge_effects(self, stream: Stream) -> None:
        """Merge effects from a forked scope into this scope.

        .. deprecated::
            Use :meth:`merge` instead, which provides proper guard checks
            and aligns with the foundation primitives API.

        Call this after speculative execution succeeds to integrate
        the effects into the main scope.
        """
        self._hierarchy.merge_effects(stream)  # type: ignore[attr-defined]

    # --- Foundation Primitives ---

    def merge(self, child: ScopeProxy) -> None:
        """Propagate child's effects to this scope.

        This is the foundation primitive for merging forked scope effects.

        After merge:
        - Child's effects appear in this scope's stream
        - Child scope should not be used further
        - State is recomputed via the fold invariant

        Args:
            child: A scope previously created by fork()

        Raises:
            ScopeError: If child was already discarded

        Example:
            child = scope.fork()
            await task(scope=child)
            scope.merge(child)  # child.effects now in scope.effects

        See Also:
            design/syntax-api/DESIGN-primitives-layer.md
        """
        self._hierarchy.merge(child)  # type: ignore[attr-defined]

    def discard(self) -> None:
        """Abandon this scope's effects and clean up container sandboxes.

        After discard:
        - This scope's effects are lost
        - This scope cannot be merged
        - Parent scope is unchanged (fold invariant)
        - Container overlays are unmounted and cleaned up

        Safe to call:
        - On any forked scope
        - Multiple times (idempotent)

        NOT safe after:
        - materialize() has been called (effects already escaped)

        Raises:
            ContainmentError: If effects have already escaped via materialize()

        Example:
            child = scope.fork()
            await task(scope=child)

            if not approved:
                child.discard()  # Effects vanish - no trace remains

        See Also:
            PLAN-workspace-patch-layering.md (Change 8) - sandbox cleanup on discard
        """
        self._hierarchy.discard()  # type: ignore[attr-defined]

    # --- Checkpoint/Restore ---

    def checkpoint(self, name: str) -> Checkpoint:
        """Create a named checkpoint for potential rollback.

        Records the current stream position and binding count. Calling
        restore(checkpoint) will truncate the stream back to this position,
        remove any bindings added after the checkpoint, and recompute
        context state by replaying the remaining effects.

        Args:
            name: Human-readable name for debugging

        Returns:
            Checkpoint that can be passed to restore()

        Example - Error recovery:
            cp = scope.checkpoint("before_migration")
            try:
                await risky_migration(scope)
            except MigrationError:
                scope.restore(cp)  # Undo all migration effects

        Example - Conditional rollback:
            cp = scope.checkpoint("attempt_1")
            result = await generate_solution(scope)

            if not result.is_valid:
                scope.restore(cp)
                result = await generate_solution_v2(scope)

        Example - Preview before deciding:
            cp = scope.checkpoint("preview")
            await some_work(scope)

            # Inspect what would be discarded
            print(f"Effects since: {len(cp.effects_since)}")

            if approved:
                pass  # Keep effects
            else:
                scope.restore(cp)  # Discard them
        """
        return self._get_checkpoint_manager().create(name)

    def restore(
        self,
        checkpoint: Checkpoint,
        *,
        keep_bindings: list[str] | None = None,
        exclude_effect_types: list[str] | None = None,
        strict: bool = False,
    ) -> None:
        """Restore to checkpoint, optionally preserving specific bindings or effects.

        Truncates the stream back to the checkpoint position, removes any
        bindings added after the checkpoint (except those in keep_bindings),
        and recomputes context states by replaying effects (except those
        matching exclude_effect_types).

        Args:
            checkpoint: A checkpoint created by this scope
            keep_bindings: List of binding names to preserve even if they were
                added after the checkpoint. Their current state is kept intact.
            exclude_effect_types: List of effect type names to skip during replay.
                Effects of these types won't be applied when recomputing state.
                Use effect.effect_type values (e.g., "tool_call_started").
            strict: If True, fail on any validation inconsistency (including
                fingerprint mismatch, binding count decrease). If False (default),
                only critical issues cause failure and warnings are logged.

        Raises:
            ValueError: If checkpoint belongs to different scope
            ValueError: If checkpoint was already restored
            ValueError: If checkpoint is stale (invalidated by previous restore)
            ValueError: If keep_bindings contains a binding that doesn't exist
            CheckpointValidationError: If strict=True and validation fails
            ContainmentError: If effects after checkpoint were materialized

        Example - Full restore:
            cp = scope.checkpoint("before_risky_operation")

            try:
                await risky_operation(scope)
            except OperationFailed:
                scope.restore(cp)  # Roll back all changes

        Example - Preserve workspace binding:
            cp = scope.checkpoint("before_experiment")
            scope.bind("temp_context", TempContext())
            await experiment(scope)

            # Restore but keep workspace state
            scope.restore(cp, keep_bindings=["workspace"])

        Example - Skip certain effect types during replay:
            cp = scope.checkpoint("before_work")
            await do_work(scope)

            # Restore but don't replay tool call effects
            scope.restore(cp, exclude_effect_types=["tool_call_started", "tool_call_completed"])

        Example with strict validation:
            cp = scope.checkpoint("critical_point")
            # ... some operations ...
            scope.restore(cp, strict=True)  # Fail on any inconsistency
        """
        self._get_checkpoint_manager().restore(
            checkpoint,
            keep_bindings=keep_bindings,
            exclude_effect_types=exclude_effect_types,
            strict=strict,
        )

    def materialize(
        self,
        registry: MaterializerRegistry | None = None,
    ) -> MaterializationSummary:
        """Apply pending effects to the real world (escape containment).

        This is the EFFECT-BASED materialization method. Effects are dispatched
        to registered materializers based on effect type. For CONTEXT-BASED
        materialization (git commits via WorkspaceRef), use commit() instead.

        After materialize:
        - Effects have ESCAPED containment
        - Registered materializers have applied their changes
        - Cannot be undone via discard()

        Args:
            registry: MaterializerRegistry for dispatch. If None, uses
                get_materializer_registry_with_builtins(scope=self).

        Returns:
            MaterializationSummary with counts of processed/materialized effects

        Raises:
            RuntimeError: If called from non-root scope
            ContainmentError: If scope has been discarded

        Example:
            with Scope() as scope:
                scope.emit(WorkspacePatchCaptured(...))

                # Apply effects to real world
                summary = scope.materialize()
                if summary:
                    print(f"Materialized {summary.effects_materialized} effects")
        """
        return self._get_effect_materialization_manager().materialize(registry)

    # --- Materialization (delegation to MaterializationManager) ---

    def _get_materialization_manager(self) -> MaterializationManager:
        """Get or create the materialization manager.

        Lazy initialization to avoid circular reference during __init__.
        """
        if self._materialization_manager is None:
            self._materialization_manager = MaterializationManager(self._materialization_host)  # type: ignore[attr-defined]
        return self._materialization_manager

    def _get_effect_materialization_manager(self) -> EffectMaterializationManager:
        """Get or create the effect materialization manager.

        Lazy initialization to avoid circular reference during __init__.
        """
        if self._effect_materialization_manager is None:
            self._effect_materialization_manager = EffectMaterializationManager(self._effect_materialization_host)  # type: ignore[attr-defined]
        return self._effect_materialization_manager

    def _get_checkpoint_manager(self) -> CheckpointManager:
        """Get or create the checkpoint manager.

        Lazy initialization to avoid circular reference during __init__.
        """
        if self._checkpoint_manager is None:
            self._checkpoint_manager = CheckpointManager(self._checkpoint_host)  # type: ignore[attr-defined]
        return self._checkpoint_manager

    def _ordered_by_reversibility(self) -> list[ContextBinding]:
        """Order bindings by reversibility level for safe materialization.

        Returns contexts ordered: AUTO first, then COMPENSABLE, then NONE.
        Only includes Materializable contexts with pending changes.
        """
        return order_bindings_by_reversibility(self._scope._bindings)

    def _rollback_completed(
        self,
        completed: list[tuple[ContextBinding, Any, Any]],
    ) -> None:
        """Rollback completed materializations in reverse order.

        Delegates to the module-level function.
        """
        rollback_completed_materializations(completed)

    def commit(self, message: str | None = None) -> dict[str, Any]:
        """Materialize all contexts with pending changes to the real filesystem.

        This is the ONLY place where materialization I/O happens.
        Must be called at root scope (no parent).

        Contexts are materialized in reversibility order (AUTO first, then
        COMPENSABLE, then NONE) so that if a non-reversible context fails,
        reversible contexts can be rolled back.

        Args:
            message: Optional commit message (passed to contexts that support it,
                    e.g., WorkspaceRef will create a git commit with this message)

        Returns:
            Dict with summary of what was materialized:
            {
                "contexts": [{"name": "workspace", "paths_affected": [...], ...}],
                "total_paths_affected": 5,
            }

        Raises:
            RuntimeError: If called from non-root scope
            RuntimeError: If any materialization fails (after rolling back completed)

        Example:
            with Scope() as scope:
                workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))
                await scope.execute("Fix the bug")

                # Accumulated changes in workspace.pending_patches
                # Now materialize to real filesystem
                result = scope.commit(message="Fix auth bug")
                print(f"Changed {result['total_paths_affected']} files")
        """
        return self._get_materialization_manager().commit(message)

    def commit_remaining(self, message: str | None = None) -> dict[str, Any]:
        """Continue an interrupted commit, skipping already-materialized contexts.

        Use after resume() when a previous commit() may have been interrupted.
        Checks the effect stream for ContextMaterialized effects and skips
        contexts that were already successfully materialized.

        This is idempotent — safe to call even if the previous commit completed
        successfully (it will simply skip all contexts).

        Args:
            message: Optional commit message (same as commit())

        Returns:
            Dict with summary of what was materialized (same format as commit())

        Example:
            # Session interrupted during commit...

            # Later, resume and complete
            with Scope.resume(project_path) as scope:
                workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))

                # Continue interrupted commit (safe even if commit completed)
                result = scope.commit_remaining(message="Complete interrupted commit")
        """
        return self._get_materialization_manager().commit_remaining(message)

    def preview_commit(self) -> dict[str, Any]:
        """Preview what commit() would do without executing.

        Returns a dictionary mapping binding names to their materialization
        intents. Useful for inspecting pending changes before committing.

        Returns:
            Dict mapping binding name to intent info:
            {
                "workspace": {
                    "context_type": "WorkspaceRef",
                    "intent": <MaterializationIntent>,
                    "has_pending_changes": True,
                },
                ...
            }

        Example:
            with Scope() as scope:
                workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))
                await scope.execute("Make some changes")

                # Preview before committing
                preview = scope.preview_commit()
                for name, info in preview.items():
                    print(f"{name}: {len(info['intent'].patches)} patches pending")

                # Now commit if happy
                scope.commit()
        """
        return self._get_materialization_manager().preview()

    # --- Snapshot (v2 feature) ---

    def snapshot(self) -> ImmutableScope:
        """Get current immutable scope state.

        Returns a frozen snapshot of the current scope that won't change.
        Useful for time-travel debugging or safe concurrent access.
        """
        return self._scope

    # --- Context Manager ---

    def __enter__(self) -> Self:
        """Enter scope context, setting as current scope.

        Unless this is a root scope (root=True) or the global scope,
        automatically becomes a child of the current scope if one exists.
        """
        return self._session.enter()  # type: ignore[attr-defined, no-any-return]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit scope context, cleaning up dangling contexts.

        Only cleans up contexts that are:
        - prepared (is_prepared=True)
        - NOT managed by an active ExecutionLifecycle (in_lifecycle=False)
        """
        self._session.exit(exc_type, exc_val, exc_tb)  # type: ignore[attr-defined]

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        return await self._session.aenter()  # type: ignore[attr-defined, no-any-return]

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self._session.aexit(exc_type, exc_val, exc_tb)  # type: ignore[attr-defined]

    def __repr__(self) -> str:
        return f"Scope({self._scope._id}, {len(self._scope._bindings)} bindings, {len(self._scope._stream)} effects)"

    # --- Convenience Methods ---

    async def execute(
        self,
        prompt: str,
        provider: Provider | str | None = None,
        task_name: str | None = None,
        auto_update_bindings: bool = True,
    ) -> tuple[Any, dict[str, ExecutionContext]]:
        """Execute a prompt with the default or specified provider.

        This is a convenience method that creates an ExecutionLifecycle internally,
        handling the full lifecycle in a single call.

        Args:
            prompt: The prompt to send to the LLM
            provider: Provider instance, provider name, or None for default
            task_name: Optional task name for effect attribution
            auto_update_bindings: If True, update scope bindings after capture

        Returns:
            Tuple of (ExecutionResult, dict of updated contexts by name)
        """
        return await self._execution.execute(  # type: ignore[attr-defined, no-any-return]
            prompt,
            provider=provider,
            task_name=task_name,
            auto_update_bindings=auto_update_bindings,
        )

    def execute_sync(
        self,
        prompt: str,
        provider: Provider | str | None = None,
        task_name: str | None = None,
    ) -> tuple[Any, dict[str, ExecutionContext]]:
        """Synchronous version of execute() for non-async contexts."""
        return self._execution.execute_sync(prompt, provider=provider, task_name=task_name)  # type: ignore[attr-defined, no-any-return]

    # --- Message History ---

    def get_messages(
        self,
        task_name: str | None = None,
        provider: str | None = None,
    ) -> list[dict[str, str]]:
        """Get conversation messages from this scope's effect stream.

        Extracts messages from PromptSent, AgentThinking, and AgentMessage
        effects, returning them as a list of dicts with role/content.
        """
        return self._inspection.get_messages(task_name=task_name, provider=provider)  # type: ignore[attr-defined, no-any-return]

    # --- Introspection (D45) ---

    def effect_counts(self) -> dict[str, int]:
        """Count effects by type name.

        Useful for quick inspection of what happened during execution.

        Returns:
            Mapping from effect type name to count.

        Example:
            >>> scope.effect_counts()
            {'TaskStarted': 1, 'ToolCallCompleted': 5, 'FilePatch': 3, 'TaskCompleted': 1}
        """
        return self._inspection.effect_counts()  # type: ignore[attr-defined, no-any-return]

    def effects_by_binding(self) -> dict[str, list[Effect]]:
        """Group effects by their binding_name attribute.

        Effects without a binding_name are grouped under the empty string key.
        Useful for debugging effect routing.

        Returns:
            Mapping from binding name to list of effects.

        Example:
            >>> scope.effects_by_binding()
            {'': [TaskStarted(...), TaskCompleted(...)],
             'workspace': [FilePatch(...), FilePatch(...)]}
        """
        return self._inspection.effects_by_binding()  # type: ignore[attr-defined, no-any-return]

    def summary(self) -> str:
        """Quick overview of the scope state.

        Provides a formatted summary of effect counts and bindings,
        useful for debugging and inspection.

        Returns:
            Formatted string summarizing effect counts and bindings.

        Example:
            >>> print(scope.summary())
            Scope Summary
            ========================================
            Total effects: 10

            By type:
              ToolCallCompleted: 5
              FilePatch: 3
              TaskStarted: 1
              TaskCompleted: 1

            By binding:
              (lifecycle): 2
              workspace: 8
        """
        return self._inspection.summary()  # type: ignore[attr-defined, no-any-return]


# =============================================================================
# Type Alias for Backward Compatibility
# =============================================================================

# Scope is an alias for ScopeProxy - this maintains full backward compatibility
Scope = ScopeProxy


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "BindingWithState",
    "ContextBinding",
    "ImmutableScope",
    "LifecycleState",
    # Public API (backward compatible)
    "Scope",
    # v2 additions
    "ScopeProxy",
    "current_scope",
    "require_scope",
]
