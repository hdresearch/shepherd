"""PhaseContext: Data carrier for the lifecycle phase pipeline.

This internal module defines the PhaseContext dataclass that flows through
the phase pipeline, carrying both phase-specific outputs and shared state.

Usage:
    ctx = PhaseContext(
        scope=scope,
        provider=provider,
        task_name="my-task",
        prompt="Do something",
        bindings=tuple(scope.all_bindings()),
    )

    # Phases return new context with updated fields
    ctx = ctx.with_composed_binding(binding)
    ctx = ctx.with_prepared(prepared_contexts, sandboxes)
    ctx = ctx.with_result(result)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from shepherd_core.context.kernel import ExecutionContext
    from shepherd_core.effects import Effect
    from shepherd_core.provider import Provider
    from shepherd_core.types import ExecutionResult, ProviderBinding

    from shepherd_runtime._scope._binding_registry import BindingWithState
    from shepherd_runtime.context import Sandbox
    from shepherd_runtime.scope import Scope
    from shepherd_runtime.task.markers import ArtifactMarker
    from shepherd_runtime.task.output import TaskRefReconstructionPolicy


@dataclass(frozen=True)
class CleanupError:
    """Record of a cleanup failure.

    Used to track cleanup errors without raising exceptions, allowing
    cleanup to continue for remaining resources.

    Attributes:
        resource_name: Identifies what failed to clean up.
            Convention: "context:{binding_name}" or "sandbox:{binding_name}"
        exception: The exception that occurred during cleanup.

    Example:
        errors = [
            CleanupError("context:workspace", RuntimeError("Connection lost")),
            CleanupError("sandbox:workspace", OSError("Permission denied")),
        ]
    """

    resource_name: str
    exception: Exception

    def __str__(self) -> str:
        return f"{self.resource_name}: {self.exception}"

    def __repr__(self) -> str:
        return f"CleanupError(resource_name={self.resource_name!r}, exception={self.exception!r})"


@dataclass(frozen=True)
class Attribution:
    """Effect attribution metadata, decoupled from Provider.

    Constructed once at lifecycle entry and threaded through PhaseContext.
    For LLM tasks, built from the provider. For programmatic tasks,
    built from the task name alone (provider_id=None).
    """

    task_name: str
    provider_id: str | None
    source: Literal["llm", "programmatic"]


@dataclass(frozen=True)
class PhaseContext:
    """Data carrier passed through the phase pipeline.

    Immutability Model
    ------------------
    PhaseContext uses a **hybrid immutability model**:

    - **Phase-specific fields** (composed_binding, extracted_effects, etc.) are
      immutable. Phases return a new PhaseContext with updated fields, ensuring
      clear data flow and easy testing.

    - **`scope`** is a shared mutable reference representing the execution
      environment. Phases mutate scope directly for state persistence (e.g.,
      updating bindings, marking lifecycle state).

    This hybrid exists because:
    1. Scope mutations must be visible to subsequent phases immediately
    2. Scope is the canonical location for binding state
    3. Making scope mutations deferred would complicate sandbox/cleanup logic

    The lifecycle phases aren't pure functions transforming data - they
    orchestrate real-world side effects (API calls, file operations, sandbox
    creation). PhaseContext provides structured data flow while phases do
    their imperative work on the shared Scope.

    Field Groups
    ------------
    Fields are grouped by lifecycle stage:
    - Initial: Set at pipeline creation
    - Configure: Set after configure phase
    - Prepare: Set after prepare phase
    - Execute: Set after execute phase
    - Artifact: Set after artifact phase
    - Extract: Set after extract phase
    - Apply: Set after apply phase
    - Error: Set when pipeline catches an exception
    - Cleanup: Tracks cleanup state for idempotency

    Thread Safety
    -------------
    PhaseContext is not thread-safe. Phases execute sequentially within a
    single pipeline. Do not share PhaseContext across threads.

    Example:
        # Create initial context
        ctx = PhaseContext(
            scope=scope,
            provider=provider,
            task_name="analyze",
            prompt="Analyze the code",
            bindings=tuple(scope.all_bindings()),
        )

        # Configure phase returns new context
        ctx = await configure_phase.execute(ctx)
        assert ctx.composed_binding is not None

        # Prepare phase returns new context
        ctx = await prepare_phase.execute(ctx)
        assert len(ctx.prepared_contexts) > 0
    """

    # =========================================================================
    # Initial (set at creation)
    # =========================================================================

    scope: Scope
    """Shared mutable reference to the execution environment.

    Phases mutate scope directly for:
    - Updating context bindings (scope.update_context())
    - Marking lifecycle state (scope.mark_binding_lifecycle())
    - Emitting effects (scope.emit())
    """

    provider: Provider | None = None
    """The provider to execute with. None for programmatic tasks."""

    task_name: str = ""
    """Task name for effect attribution and logging."""

    prompt: str = ""
    """The prompt to send to the LLM. Set before execute phase."""

    attribution: Attribution | None = None
    """Effect attribution metadata. When set, phases use this for provider_id
    instead of reading ctx.provider.provider_id directly."""

    executor: Any | None = None
    """Callable for programmatic task execution. When set, ExecutePhase
    calls this instead of provider.execute_sdk()."""

    kernel_v3_canary_spec: Any | None = None
    """Optional programmatic task opt-in for kernel-v3 canary execution."""

    kernel_v3_canary_target: Any | None = None
    """Task instance whose outputs are written by kernel-v3 canary execution."""

    bindings: tuple[BindingWithState, ...] = ()
    """All bindings from scope at pipeline creation time."""

    artifact_markers: dict[str, ArtifactMarker] = field(default_factory=dict)
    """Artifact field markers from the task definition."""

    output_format: dict[str, Any] | None = None
    """JSON schema for structured output (if any)."""

    # =========================================================================
    # Cache State (set at creation, used by CacheCheck/CacheStore phases)
    # =========================================================================

    task_inputs: dict[str, Any] = field(default_factory=dict)
    """Task input values for cache key computation."""

    task_meta: Any = None  # TaskMetadata, typed as Any to avoid circular import
    """Task metadata for cache key computation and output extraction."""

    taskref_policy: TaskRefReconstructionPolicy | None = None
    """Caller-owned policy for TaskRef output/cache rehydration."""

    # =========================================================================
    # Cache Phase Output
    # =========================================================================

    cache_hit: bool = False
    """True if CacheCheckPhase found a cached result."""

    cached_outputs: dict[str, Any] = field(default_factory=dict)
    """Cached output values (populated on cache hit)."""

    execution_key: str = ""
    """Cache key used for lookup/storage."""

    # =========================================================================
    # Configure Phase Output
    # =========================================================================

    composed_binding: ProviderBinding | None = None
    """The composed binding from all contexts after configure phase."""

    # =========================================================================
    # Prepare Phase Output
    # =========================================================================

    prepared_contexts: dict[str, ExecutionContext] = field(default_factory=dict)
    """Prepared context instances by binding name.

    Stored here (not just in scope) for reliable rollback - we need to know
    exactly which contexts were prepared even if scope state changes.
    """

    sandboxes: dict[str, Sandbox] = field(default_factory=dict)
    """Sandbox instances by binding name (for contexts that have sandboxes)."""

    # =========================================================================
    # Execute Phase Output
    # =========================================================================

    result: ExecutionResult | None = None
    """The execution result from the provider."""

    kernel_v3_canary_report: Any | None = None
    """Cheap report for a kernel-v3 canary decision, when one ran."""

    # =========================================================================
    # Artifact Phase Output
    # =========================================================================

    artifact_outputs: dict[str, Any] = field(default_factory=dict)
    """Collected artifact contents by field name."""

    artifact_effects: tuple[Effect, ...] = ()
    """Effects emitted during artifact collection."""

    # =========================================================================
    # Extract Phase Output
    # =========================================================================

    extracted_effects: tuple[Effect, ...] = ()
    """All effects extracted from all contexts."""

    context_effects: dict[str, tuple[Effect, ...]] = field(default_factory=dict)
    """Effects grouped by binding name (for per-context application)."""

    # =========================================================================
    # Apply Phase Output
    # =========================================================================

    context_outputs: dict[str, ExecutionContext] = field(default_factory=dict)
    """Updated context instances after effect application."""

    # =========================================================================
    # Error State
    # =========================================================================

    error: Exception | None = None
    """Set when pipeline catches an exception, so cleanup knows failure context."""

    # =========================================================================
    # Cleanup State (for idempotency)
    # =========================================================================

    cleaned_up_contexts: frozenset[str] = frozenset()
    """Binding names of contexts that have been cleaned up.

    Used to prevent double-cleanup when both rollback and cleanup run.
    """

    discarded_sandboxes: frozenset[str] = frozenset()
    """Binding names of sandboxes that have been discarded.

    Used to prevent double-discard when both rollback and cleanup run.
    """

    cleanup_errors: tuple[CleanupError, ...] = ()
    """Errors that occurred during cleanup (logged, not raised)."""

    # =========================================================================
    # Timing
    # =========================================================================

    phase_timings: dict[str, float] = field(default_factory=dict)
    """Duration in milliseconds for each completed phase."""

    # =========================================================================
    # Convenience Methods: Phase Output Updates
    # =========================================================================

    def with_composed_binding(self, binding: ProviderBinding) -> PhaseContext:
        """Return new context with composed binding set.

        Args:
            binding: The composed ProviderBinding from configure phase.

        Returns:
            New PhaseContext with composed_binding field updated.
        """
        return replace(self, composed_binding=binding)

    def with_prepared(
        self,
        prepared_contexts: dict[str, ExecutionContext],
        sandboxes: dict[str, Sandbox],
    ) -> PhaseContext:
        """Return new context with prepare phase outputs.

        Args:
            prepared_contexts: Dict of prepared context instances by binding name.
            sandboxes: Dict of sandbox instances by binding name.

        Returns:
            New PhaseContext with prepared_contexts and sandboxes fields updated.
        """
        return replace(
            self,
            prepared_contexts=dict(prepared_contexts),
            sandboxes=dict(sandboxes),
        )

    def with_result(self, result: ExecutionResult) -> PhaseContext:
        """Return new context with execution result.

        Args:
            result: The ExecutionResult from provider.execute_sdk().

        Returns:
            New PhaseContext with result field updated.
        """
        return replace(self, result=result)

    def with_kernel_v3_canary_report(self, report: Any) -> PhaseContext:
        """Return new context with kernel-v3 canary report set."""
        return replace(self, kernel_v3_canary_report=report)

    def with_artifacts(
        self,
        outputs: dict[str, Any],
        effects: tuple[Effect, ...],
    ) -> PhaseContext:
        """Return new context with artifact phase outputs.

        Args:
            outputs: Dict of artifact contents by field name.
            effects: Tuple of ArtifactCollected effects.

        Returns:
            New PhaseContext with artifact_outputs and artifact_effects updated.
        """
        return replace(
            self,
            artifact_outputs=dict(outputs),
            artifact_effects=effects,
        )

    def with_extracted_effects(
        self,
        all_effects: tuple[Effect, ...],
        per_context: dict[str, tuple[Effect, ...]],
    ) -> PhaseContext:
        """Return new context with extracted effects.

        Args:
            all_effects: All effects from all contexts (flat tuple).
            per_context: Effects grouped by binding name for per-context apply.

        Returns:
            New PhaseContext with extracted_effects and context_effects updated.
        """
        return replace(
            self,
            extracted_effects=all_effects,
            context_effects=dict(per_context),
        )

    def with_context_outputs(
        self,
        outputs: dict[str, ExecutionContext],
    ) -> PhaseContext:
        """Return new context with applied context outputs.

        Args:
            outputs: Dict of updated context instances by binding name.

        Returns:
            New PhaseContext with context_outputs field updated.
        """
        return replace(self, context_outputs=dict(outputs))

    def with_cache_hit(
        self,
        outputs: dict[str, Any],
        execution_key: str,
    ) -> PhaseContext:
        """Return new context marking a cache hit.

        Args:
            outputs: The cached output values.
            execution_key: The cache key that was hit.

        Returns:
            New PhaseContext with cache_hit=True and cached data.
        """
        return replace(
            self,
            cache_hit=True,
            cached_outputs=dict(outputs),
            execution_key=execution_key,
        )

    def with_error(self, error: Exception) -> PhaseContext:
        """Return new context with error set (for cleanup phase).

        Args:
            error: The exception that caused pipeline failure.

        Returns:
            New PhaseContext with error field set.
        """
        return replace(self, error=error)

    def with_phase_timing(self, phase: str, duration_ms: float) -> PhaseContext:
        """Return new context with phase timing recorded.

        Args:
            phase: Phase name (e.g., "configure", "prepare").
            duration_ms: Duration of the phase in milliseconds.

        Returns:
            New PhaseContext with phase_timings updated.
        """
        timings = dict(self.phase_timings)
        timings[phase] = duration_ms
        return replace(self, phase_timings=timings)

    def with_prompt(self, prompt: str) -> PhaseContext:
        """Return new context with prompt set.

        Args:
            prompt: The prompt to send to the LLM.

        Returns:
            New PhaseContext with prompt field updated.
        """
        return replace(self, prompt=prompt)

    def with_sandbox_wired_binding(self) -> PhaseContext:
        """Wire sandbox paths into composed_binding.cwd and context_description.

        Called after prepare phase to update composed_binding.cwd to point
        to the sandbox path, enabling provider to execute in isolation.

        This is critical for sandbox isolation to work correctly - the provider
        needs to execute in the sandbox directory, not the original workspace.
        The context_description is also updated so the agent sees the sandbox
        path, not the original path.

        Returns:
            New PhaseContext with composed_binding.cwd and context_description
            updated to sandbox path, or self unchanged if no sandboxes or no
            matching cwd found.
        """
        import logging

        logger = logging.getLogger(__name__)

        if not self.sandboxes or not self.composed_binding:
            return self

        composed = self.composed_binding

        # Find the sandbox that should provide the cwd
        # Use the sandbox whose original context provided the binding's cwd
        for binding_name, sandbox in self.sandboxes.items():
            ctx = self.prepared_contexts.get(binding_name)
            if ctx is None:
                continue

            # Get original cwd from context (WorkspaceRef has .path attribute)
            original_cwd = getattr(ctx, "path", None)
            if original_cwd and str(composed.cwd) == str(original_cwd):
                # Update cwd to sandbox path, preserve original_cwd for session tracking
                updates = {
                    "cwd": str(sandbox.path),
                    "original_cwd": str(original_cwd),  # Pre-sandbox workspace path
                }

                # Also update context_description to replace original path with sandbox path
                # TODO: This string substitution is pragmatic but can be fragile if the original
                # path appears as a substring of other paths. Consider a late-binding approach:
                # design/spikes/SPIKE-sandbox-path-substitution.md
                if composed.context_description:
                    updates["context_description"] = composed.context_description.replace(
                        str(original_cwd), str(sandbox.path)
                    )

                composed = composed.model_copy(update=updates)
                logger.debug(
                    "Sandbox path wired: %s -> %s",
                    original_cwd,
                    sandbox.path,
                )

                # Ensure session transcript is findable from new CWD.
                # The CLI derives the transcript project folder from CWD,
                # so changing CWD means the CLI looks in a different folder.
                # Symlink the previous transcript into the new project folder
                # so the CLI can find it when resuming/forking the session.
                # Skip for container devices — they use session overlays instead.
                device = getattr(self.scope, "current_device", None)
                isolation = getattr(getattr(device, "capabilities", None), "isolation_level", "none")
                if isolation == "none":
                    self._link_session_transcript(str(sandbox.path), logger)

                break

        if composed is self.composed_binding:
            return self  # No change

        return replace(self, composed_binding=composed)

    def _link_session_transcript(self, new_cwd: str, logger: Any) -> None:
        """Symlink a session transcript so the CLI can find it under the new CWD.

        When the CWD changes (e.g. to a per-task sandbox directory), the CLI
        looks for transcripts in ``~/.claude/projects/<project-from-new-cwd>/``.
        If a previous task created the transcript under a different CWD, the
        CLI won't find it.  This method creates a symlink from the new project
        folder to the actual transcript file.

        Uses duck-typing to find a SessionState-like context in
        ``prepared_contexts`` (must have ``session_id`` and ``transcript_path``).
        """
        from pathlib import Path

        from shepherd_core.types import compute_transcript_path

        # Find a context that looks like SessionState
        for ctx in self.prepared_contexts.values():
            session_id = getattr(ctx, "session_id", None)
            transcript_path = getattr(ctx, "transcript_path", None)
            if not session_id or not transcript_path:
                continue

            old_path = Path(transcript_path)
            if not old_path.exists():
                logger.debug("Session transcript not found at %s, skipping symlink", old_path)
                return

            new_path = Path(compute_transcript_path(new_cwd, session_id))
            if new_path.exists() or new_path.is_symlink():
                return  # Already accessible

            new_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                new_path.symlink_to(old_path)
                logger.debug("Linked session transcript: %s -> %s", new_path, old_path)
            except OSError as e:
                logger.warning("Could not symlink session transcript: %s", e)
            return  # Only handle the first session context

    # =========================================================================
    # Convenience Methods: Cleanup State Tracking
    # =========================================================================

    def mark_cleaned_up(self, binding_name: str) -> PhaseContext:
        """Mark a context as cleaned up (prevents double-cleanup).

        Args:
            binding_name: The binding name of the context that was cleaned up.

        Returns:
            New PhaseContext with binding_name added to cleaned_up_contexts.
        """
        return replace(
            self,
            cleaned_up_contexts=self.cleaned_up_contexts | {binding_name},
        )

    def mark_sandbox_discarded(self, binding_name: str) -> PhaseContext:
        """Mark a sandbox as discarded (prevents double-discard).

        Args:
            binding_name: The binding name of the sandbox that was discarded.

        Returns:
            New PhaseContext with binding_name added to discarded_sandboxes.
        """
        return replace(
            self,
            discarded_sandboxes=self.discarded_sandboxes | {binding_name},
        )

    def is_cleaned_up(self, binding_name: str) -> bool:
        """Check if context has already been cleaned up.

        Args:
            binding_name: The binding name to check.

        Returns:
            True if the context has been cleaned up, False otherwise.
        """
        return binding_name in self.cleaned_up_contexts

    def is_sandbox_discarded(self, binding_name: str) -> bool:
        """Check if sandbox has already been discarded.

        Args:
            binding_name: The binding name to check.

        Returns:
            True if the sandbox has been discarded, False otherwise.
        """
        return binding_name in self.discarded_sandboxes

    def with_cleanup_errors(self, errors: list[CleanupError]) -> PhaseContext:
        """Return new context with cleanup errors recorded.

        Args:
            errors: List of CleanupError instances from cleanup phase.

        Returns:
            New PhaseContext with cleanup_errors field set.
        """
        return replace(self, cleanup_errors=tuple(errors))

    # =========================================================================
    # Computed Properties
    # =========================================================================

    @property
    def effective_provider_id(self) -> str | None:
        """Provider ID for effect attribution.

        Prefers attribution.provider_id (set at lifecycle entry) over
        provider.provider_id. Returns None for programmatic tasks.
        """
        if self.attribution:
            return self.attribution.provider_id
        return self.provider.provider_id if self.provider else None

    @property
    def binding_names(self) -> tuple[str, ...]:
        """Names of all bindings in this context."""
        return tuple(b.name for b in self.bindings)

    @property
    def has_error(self) -> bool:
        """Whether the pipeline encountered an error."""
        return self.error is not None

    @property
    def has_cleanup_errors(self) -> bool:
        """Whether any cleanup errors occurred."""
        return len(self.cleanup_errors) > 0

    @property
    def total_effects(self) -> int:
        """Total number of extracted effects."""
        return len(self.extracted_effects)


__all__ = [
    "Attribution",
    "CleanupError",
    "PhaseContext",
]
