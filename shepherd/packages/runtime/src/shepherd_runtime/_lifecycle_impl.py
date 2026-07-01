"""Runtime-owned lifecycle implementation.

Simple Usage
------------
For most cases, use Scope.execute() which handles everything internally:

    with Scope() as scope:
        scope.register_provider("default", provider, default=True)
        workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))

        result, outputs = await scope.execute("Fix the bug")
        # workspace is a ContextRef - auto-updates as effects are applied
        print(workspace.pending_patches)

Advanced Usage
--------------
For sequential multi-step workflows or when you need access to intermediate
state (e.g., composed_binding), use ExecutionLifecycle directly:

    with Scope() as scope:
        scope.register_provider("default", provider, default=True)
        workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))

        async with ExecutionLifecycle(scope, provider, task_name="step_1") as lc:
            print(f"Composed binding: {lc.composed_binding}")
            await lc.execute("Create file")

        async with ExecutionLifecycle(scope, provider, task_name="step_2") as lc:
            await lc.execute("Modify file")  # Sees step_1's changes

        # workspace ContextRef reflects all changes from both steps
        print(workspace.pending_patches)

This module defines:
- ExecutionLifecycle (Layer 2): Orchestrates the 7-phase lifecycle
- execute(): Convenience function for single-shot execution

Three-Layer Model
-----------------
- Layer 1 (Scope): Resource container - owns bindings, providers, stream
- Layer 2 (ExecutionLifecycle): Orchestrates configure/prepare/execute/artifact/extract/apply/cleanup
- Layer 3 (Provider): Translates binding to SDK config, executes

Mental Model:
    Scope = Your workspace session (long-lived, accumulates state)
    ExecutionLifecycle = A single task execution (short-lived, atomic)
    Provider = SDK adapter (translates abstract config to SDK-specific calls)

ExecutionLifecycle Responsibilities:
1. Call configure() on contexts, compose bindings
2. Call prepare() on contexts (with rollback on failure)
3. Delegate to Provider for execution
4. Call extract_effects() on contexts, emit effects
5. Call apply_effect() on contexts to derive new state
6. Call cleanup() on contexts (always)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import uuid4

from shepherd_core.effects import (
    TaskCompleted,
    TaskFailed,
    TaskStarted,
)
from shepherd_core.errors import BindingNotFoundError, TaskExecutionError
from shepherd_core.text import smart_truncate
from typing_extensions import Self

from shepherd_runtime._lifecycle import (
    ApplyPhase,
    ArtifactPhase,
    Attribution,
    CleanupError,
    CleanupPhase,
    ConfigurePhase,
    EffectEmitter,
    ExecutePhase,
    ExtractPhase,
    LifecyclePipeline,
    Phase,
    PhaseBase,
    PhaseContext,
    PreparePhase,
)

from ._phase_cache import CacheCheckPhase, CacheStorePhase
from .sandbox_registry import (
    get_default_registry,
    register_sandbox_factory,
)

if TYPE_CHECKING:
    import types
    from collections.abc import Callable

    from shepherd_core.context.kernel import ExecutionContext
    from shepherd_core.foundation.protocols.device import (
        DeviceProtocol,
        EffectBundle,
        ExecutionSpec,
    )
    from shepherd_core.provider import Provider
    from shepherd_core.types import (
        ExecutionResult,
        ProviderBinding,
    )

    from shepherd_runtime._scope._binding_registry import BindingWithState
    from shepherd_runtime.scope import Scope
    from shepherd_runtime.task.authoring import ArtifactMarker
    from shepherd_runtime.task.output import TaskRefReconstructionPolicy

logger = logging.getLogger(__name__)


# Backward compatibility: expose _SANDBOX_FACTORIES for existing tests
# This is a property that delegates to the default registry's internal dict
# NOTE: Prefer using register_sandbox_factory() for new code
_SANDBOX_FACTORIES = get_default_registry()._factories


# =============================================================================
# Error Location Extraction
# =============================================================================


def _extract_error_location(e: Exception) -> str | None:
    """Extract condensed location from exception traceback.

    Returns a string like "provider.py:830 in execute_sdk" or None if
    no traceback is available.
    """
    tb = e.__traceback__
    if tb is None:
        return None

    # Walk to the innermost frame
    while tb.tb_next is not None:
        tb = tb.tb_next

    frame = tb.tb_frame
    filename = Path(frame.f_code.co_filename).name
    lineno = tb.tb_lineno
    funcname = frame.f_code.co_name

    return f"{filename}:{lineno} in {funcname}"


# =============================================================================
# Layer 2: ExecutionLifecycle
# =============================================================================


@dataclass
class ExecutionLifecycle:
    """Layer 2: Orchestrates the 7-phase execution lifecycle.

    ExecutionLifecycle is a context manager that handles:
    1. Configure: Call configure() on all contexts, compose bindings
    2. Prepare: Call prepare() on contexts (with rollback on failure)
    3. Execute: Delegate to provider (via execute() method)
    4. Artifact: Collect artifact files written by the agent
    5. Extract: Call extract_effects() on contexts (v2 API)
    6. Apply: Call apply_effect() on contexts to derive new state (v2 API)
    7. Cleanup: Call cleanup() on contexts (always, via __exit__)

    v2 API (extract_effects + apply_effect):
        Separates effect extraction from state derivation, enabling:
        - Time-travel debugging (reconstruct state from effects)
        - Speculative execution (fork, run, approve/reject)
        - Sandbox isolation (changes captured as effects)

    v1 API (capture) is still supported for backward compatibility.

    The lifecycle ensures:
    - Preparation failures trigger rollback of prepared contexts
    - Cleanup always runs, even on exception
    - Context outputs are available after execution

    Usage:
        async with ExecutionLifecycle(scope, provider) as lifecycle:
            result = await lifecycle.execute("Analyze the data")
            updated_workspace = lifecycle.get_context("workspace")

    Attributes:
        scope: The scope containing bindings and stream
        provider: The provider to execute with
        task_name: Optional task name for effect attribution
        auto_update_bindings: If True, update scope bindings after capture
    """

    scope: Scope
    provider: Provider | None = None
    task_name: str | None = None
    auto_update_bindings: bool = True
    artifact_markers: dict[str, ArtifactMarker] = field(default_factory=dict)
    output_format: dict[str, Any] | None = None  # JSON schema for structured output
    executor: Any | None = None  # Callable for programmatic tasks (typed as Any to avoid Callable import)
    kernel_v3_canary_spec: Any | None = None
    kernel_v3_canary_target: Any | None = None
    attribution: Attribution | None = None  # Effect attribution; auto-constructed if not provided

    # Cache support (optional, only set for @task executions)
    task_meta: Any = None  # TaskMetadata, typed as Any to avoid circular import
    task_inputs: dict[str, Any] = field(default_factory=dict)
    taskref_policy: TaskRefReconstructionPolicy | None = None

    # Stage attribution (set when task is executed via run_stage)
    stage_name: str | None = None

    # Internal state (set during lifecycle)
    _bindings: list[BindingWithState] = field(default_factory=list, repr=False)
    _pipeline: LifecyclePipeline | None = field(default=None, repr=False)
    _emitter: EffectEmitter | None = field(default=None, repr=False)
    _entered: bool = field(default=False, repr=False)
    _executed: bool = field(default=False, repr=False)
    _device_task_outputs: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Initialize task name and attribution if not provided."""
        if self.task_name is None:
            self.task_name = f"task_{uuid4().hex[:8]}"
        if self.attribution is None:
            self.attribution = Attribution(
                task_name=self.task_name or "",
                provider_id=self.provider.provider_id if self.provider else None,
                source="llm" if self.provider else "programmatic",
            )

    # --- Effect Emission ---

    def _emit(self, effect: Any) -> None:
        """Emit an effect via the emitter (scope + formatter routing).

        Delegates to EffectEmitter which handles:
        - Emitting to scope for stream recording
        - Routing to formatter for verbose output (with exception handling)
        """
        if self._emitter is not None:
            self._emitter.emit(effect)
        else:
            # Fallback for edge cases (before __aenter__ or during testing)
            self.scope.emit(effect)

    # --- Context Manager Protocol ---

    async def __aenter__(self) -> Self:
        """Enter lifecycle: configure and prepare contexts.

        Creates the pipeline and runs configure + prepare phases.
        After prepare, wires sandbox paths to composed_binding.
        """
        if self._entered:
            raise RuntimeError("ExecutionLifecycle already entered")
        self._entered = True

        # Create emitter for effect routing (scope + formatter)
        formatter = self.provider.formatter if self.provider else None
        self._emitter = EffectEmitter(self.scope, formatter)

        # Get all bindings from scope
        self._bindings = list(self.scope.all_bindings())

        # Mark bindings as in-lifecycle
        for binding in self._bindings:
            self.scope.mark_binding_lifecycle(binding.name, in_lifecycle=True)

        # Create pipeline with all phases (9-phase pipeline with cache)
        registry = get_default_registry()
        self._pipeline = LifecyclePipeline(
            phases=[
                ConfigurePhase(),
                PreparePhase(registry, self._emitter),
                CacheCheckPhase(),  # Phase 3: Check cache before execution
                ExecutePhase(),  # Phase 4: Skipped on cache hit
                ArtifactPhase(self._emitter),  # Phase 5: Skipped on cache hit
                ExtractPhase(self._emitter),  # Phase 6: Skipped on cache hit
                ApplyPhase(self._emitter, self.auto_update_bindings),  # Phase 7: Skipped on cache hit
                CacheStorePhase(),  # Phase 8: Store results (no-op on cache hit)
                CleanupPhase(self._emitter),  # Phase 9: Always runs
            ],
            emitter=self._emitter,
        )

        # Create initial PhaseContext
        initial_ctx = PhaseContext(
            scope=self.scope,
            provider=self.provider,
            task_name=self.task_name,  # type: ignore[arg-type]
            attribution=self.attribution,
            executor=self.executor,
            bindings=tuple(self._bindings),
            artifact_markers=self.artifact_markers,
            output_format=self.output_format,
            task_meta=self.task_meta,
            task_inputs=self.task_inputs,
            taskref_policy=self.taskref_policy,
            kernel_v3_canary_spec=self.kernel_v3_canary_spec,
            kernel_v3_canary_target=self.kernel_v3_canary_target,
        )

        try:
            # Run configure and prepare phases
            ctx = await self._pipeline.run_until(initial_ctx, stop_after="prepare")

            # Wire sandbox paths to composed_binding
            ctx = ctx.with_sandbox_wired_binding()
            self._pipeline.update_context(ctx)

        except Exception:
            # Unmark lifecycle on failure (rollback already handled by pipeline)
            for binding in self._bindings:
                self.scope.mark_binding_lifecycle(binding.name, in_lifecycle=False)
            raise

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit lifecycle: cleanup all prepared contexts.

        If an error occurred, rollback is triggered first (may be no-op if
        already rolled back by pipeline). Then cleanup phase always runs.
        """
        try:
            if exc_val is not None and self._pipeline is not None:
                # Error occurred - rollback (may be no-op if already done)
                await self._pipeline.rollback_all(exc_val)  # type: ignore[arg-type]

            # Run cleanup phase
            if self._pipeline is not None:
                ctx = self._pipeline.current_context
                if ctx is not None:
                    try:
                        await self._pipeline.run_until(ctx, stop_after="cleanup")
                    except Exception as e:
                        # Cleanup should never raise, but log if it does
                        logger.exception("Cleanup phase failed: %s", e)

        finally:
            # Always unmark lifecycle for all bindings
            for binding in self._bindings:
                self.scope.mark_binding_lifecycle(binding.name, in_lifecycle=False)

            # Finalize emitter (calls formatter.finalize() with exception handling)
            if self._emitter is not None:
                self._emitter.finalize()

    # --- Synchronous Context Manager (for sync code) ---

    def __enter__(self) -> Self:
        """Synchronous enter - raises if async required."""
        import asyncio

        # Check if we're in an async context
        try:
            asyncio.get_running_loop()
            raise RuntimeError("Use 'async with ExecutionLifecycle(...)' in async context")
        except RuntimeError as e:
            if "no running event loop" not in str(e):
                raise

        # Run async enter synchronously
        return asyncio.run(self.__aenter__())

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Synchronous exit."""
        import asyncio

        asyncio.run(self.__aexit__(exc_type, exc_val, exc_tb))

    # --- Execution ---

    async def execute(self, prompt: str) -> ExecutionResult:
        """Execute with the provider and capture results.

        This method:
        1. Emits TaskStarted effect
        2. Checks for device - delegates to device or runs in-process
        3. Emits TaskCompleted/TaskFailed

        Device Branching:
        - If scope.current_device is None or has isolation_level="none",
          uses the existing in-process pipeline path.
        - Otherwise, delegates to _execute_on_device() for isolated execution.

        Args:
            prompt: The prompt to send to the LLM

        Returns:
            ExecutionResult from the provider

        Raises:
            RuntimeError: If called outside of lifecycle context
            Exception: If provider execution fails
        """
        if not self._entered:
            raise RuntimeError("Must enter ExecutionLifecycle before execute()")
        if self._executed:
            raise RuntimeError("execute() can only be called once per lifecycle")
        if self._pipeline is None:
            raise RuntimeError("Pipeline not initialized")
        assert self.attribution is not None  # guaranteed by __post_init__
        self._executed = True

        start_time = time.perf_counter()

        # Resolve device once for consistent attribution across
        # TaskStarted/TaskCompleted/TaskFailed
        device = self.scope.current_device
        _raw_name = getattr(device, "name", None) if device is not None else None
        device_name = _raw_name if isinstance(_raw_name, str) else None

        # Emit task started
        self._emit(
            TaskStarted(
                task_name=self.task_name,
                provider_id=self.attribution.provider_id,
                inputs={"prompt": prompt[:200]},  # Truncate for effect
                device_name=device_name,
                stage_name=self.stage_name,
            )
        )

        try:
            # Check for device-based execution
            if device is not None and device.capabilities.isolation_level != "none":
                # Device-delegated execution (container, cloud, etc.)
                result = await self._execute_on_device(device, prompt)
            else:
                # Existing in-process execution path
                result = await self._execute_in_process(prompt)

            # Emit task completed
            duration_ms = (time.perf_counter() - start_time) * 1000
            self._emit(
                TaskCompleted(
                    task_name=self.task_name,
                    provider_id=self.attribution.provider_id,
                    outputs={"text_length": len(result.output_text)},
                    duration_ms=duration_ms,
                    device_name=device_name,
                    stage_name=self.stage_name,
                )
            )

            return result

        except Exception as e:  # noqa: BLE001
            self._handle_task_failure(e, start_time, device_name=device_name, stage_name=self.stage_name)

    async def run_executor(self, executor: Any = None) -> None:
        """Execute a programmatic task through the lifecycle.

        This is the programmatic counterpart to execute(prompt). It runs the
        executor callable through the phase pipeline, emitting TaskStarted/
        TaskCompleted/TaskFailed effects, while skipping device delegation.

        Args:
            executor: Optional override callable. If None, uses self.executor.

        Raises:
            RuntimeError: If called outside lifecycle context or called twice.
            TaskExecutionError: If the executor raises.
        """
        if not self._entered:
            raise RuntimeError("Must enter ExecutionLifecycle before run_executor()")
        if self._executed:
            raise RuntimeError("run_executor() can only be called once per lifecycle")
        if self._pipeline is None:
            raise RuntimeError("Pipeline not initialized")
        assert self.attribution is not None  # guaranteed by __post_init__
        self._executed = True

        executor = executor or self.executor
        if executor is None:
            raise RuntimeError(
                "run_executor() requires an executor callable. Pass one as an argument or set self.executor."
            )
        executor_name = getattr(executor, "__qualname__", repr(executor))

        start_time = time.perf_counter()

        # Resolve device once for consistent attribution
        device = self.scope.current_device
        _raw_name = getattr(device, "name", None) if device is not None else None
        device_name = _raw_name if isinstance(_raw_name, str) else None

        # Emit task started
        self._emit(
            TaskStarted(
                task_name=self.task_name,
                provider_id=self.attribution.provider_id,
                inputs={"executor": executor_name},
                device_name=device_name,
                stage_name=self.stage_name,
            )
        )

        try:
            # Check for device-based execution of programmatic tasks
            if (
                device is not None
                and device.capabilities.isolation_level != "none"
                and hasattr(executor, "__self__")
                and getattr(executor.__self__.__class__, "_task_source", None) is not None
            ):
                result = await self._execute_on_device_scaffold(device, self._build_programmatic_spec(executor))
                self._device_task_outputs = dict(result.metadata.get("task_outputs", {})) if result.metadata else {}
            else:
                # Existing in-process pipeline path
                ctx = self._pipeline.current_context
                if ctx is None:
                    raise RuntimeError("No context available in pipeline")

                # Run all middle phases (execute phase will call the executor)
                ctx = await self._pipeline.run_until(ctx, stop_after="apply")

            # Emit task completed
            duration_ms = (time.perf_counter() - start_time) * 1000
            canary_report = self.kernel_v3_canary_report
            metadata = {}
            if canary_report is not None:
                metadata["kernel_v3_canary"] = canary_report.to_metadata()
            self._emit(
                TaskCompleted(
                    task_name=self.task_name,
                    provider_id=self.attribution.provider_id,
                    outputs={},
                    duration_ms=duration_ms,
                    device_name=device_name,
                    stage_name=self.stage_name,
                    metadata=metadata,
                )
            )

        except Exception as e:  # noqa: BLE001
            self._handle_task_failure(e, start_time, device_name=device_name, stage_name=self.stage_name)

    def _handle_task_failure(
        self,
        e: Exception,
        start_time: float | None = None,
        *,
        device_name: str | None = None,
        stage_name: str | None = None,
    ) -> NoReturn:
        """Emit TaskFailed, auto-debug, and wrap/re-raise as TaskExecutionError.

        Shared by execute() and run_executor() so error handling stays consistent.
        """
        raw_phase = getattr(e, "phase", "execute")
        valid_phases = {"configure", "prepare", "execute", "extract", "apply", "cleanup", ""}
        phase = raw_phase if raw_phase in valid_phases else "execute"
        session_id = getattr(e, "session_id", None)
        last_tool_name = getattr(e, "last_tool_name", None)
        tool_calls_completed = getattr(e, "tool_calls_completed", 0)
        suggestions = tuple(getattr(e, "suggestions", []))
        error_location = _extract_error_location(e)

        # Attribution is guaranteed non-None after __post_init__
        assert self.attribution is not None  # safety for mypy

        self._emit(
            TaskFailed(
                task_name=self.task_name,
                provider_id=self.attribution.provider_id,
                error=smart_truncate(str(e), 500),
                error_type=type(e).__name__,
                duration_ms=(time.perf_counter() - start_time) * 1000 if start_time is not None else 0.0,
                device_name=device_name,
                stage_name=stage_name,
                phase=phase,  # type: ignore[arg-type]
                session_id=session_id,
                last_tool_name=last_tool_name,
                tool_calls_completed=tool_calls_completed,
                suggestions=suggestions,
                error_location=error_location,
            )
        )

        # Auto-print debug summary if verbose mode has auto_debug_on_failure enabled
        if self._emitter is not None and self._emitter.formatter is not None:
            config = getattr(self._emitter.formatter, "config", None)
            if config is not None and getattr(config, "auto_debug_on_failure", False):
                import sys

                debug_output = self.scope.effects.debug_summary()
                print(f"\n{debug_output}", file=sys.stderr)  # noqa: T201 — intentional debug output

        if isinstance(e, TaskExecutionError):
            raise e

        raise TaskExecutionError(
            str(e),
            task_name=self.task_name or "unknown",
            phase=phase,
            effects=self.scope.effects,
            suggestions=suggestions,
            cause=e,
        ) from e

    async def _execute_in_process(self, prompt: str) -> ExecutionResult:
        """Execute using the existing in-process pipeline.

        This is the original execution path - runs phases directly
        via the pipeline without device isolation.

        Args:
            prompt: The prompt to send to the LLM

        Returns:
            ExecutionResult from the provider
        """
        # Set prompt in context and run phases
        ctx = self._pipeline.current_context  # type: ignore[union-attr]
        if ctx is None:
            raise RuntimeError("No context available in pipeline")

        ctx = ctx.with_prompt(prompt)
        self._pipeline.update_context(ctx)  # type: ignore[union-attr]

        # Run execute through apply phases
        ctx = await self._pipeline.run_until(ctx, stop_after="apply")  # type: ignore[union-attr]

        # Get result from context
        result = ctx.result
        if result is None:
            raise RuntimeError("No result available after execute phase")

        return result

    async def _execute_on_device(
        self,
        device: DeviceProtocol,
        prompt: str,
    ) -> ExecutionResult:
        """Execute using a device for isolation.

        This is the device-delegated LLM path - sets prompt on phase context
        for cache key, builds an LLM execution spec, and delegates to the
        shared scaffold.

        Args:
            device: Device to execute on
            prompt: The prompt to send to the LLM

        Returns:
            ExecutionResult from the device
        """
        from shepherd_core.foundation.protocols.device import ExecutionSpec

        # Set prompt on phase context (needed for cache key computation)
        phase_ctx = self._pipeline.current_context  # type: ignore[union-attr]
        if phase_ctx is not None:
            phase_ctx = phase_ctx.with_prompt(prompt)
            self._pipeline.update_context(phase_ctx)  # type: ignore[union-attr]

        def build_llm_spec(context_states: dict[str, Any]) -> ExecutionSpec:
            # Build provider config
            provider_config: dict[str, Any] = {}
            if self.provider is not None:
                if hasattr(self.provider, "to_config"):
                    provider_config = self.provider.to_config()
                elif hasattr(self.provider, "model"):
                    provider_config = {"model": self.provider.model}

            # Extract tools from context capabilities
            from shepherd_core.types import tools_for_capabilities

            all_capabilities: set[str] = set()
            for binding in self._bindings:
                bind_ctx = binding.context
                if hasattr(bind_ctx, "capabilities"):
                    all_capabilities.update(bind_ctx.capabilities)

            tools = list(tools_for_capabilities(frozenset(all_capabilities))) if all_capabilities else None

            return ExecutionSpec(
                prompt=prompt,
                provider_config=provider_config,
                output_format=self.output_format,
                tools=tools,
            )

        return await self._execute_on_device_scaffold(device, build_llm_spec)

    async def _execute_on_device_scaffold(
        self,
        device: DeviceProtocol,
        build_spec: Callable[[dict[str, Any]], ExecutionSpec],
    ) -> ExecutionResult:
        """Shared orchestration scaffold for device execution.

        Both LLM and programmatic paths use this scaffold. The build_spec
        callback determines what kind of ExecutionSpec is constructed.

        Args:
            device: Device to execute on
            build_spec: Callable[[dict[str, Any]], ExecutionSpec]

        Returns:
            ExecutionResult from the device
        """
        from shepherd_core.foundation.protocols.device import SandboxConfig

        # 0. Check cache first (before doing any device work)
        # CacheCheckPhase runs on host and can skip device execution entirely
        phase_ctx = self._pipeline.current_context  # type: ignore[union-attr]
        if phase_ctx is not None:
            cache_check_phase = CacheCheckPhase()
            phase_ctx = await cache_check_phase.execute(phase_ctx)
            self._pipeline.update_context(phase_ctx)  # type: ignore[union-attr]

            # If cache hit, return cached result without device execution
            if phase_ctx.cache_hit and phase_ctx.result is not None:
                self._mark_device_phases_completed()
                return phase_ctx.result

        # 1. Serialize context states
        context_states: dict[str, Any] = {}
        for binding in self._bindings:
            bind_ctx = binding.context
            if hasattr(bind_ctx, "to_state"):
                context_states[binding.name] = bind_ctx.to_state()

        # 2. Create sandbox
        config = SandboxConfig(context_states=context_states)
        sandbox = await device.create_sandbox(self.scope, config)  # type: ignore[arg-type]

        # Pass task identity through sandbox metadata for effect attribution
        if hasattr(sandbox, "_metadata") and self.task_name:
            sandbox._metadata["task_name"] = self.task_name

        try:
            # 3. Build execution spec (LLM or programmatic via callback)
            spec = build_spec(context_states)

            # 4. Execute on device
            result = await device.execute(sandbox, spec)

            # 5. Extract effects
            bundle = await device.extract_effects(sandbox, result)

            # 6. Apply effects to scope
            self._apply_effect_bundle(bundle)

            # 7. Merge session overlay to host (if applicable)
            # Only for LLM tasks (task_spec is None means LLM path).
            # This must happen BEFORE cleanup destroys the overlay.
            # The forked session transcript needs to be preserved for future resumption.
            # See PLAN-session-resumption-containers.md Change 10.
            if spec.task_spec is None and hasattr(device, "merge_session_to_host"):
                try:
                    device.merge_session_to_host(sandbox)
                except Exception as e:  # noqa: BLE001
                    # Session merge failure shouldn't fail the execution
                    logger.warning(f"Failed to merge session overlay: {e}")

            # 8. Run cache store phase (must happen on host, not in container)
            # Device execution handles execute/artifact/extract/apply internally,
            # but cache storage needs host filesystem access.
            phase_ctx = self._pipeline.current_context  # type: ignore[union-attr]
            if phase_ctx is not None:
                phase_ctx = phase_ctx.with_result(result)  # type: ignore[arg-type]
                cache_store_phase = CacheStorePhase()
                await cache_store_phase.execute(phase_ctx)

            # 9. Mark phases as completed so __aexit__ doesn't re-run them
            self._mark_device_phases_completed()

            return result  # type: ignore[return-value]

        finally:
            # Cleanup container but PRESERVE overlay directories for workspace layering.
            # Overlays are cleaned up when the scope exits/discards, not after each task.
            # This enables Task B to see Task A's changes via stacked overlays.
            # See: PLAN-workspace-patch-layering.md
            await device.cleanup(sandbox, preserve_overlays=True)  # type: ignore[call-arg]

    def _build_programmatic_spec(self, executor: Any) -> Callable[[dict[str, Any]], ExecutionSpec]:
        """Return a closure that builds a programmatic ExecutionSpec from executor metadata.

        Args:
            executor: The bound method (e.g., task_instance.execute)

        Returns:
            A callable(context_states) -> ExecutionSpec
        """
        import asyncio

        from shepherd_core.foundation.protocols.device import ExecutionSpec, TaskSpec

        from shepherd_runtime.task.source_analysis import extract_task_imports

        task_instance = executor.__self__
        task_class = task_instance.__class__
        meta = task_class._task_meta
        task_source = task_class._task_source
        task_class_name = task_class.__name__

        # Extract imports with fallback to empty tuple
        try:
            task_imports = tuple(extract_task_imports(task_class))
        except Exception:  # noqa: BLE001
            task_imports = ()

        # Serialize inputs: model_dump filtered to input field names
        input_names = set(meta.inputs.keys())
        all_data = task_instance.model_dump(mode="json")
        serialized_inputs = {k: v for k, v in all_data.items() if k in input_names}

        # Output field names
        output_fields = tuple(meta.outputs.keys())

        # Determine if executor is async
        is_async = asyncio.iscoroutinefunction(executor)

        def build_spec(context_states: dict[str, Any]) -> ExecutionSpec:
            # Build context_fields mapping from meta.contexts and binding names
            # Binding name == field name in meta.contexts
            context_fields: dict[str, str] = {name: name for name in meta.contexts}

            task_spec = TaskSpec(
                task_source=task_source,
                task_class_name=task_class_name,
                task_imports=task_imports,
                task_inputs=serialized_inputs,
                output_fields=output_fields,
                context_fields=context_fields,
                is_async=is_async,
            )

            return ExecutionSpec(
                prompt="",
                provider_config={},
                task_spec=task_spec,
            )

        return build_spec

    def _mark_device_phases_completed(self) -> None:
        """Mark execute through apply phases as completed after device execution.

        When using device-based execution, the device handles these phases
        internally. We need to mark them as completed in the pipeline so that
        __aexit__'s run_until(cleanup) doesn't try to re-run them.
        """
        if self._pipeline is None:
            return

        # Phases handled by device execution
        device_phases = ["execute", "artifact", "extract", "apply"]

        for phase_name in device_phases:
            phase_index = self._pipeline._get_phase_index(phase_name)
            if phase_index is not None and phase_index >= self._pipeline._phase_index:
                phase = self._pipeline.phases[phase_index]
                if phase not in self._pipeline._completed_phases:
                    self._pipeline._completed_phases.append(phase)

        # Advance phase_index past the device-handled phases
        cleanup_index = self._pipeline._get_phase_index("cleanup")
        if cleanup_index is not None:
            self._pipeline._phase_index = cleanup_index

    def _apply_effect_bundle(self, bundle: EffectBundle) -> None:
        """Apply extracted effects from device execution to scope.

        Args:
            bundle: EffectBundle from device.extract_effects()
        """
        # Apply lifecycle effects
        for effect in bundle.lifecycle_effects:
            self._emit(effect)

        # Apply context effects
        for binding_name, effects in bundle.context_effects.items():
            for effect in effects:
                # Add binding_name attribution if not present
                if (not hasattr(effect, "binding_name") or effect.binding_name is None) and hasattr(
                    effect, "with_binding"
                ):
                    effect = effect.with_binding(binding_name)
                self._emit(effect)

    # --- Context Access ---

    def get_context(self, name: str) -> ExecutionContext:
        """Get the captured context by name.

        Returns the updated context after capture, or the prepared
        context if capture hasn't run yet.
        """
        # Check pipeline context first
        if self._pipeline is not None:
            ctx = self._pipeline.current_context
            if ctx is not None and name in ctx.context_outputs:
                return ctx.context_outputs[name]

        # Fall back to scope's current context
        for binding in self._bindings:
            if binding.name == name:
                return self.scope.get_context(name)

        available = [b.name for b in self._bindings]
        raise BindingNotFoundError(name, available)

    @property
    def composed_binding(self) -> ProviderBinding | None:
        """The composed binding from all contexts."""
        if self._pipeline is not None:
            ctx = self._pipeline.current_context
            if ctx is not None:
                return ctx.composed_binding
        return None

    @property
    def context_outputs(self) -> dict[str, ExecutionContext]:
        """All captured contexts by name."""
        if self._pipeline is not None:
            ctx = self._pipeline.current_context
            if ctx is not None:
                return dict(ctx.context_outputs)
        return {}

    @property
    def artifact_outputs(self) -> dict[str, Any]:
        """All collected artifacts by field name."""
        if self._pipeline is not None:
            ctx = self._pipeline.current_context
            if ctx is not None:
                return dict(ctx.artifact_outputs)
        return {}

    @property
    def cache_hit(self) -> bool:
        """True if execution used cached result."""
        if self._pipeline is not None:
            ctx = self._pipeline.current_context
            if ctx is not None:
                return ctx.cache_hit
        return False

    @property
    def cached_outputs(self) -> dict[str, Any]:
        """Cached outputs if cache_hit is True."""
        if self._pipeline is not None:
            ctx = self._pipeline.current_context
            if ctx is not None:
                return dict(ctx.cached_outputs)
        return {}

    @property
    def kernel_v3_canary_report(self) -> Any | None:
        """Most recent kernel-v3 canary report, if one was produced."""
        if self._pipeline is not None:
            ctx = self._pipeline.current_context
            if ctx is not None:
                return ctx.kernel_v3_canary_report
        return None


# =============================================================================
# Convenience Functions
# =============================================================================


async def execute(
    scope: Scope,
    prompt: str,
    provider: Provider | str | None = None,
    task_name: str | None = None,
    taskref_policy: TaskRefReconstructionPolicy | None = None,
) -> tuple[ExecutionResult, dict[str, ExecutionContext]]:
    """Convenience function for single-shot execution.

    Creates an ExecutionLifecycle internally, runs execute(), and returns
    both the result and updated contexts.

    Args:
        scope: The scope with bound contexts
        prompt: The prompt to send
        provider: Provider instance or name (uses default if None)
        task_name: Optional task name for attribution
        taskref_policy: Policy for TaskRef reconstruction (uses default if None)

    Returns:
        Tuple of (ExecutionResult, dict of updated contexts by name)

    Example:
        with Scope() as scope:
            workspace = scope.bind("workspace", WorkspaceRef.from_path("/repo"))
            scope.register_provider("claude", provider, default=True)

            result, outputs = await execute(scope, "Fix the bug")
            # workspace ContextRef auto-updates - no need for outputs dict
            print(workspace.pending_patches)
    """
    # Resolve provider
    if provider is None:
        resolved_provider = scope.get_provider()
    elif isinstance(provider, str):
        resolved_provider = scope.get_provider(provider)
    else:
        resolved_provider = provider

    async with ExecutionLifecycle(
        scope=scope,
        provider=resolved_provider,
        task_name=task_name,
        taskref_policy=taskref_policy,
    ) as lifecycle:
        result = await lifecycle.execute(prompt)
        return result, lifecycle.context_outputs


__all__ = [
    "ApplyPhase",
    "ArtifactPhase",
    "CacheCheckPhase",
    "CacheStorePhase",
    # Phase pipeline components (Phase 3+)
    "CleanupError",
    "CleanupPhase",
    "ConfigurePhase",
    "EffectEmitter",
    "ExecutePhase",
    # Main API
    "ExecutionLifecycle",
    "ExtractPhase",
    # Pipeline orchestrator (Phase 5)
    "LifecyclePipeline",
    # Phase handlers (Phase 4)
    "Phase",
    "PhaseBase",
    "PhaseContext",
    "PreparePhase",
    "execute",
    # Sandbox registration
    "register_sandbox_factory",
]
