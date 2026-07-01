"""Effect types for the execution context framework.

This module defines:
- Effect: Base class with attribution
- Task lifecycle effects: TaskStarted, TaskCompleted, TaskFailed
- Context lifecycle effects: ContextConfigured, ContextPrepared, ContextCaptured, ContextCleanedUp
- Tool effects: ToolCallStarted, ToolCallCompleted, ToolCallRejected
- Domain effects: File*, Session*, Transfer*, ExternalAPICall

All effects are immutable (frozen Pydantic models) and carry attribution
metadata for filtering and debugging. Serializable via model_dump()/model_validate().
"""

from __future__ import annotations

import hashlib
import time
import warnings
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._decode import decode_effect, resolve_effect_class
from .registry import EffectTypeRegistry

# =============================================================================
# Type Aliases
# =============================================================================

# Lifecycle phases for error attribution
LifecyclePhase = Literal["configure", "prepare", "execute", "extract", "apply", "cleanup", ""]


# =============================================================================
# Preview Length Constants
# =============================================================================

# Different content types use different preview lengths based on typical use
PREVIEW_LENGTH_TOOL_OUTPUT = 500  # Tool/bash output - enough for error messages
PREVIEW_LENGTH_PROMPT = 200  # Prompts - identifier/preview only
PREVIEW_LENGTH_STEP_SUMMARY = 100  # Step outputs - usually JSON summaries

# Phase 1b preview lengths (same as tool output for consistency)
PREVIEW_LENGTH_FILE_CONTENT = 500  # File content preview
PREVIEW_LENGTH_ARTIFACT = 500  # Artifact content preview
PREVIEW_LENGTH_API_BODY = 500  # API request/response body preview

# =============================================================================
# Large Content Handling Constants
# =============================================================================

# Threshold for applying truncation. Content larger than this is truncated
# to HEAD + TAIL with a marker in between. Hash is always computed from
# the full content before truncation.
#
# See: truncate_with_hash() and docs/architecture/effect-system.md
MAX_CONTENT_SIZE = 1_048_576  # 1MB threshold for truncation
TRUNCATE_HEAD_SIZE = 512_000  # 500KB from start (imports, setup, context)
TRUNCATE_TAIL_SIZE = 512_000  # 500KB from end (errors, output, traces)


def truncate_with_hash(content: str) -> tuple[str, str, bool]:
    """Truncate large content while preserving head and tail context.

    Use this helper when emitting effects that may contain large content
    (>1MB). The hash is computed from the FULL content before truncation,
    enabling verification even when content is truncated.

    Strategy: Keep first 500KB + last 500KB with marker in between.
    This preserves:
    - Head: imports, setup, initial context
    - Tail: errors, final output, stack traces

    When to Use
    -----------
    - FileRead: Source files can be arbitrarily large
    - BashCommand: Build logs, test output can exceed 1MB
    - ExternalAPICall: API responses (when implemented)

    When NOT to Use
    ---------------
    - ArtifactWritten: Artifacts are typically small (<100KB)
    - PromptSent: Prompts are bounded by LLM context limits
    - StepCompleted: Step outputs should be JSON-serializable summaries

    Args:
        content: The content string to potentially truncate

    Returns:
        Tuple of (possibly_truncated_content, sha256_hash, was_truncated)

    Example:
        >>> # For effects with potentially large content
        >>> from shepherd_core.effects import truncate_with_hash, MAX_CONTENT_SIZE
        >>>
        >>> if len(content) > MAX_CONTENT_SIZE:
        ...     content, content_hash, truncated = truncate_with_hash(content)
        ... else:
        ...     content_hash = hashlib.sha256(content.encode()).hexdigest()
        ...     truncated = False
        >>>
        >>> effect = FileRead(
        ...     path=path,
        ...     content=content,
        ...     content_hash=content_hash,
        ...     content_truncated=truncated,
        ... )

    See Also:
        - MAX_CONTENT_SIZE: 1MB threshold for truncation
        - TRUNCATE_HEAD_SIZE: 500KB preserved from start
        - TRUNCATE_TAIL_SIZE: 500KB preserved from end
    """
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    if len(content) <= MAX_CONTENT_SIZE:
        return content, content_hash, False

    # Truncate with head + tail strategy
    head = content[:TRUNCATE_HEAD_SIZE]
    tail = content[-TRUNCATE_TAIL_SIZE:]
    truncated_bytes = len(content) - TRUNCATE_HEAD_SIZE - TRUNCATE_TAIL_SIZE

    truncated_content = f"{head}\n\n... [{truncated_bytes:,} bytes truncated] ...\n\n{tail}"

    return truncated_content, content_hash, True


# =============================================================================
# Intent Effect Classification
# =============================================================================

# Intent effect type discriminators
# Intent effects are declared via tool calls — what the agent tried to do.
INTENT_EFFECT_TYPES: frozenset[str] = frozenset(
    {
        "tool_call_started",
        "tool_call_completed",
        "tool_call_rejected",
    }
)


def is_intent_effect(effect: Effect) -> bool:
    """Check if effect is an intent effect (tool call).

    Intent effects capture what the agent declared/tried to do via tool calls.
    They are emitted during execution by the provider.

    Args:
        effect: The effect to check

    Returns:
        True if the effect is an intent effect (ToolCallStarted, ToolCallCompleted,
        or ToolCallRejected), False otherwise.
    """
    return effect.effect_type in INTENT_EFFECT_TYPES


def is_result_effect(effect: Effect) -> bool:
    """Check if effect has causality tracking (caused_by field).

    Result effects capture what actually happened (inferred via OverlayFS or
    other observation). They typically have a `caused_by` field linking to
    the intent effect that triggered them.

    Args:
        effect: The effect to check

    Returns:
        True if the effect has a `caused_by` attribute, indicating it's a
        result effect that links to a causing intent.

    Note:
        This checks for the presence of the `caused_by` attribute, not just
        whether it's non-None. Effects with `caused_by=None` still return True
        as they are structurally result effects (just without a linked cause).
    """
    return hasattr(effect, "caused_by")


# =============================================================================
# Data Types for Effects
# =============================================================================


class DiffPatch(BaseModel):
    """Unified diff with full content for state derivation.

    This class holds the actual patch content, enabling:
    - State reconstruction via apply_effect()
    - Time-travel debugging
    - Effect replay

    Attributes:
        patch: Full unified diff content
        files_changed: List of files affected by the patch
        source_step: Task/step that produced this patch (for attribution)
        sha256: Content hash for integrity verification
    """

    model_config = ConfigDict(frozen=True)

    patch: str = ""
    files_changed: tuple[str, ...] = ()
    source_step: str | None = None
    sha256: str | None = None

    @model_validator(mode="after")
    def _ensure_sha256(self) -> Self:
        """Auto-compute sha256 for non-empty patches.

        This ensures content_hash computation in WorkspaceRef is O(n) in the
        number of patches (just concatenating pre-computed hashes), not
        O(n * patch_size).

        Empty patches (no content or whitespace-only) have sha256=None,
        which is semantically correct - no content means no hash.
        """
        if self.patch.strip() and not self.sha256:
            import hashlib

            computed = hashlib.sha256(self.patch.encode("utf-8")).hexdigest()
            # Use object.__setattr__ because model is frozen
            object.__setattr__(self, "sha256", computed)
        return self

    @classmethod
    def from_diff(
        cls,
        patch: str,
        files: tuple[str, ...] | list[str],
        source_step: str | None = None,
    ) -> DiffPatch:
        """Create a DiffPatch from diff content.

        Args:
            patch: Unified diff content
            files: Files changed by the patch
            source_step: Optional task/step name for attribution

        Returns:
            DiffPatch with computed SHA-256 hash
        """
        import hashlib

        # Git's patch consumers (notably `git apply`) expect the patch stream to
        # end with a newline. Without it, Git can report `corrupt patch at line …`
        # even for otherwise-valid diffs.
        if patch and not patch.endswith("\n"):
            patch = patch + "\n"

        sha = hashlib.sha256(patch.encode()).hexdigest()
        files_tuple = tuple(files) if isinstance(files, list) else files
        return cls(patch=patch, files_changed=files_tuple, source_step=source_step, sha256=sha)

    def __len__(self) -> int:
        """Return patch size in bytes."""
        return len(self.patch.encode())


# =============================================================================
# Effect Base
# =============================================================================


class Effect(BaseModel):
    """Base class for all effects with attribution metadata.

    Every effect carries attribution to enable:
    - Filtering by task, provider, or context
    - Debugging multi-provider executions
    - Audit trails and replay

    Effect Categories (Intent vs Result)
    -------------------------------------
    Effects fall into two categories:

    1. **Intent Effects**: Declared via tool calls — what the agent tried to do.
       Examples: ToolCallStarted, ToolCallCompleted
       Emitted during execution by the provider.

    2. **Result Effects**: Inferred from execution environment — what happened.
       Examples: FileCreate, FilePatch, ArtifactWritten
       Extracted after execution by the device.

    Result effects can link to their causing intent via the `caused_by` field,
    enabling causality tracking and "why did this happen?" queries.

    Effect Routing
    --------------
    Effects are routed to contexts for state derivation via two mechanisms:

    1. **binding_name** (stable routing): Routes to the binding with matching name.
       Used for lifecycle effects that must reach their target regardless of
       context state changes. Takes precedence when set.

    2. **context_id** (semantic routing): Routes to contexts with matching identity.
       Used for domain effects that should follow the context's semantic identity.

    When binding_name is set, routing uses it exclusively. When only context_id
    is set, routing falls back to context_id matching. This dual-mode routing
    ensures lifecycle effects work correctly even when context_id changes during
    effect application (e.g., SessionState's context_id changes when session_id
    is set).

    Subclasses must define effect_type as a Literal field with default value.

    See Also:
        design/DECISIONS.md#d17-intentresult-effect-model - Intent/Result model decision
        design/device-abstraction/DESIGN-boundary-translation.md - Effect extraction
    """

    model_config = ConfigDict(frozen=True)

    # Type discriminator - subclasses override with Literal type
    effect_type: str = "base"

    # Attribution - who/what produced this effect
    task_name: str | None = None
    provider_id: str | None = None

    # Routing - determines which context processes this effect
    # binding_name takes precedence over context_id when set
    binding_name: str | None = None  # Stable routing for lifecycle effects
    context_id: str | None = None  # Semantic routing for domain effects

    # Temporal ordering
    timestamp: float = Field(default_factory=time.time)

    def with_attribution(
        self,
        task_name: str | None = None,
        provider_id: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> Self:
        """Return copy with updated attribution (None values preserve existing)."""
        return self.model_copy(
            update={
                "task_name": task_name if task_name is not None else self.task_name,
                "provider_id": provider_id if provider_id is not None else self.provider_id,
                "context_id": context_id if context_id is not None else self.context_id,
                "binding_name": binding_name if binding_name is not None else self.binding_name,
            }
        )

    def with_context(self, context_id: str) -> Self:
        """Convenience: set context_id for semantic routing."""
        return self.model_copy(update={"context_id": context_id})

    def with_binding(self, binding_name: str) -> Self:
        """Convenience: set binding_name for stable routing."""
        return self.model_copy(update={"binding_name": binding_name})


# =============================================================================
# Task Lifecycle Effects
# =============================================================================


class TaskStarted(Effect):
    """Task execution began.

    Attributes:
        inputs: Truncated task inputs for effect attribution.
        scope_id: The emitting scope's own unique ID. Stored directly on the
            Effect (not just on EffectLayer) so it survives merge — EffectLayer's
            scope_id is re-stamped during merge(), but this field is immutable.
            The profiler matches parent_scope_id references against this field
            to reconstruct the call tree.
        parent_scope_id: scope_id of the parent scope that spawned this task.
            For tasks invoked via child(), this is the parent scope's id.
            For tasks inside combinators (which use fork()), this resolves
            through the fork to the nearest non-fork ancestor scope via
            ImmutableScope._origin_id. None only for root tasks.
    """

    effect_type: Literal["task_started"] = "task_started"
    inputs: dict[str, Any] = Field(default_factory=dict)
    scope_id: str = ""
    parent_scope_id: str | None = None
    device_name: str | None = None
    stage_name: str | None = None


class TaskCompleted(Effect):
    """Task execution completed successfully."""

    effect_type: Literal["task_completed"] = "task_completed"
    outputs: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    device_name: str | None = None
    stage_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskFailed(Effect):
    """Task execution failed.

    Enhanced with debugging context to help developers quickly identify
    and fix issues. Includes phase information, session context, and
    actionable suggestions.

    Attributes:
        error: Truncated error message (first 500 chars)
        error_type: Exception class name (e.g., "SDKExecutionError")
        phase: Lifecycle phase where failure occurred
        session_id: Session ID for session-based providers (for debugging)
        last_tool_name: Name of the last tool called before failure
        tool_calls_completed: Number of successful tool calls before failure
        suggestions: Actionable suggestions for fixing the error
        error_location: Condensed location (e.g., "provider.py:830 in execute_sdk")
    """

    effect_type: Literal["task_failed"] = "task_failed"
    error: str = ""
    error_type: str = ""
    duration_ms: float = 0.0
    device_name: str | None = None
    stage_name: str | None = None
    # Debugging context
    phase: LifecyclePhase = ""
    session_id: str | None = None
    last_tool_name: str | None = None
    tool_calls_completed: int = 0
    suggestions: tuple[str, ...] = ()
    error_location: str | None = None


class InputProvided(Effect):
    """Input was provided to a task."""

    effect_type: Literal["input_provided"] = "input_provided"
    field_name: str = ""
    value: Any = None


class OutputProduced(Effect):
    """Output was produced by a task."""

    effect_type: Literal["output_produced"] = "output_produced"
    field_name: str = ""
    value: Any = None


# =============================================================================
# Context Lifecycle Effects
# =============================================================================


class ContextConfigured(Effect):
    """Context configure() was called, returned ProviderBinding."""

    effect_type: Literal["context_configured"] = "context_configured"
    binding_name: str = ""  # Name in scope (e.g., "workspace")
    capabilities: tuple[str, ...] = ()  # Capabilities declared


class ContextPrepared(Effect):
    """Context prepare() was called successfully."""

    effect_type: Literal["context_prepared"] = "context_prepared"
    binding_name: str = ""


class ContextCaptured(Effect):
    """Context capture() was called, state changes recorded."""

    effect_type: Literal["context_captured"] = "context_captured"
    binding_name: str = ""
    old_context_id: str = ""
    new_context_id: str = ""
    effect_count: int = 0  # Number of domain effects produced


class ContextCleanedUp(Effect):
    """Context cleanup() was called."""

    effect_type: Literal["context_cleaned_up"] = "context_cleaned_up"
    binding_name: str = ""
    had_error: bool = False
    already_cleaned: bool = False  # True if cleanup was already done by rollback


# =============================================================================
# Lifecycle Phase Effects
# =============================================================================


class LifecyclePhaseStarted(Effect):
    """A lifecycle phase has started.

    Used for verbose output and debugging to track the 7-phase
    execution lifecycle: configure, prepare, execute, artifact, extract, apply, cleanup.
    """

    effect_type: Literal["lifecycle_phase_started"] = "lifecycle_phase_started"
    phase: str = ""  # "configure", "prepare", "execute", "artifact", "extract", "apply", "cleanup"
    context_count: int = 0  # Number of contexts involved


class LifecyclePhaseCompleted(Effect):
    """A lifecycle phase has completed.

    Includes timing information for performance tracking.
    """

    effect_type: Literal["lifecycle_phase_completed"] = "lifecycle_phase_completed"
    phase: str = ""  # "configure", "prepare", "execute", "artifact", "extract", "apply", "cleanup"
    duration_ms: float = 0.0  # Time taken in milliseconds


class LifecyclePhaseFailed(Effect):
    """A lifecycle phase has failed.

    Provides observability into phase failures with timing and error information.
    Complements LifecyclePhaseStarted and LifecyclePhaseCompleted.

    Error info is included in the effect (not just the exception) for:
    - Streaming to external observability systems
    - Effect stream analysis without exception access
    - Audit trails that capture what went wrong
    """

    effect_type: Literal["lifecycle_phase_failed"] = "lifecycle_phase_failed"
    phase: str = ""  # "configure", "prepare", "execute", etc.
    duration_ms: float = 0.0  # Time taken before failure
    error_type: str = ""  # e.g., "PreparationError", "ExecutionError"
    error_message: str = ""  # First 500 chars of str(error), truncated if needed


# =============================================================================
# Tool Effects
# =============================================================================


class ToolCallStarted(Effect):
    """Tool invocation began."""

    effect_type: Literal["tool_call_started"] = "tool_call_started"
    tool_call_id: str = ""
    tool_name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class ToolCallCompleted(Effect):
    """Tool invocation completed with full output.

    Attributes:
        tool_call_id: Unique identifier for this tool call
        tool_name: Name of the tool that was invoked
        success: Whether the tool call succeeded
        output: Complete tool output
        duration_ms: Wall-clock duration of the tool call in milliseconds.
            For the Claude provider, this includes SDK message processing overhead
            (typically <5ms). For the LiteLLM provider, this is the direct tool
            execution time. The difference is negligible for profiling purposes.
            provider_id on each effect identifies which measurement model applies.
    """

    effect_type: Literal["tool_call_completed"] = "tool_call_completed"
    tool_call_id: str = ""
    tool_name: str = ""
    success: bool = True
    output: str = ""
    duration_ms: float = 0.0

    @property
    def output_preview(self) -> str:
        """Truncated preview for display (computed, not stored)."""
        if len(self.output) > PREVIEW_LENGTH_TOOL_OUTPUT:
            return self.output[:PREVIEW_LENGTH_TOOL_OUTPUT] + "..."
        return self.output


class ToolCallRejected(Effect):
    """Tool invocation was rejected by a context."""

    effect_type: Literal["tool_call_rejected"] = "tool_call_rejected"
    tool_call_id: str = ""
    tool_name: str = ""
    reason: str = ""
    rejected_by: str = ""  # context_id or "capability_check"


class ToolCallInfo(BaseModel):
    """Summary of a single tool call within a batch."""

    tool_name: str = ""
    tool_call_id: str | None = None
    input_preview: str = ""
    output_preview: str = ""


class ToolCallBatch(Effect):
    """Summary of tool calls executed server-side, without per-call causality.

    Emitted by providers that delegate tool dispatch to an external agent loop
    (e.g., OpenCode server). Individual tool-to-file causality is not available;
    all filesystem effects are linked to this batch as a group via batch_id.
    """

    effect_type: Literal["tool_call_batch"] = "tool_call_batch"
    batch_id: str = ""
    tool_calls: tuple[ToolCallInfo, ...] = ()


# =============================================================================
# File Domain Effects
# =============================================================================


class FileRead(Effect):
    """File was read.

    Stores full file content for replay and audit. Binary files store
    empty content with hash only (detected via isinstance check on res.output).

    Attributes:
        path: Path to the file that was read
        content: Complete file content (empty for binary files)
        content_hash: SHA256 hash of content for verification
        content_truncated: True if content was truncated (>1MB files)
    """

    effect_type: Literal["file_read"] = "file_read"
    path: str = ""
    content: str = ""
    content_hash: str = ""
    content_truncated: bool = False

    @property
    def content_preview(self) -> str:
        """Truncated preview for display (computed, not serialized)."""
        if len(self.content) > PREVIEW_LENGTH_FILE_CONTENT:
            return self.content[:PREVIEW_LENGTH_FILE_CONTENT] + "..."
        return self.content


class FileCreate(Effect):
    """File was created (Result Effect)."""

    effect_type: Literal["file_create"] = "file_create"
    path: str = ""
    content: str = ""  # File content (for reversibility)
    content_hash: str = ""  # Optional hash for large files
    caused_by: str | None = None  # tool_call_id that created this file

    def reverse(self) -> FileDelete:
        """Reverse a file creation by deleting it."""
        return FileDelete(
            path=self.path,
            had_content=self.content,
            task_name=self.task_name,
            provider_id=self.provider_id,
            context_id=self.context_id,
        )


class FilePatch(Effect):
    """File was modified via edit/patch (Result Effect)."""

    effect_type: Literal["file_patch"] = "file_patch"
    path: str = ""
    old_content: str = ""  # Content before patch (for reversibility)
    new_content: str = ""  # Content after patch
    patch_hash: str = ""  # Optional hash of the diff
    caused_by: str | None = None  # tool_call_id that modified this file

    def reverse(self) -> FilePatch:
        """Reverse a patch by swapping old and new content."""
        return FilePatch(
            path=self.path,
            old_content=self.new_content,
            new_content=self.old_content,
            patch_hash=self.patch_hash,
            task_name=self.task_name,
            provider_id=self.provider_id,
            context_id=self.context_id,
        )


class FileDelete(Effect):
    """File was deleted (Result Effect)."""

    effect_type: Literal["file_delete"] = "file_delete"
    path: str = ""
    had_content: str = ""  # Content before deletion (for reversibility)
    caused_by: str | None = None  # tool_call_id that deleted this file

    def reverse(self) -> FileCreate:
        """Reverse a file deletion by recreating it."""
        return FileCreate(
            path=self.path,
            content=self.had_content,
            task_name=self.task_name,
            provider_id=self.provider_id,
            context_id=self.context_id,
        )


class WorkspaceMaterialized(Effect):
    """Workspace derived state was materialized to real filesystem.

    Emitted when materialize() is called on a WorkspaceRef to apply
    pending patches to the actual repository.

    This effect is for audit trail only - the state transition happens
    via the returned new WorkspaceRef from materialize().

    Attributes:
        old_base_commit: The base commit before materialization
        new_base_commit: The new base commit (same if commit=False, new HEAD if commit=True)
        patches_applied: Number of patches that were applied
        committed: Whether a git commit was created
    """

    effect_type: Literal["workspace_materialized"] = "workspace_materialized"
    old_base_commit: str = ""
    new_base_commit: str = ""
    patches_applied: int = 0
    committed: bool = False


class ContextMaterialized(Effect):
    """A context's accumulated changes were materialized to the real world.

    Emitted by scope.commit() after each materialization attempt.
    Provides audit trail and observability for materialization operations.

    Attributes:
        binding_name: Name of the binding in the scope
        context_type: Type of the context (e.g., "WorkspaceRef", "SimpleWorkspace")
        changes_applied: Number of changes/patches applied
        paths_affected: File paths affected by the materialization
        success: Whether materialization succeeded
        committed: Whether a commit was created (for git-based contexts)
        error: Error message if materialization failed
        duration_ms: Time taken for materialization in milliseconds
    """

    effect_type: Literal["context_materialized"] = "context_materialized"
    binding_name: str = ""
    context_type: str = ""
    changes_applied: int = 0
    paths_affected: tuple[str, ...] = ()
    success: bool = True
    committed: bool = False
    error: str | None = None
    duration_ms: float = 0.0


# =============================================================================
# Step Lifecycle Effects
# =============================================================================


class StepStarted(Effect):
    """Step execution began within a composite task."""

    effect_type: Literal["step_started"] = "step_started"
    step_name: str = ""
    parent_task: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)


class StepCompleted(Effect):
    """Step execution completed successfully.

    Attributes:
        step_name: Name of the completed step
        parent_task: Parent task that owns this step
        outputs: Complete step outputs (any JSON-serializable value)
        duration_ms: Execution time in milliseconds

    Note:
        outputs must be JSON-serializable (dict, list, str, int, float,
        bool, None, datetime). Custom objects will fail during persistence.
    """

    effect_type: Literal["step_completed"] = "step_completed"
    step_name: str = ""
    parent_task: str = ""
    outputs: Any = None
    duration_ms: float = 0.0

    @property
    def outputs_summary(self) -> str:
        """Summary string for display (computed, not stored)."""
        if self.outputs is None:
            return ""
        s = str(self.outputs)
        if len(s) > PREVIEW_LENGTH_STEP_SUMMARY:
            return s[:PREVIEW_LENGTH_STEP_SUMMARY] + "..."
        return s


class StepFailed(Effect):
    """Step execution failed."""

    effect_type: Literal["step_failed"] = "step_failed"
    step_name: str = ""
    parent_task: str = ""
    error: str = ""
    error_type: str = ""
    duration_ms: float = 0.0


# =============================================================================
# Pipeline Stage Effects
# =============================================================================


class StageStarted(Effect):
    """A pipeline stage began execution via run_stage."""

    effect_type: Literal["stage_started"] = "stage_started"
    stage_name: str = ""


class StageCompleted(Effect):
    """A pipeline stage completed successfully."""

    effect_type: Literal["stage_completed"] = "stage_completed"
    stage_name: str = ""
    duration_ms: float = 0.0
    defaulted: bool = False
    partial: bool = False


class StageSkipped(Effect):
    """A pipeline stage was skipped (condition not met or OnError.skip)."""

    effect_type: Literal["stage_skipped"] = "stage_skipped"
    stage_name: str = ""
    reason: str = ""


class StageFailed(Effect):
    """A pipeline stage failed with OnError.fatal."""

    effect_type: Literal["stage_failed"] = "stage_failed"
    stage_name: str = ""
    error: str = ""
    duration_ms: float = 0.0


# =============================================================================
# Agent Trace Effects
# =============================================================================


class PromptSent(Effect):
    """Prompt was sent to the LLM.

    Attributes:
        system_prompt: Complete system prompt
        user_prompt: Complete user prompt
        total_tokens: Deprecated — always 0. Token counts are now reported via
            LLMResponseReceived. Retained for backwards compatibility with
            existing JSONL streams.
        input_tokens: Estimated input token count at send time, if available.
            For providers that can count tokens before the API call (e.g. via
            a tokenizer), this captures the request-side token budget. 0 means
            unknown. The authoritative post-hoc count is on LLMResponseReceived.
        model_id: Model identifier for the request. Set to the requested model
            (self.model) since the served model is not yet known at prompt-send
            time. The actual served model is reported on LLMResponseReceived.
    """

    effect_type: Literal["prompt_sent"] = "prompt_sent"
    system_prompt: str = ""
    user_prompt: str = ""
    total_tokens: int = 0
    input_tokens: int = 0
    model_id: str = ""

    @property
    def system_prompt_preview(self) -> str:
        """Truncated system prompt for display (computed, not stored)."""
        if len(self.system_prompt) > PREVIEW_LENGTH_PROMPT:
            return self.system_prompt[:PREVIEW_LENGTH_PROMPT] + "..."
        return self.system_prompt

    @property
    def user_prompt_preview(self) -> str:
        """Truncated user prompt for display (computed, not stored)."""
        if len(self.user_prompt) > PREVIEW_LENGTH_PROMPT:
            return self.user_prompt[:PREVIEW_LENGTH_PROMPT] + "..."
        return self.user_prompt


class LLMResponseReceived(Effect):
    """LLM response metadata captured after a provider invocation completes.

    Emitted once per execute_sdk() call from Claude, LiteLLM, and OpenCode providers.
    Captures the response-side metadata that PromptSent does not cover: token
    counts, cost, timing, model identity, and turn count.

    For the Claude provider, fields are extracted from the SDK's ResultMessage.
    For the LiteLLM provider, fields are aggregated across all turns in the
    multi-turn acompletion() loop.

    Attributes:
        input_tokens: Total input/prompt tokens consumed across all turns.
        output_tokens: Total output/completion tokens produced across all turns.
        total_tokens: Total tokens (input + output). May differ from the sum
            if the provider reports a different total (e.g. cache adjustments).
        cost_usd: Total cost in USD. None when cost tracking is unavailable
            (e.g. unknown model, error path, SDK limitation).
        duration_ms: Wall-clock duration of the entire invocation in milliseconds,
            including tool execution time.
        duration_api_ms: API-only duration in milliseconds, excluding tool
            execution time. For Claude, from ResultMessage.duration_api_ms.
            For LiteLLM, sum of perf_counter() around each acompletion() call.
        num_turns: Number of shepherd turns (LLM call -> tool execution cycles).
        model_id: Model identifier that actually served the request. May differ
            from the requested model due to aliases or routing. Empty string
            means unknown.
        is_error: Whether the invocation ended in error.
        usage_details: Raw usage dict for forward compatibility. For Claude,
            the ResultMessage.usage dict. For LiteLLM, the last turn's
            response.usage.model_dump(). None when unavailable.
        cache_creation_input_tokens: Tokens written to prompt cache (0 if
            caching not active).
        cache_read_input_tokens: Tokens read from prompt cache (0 if caching
            not active).
    """

    effect_type: Literal["llm_response_received"] = "llm_response_received"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: float = 0.0
    duration_api_ms: float = 0.0
    num_turns: int = 0
    model_id: str = ""
    is_error: bool = False
    usage_details: dict[str, Any] | None = None
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class AgentThinking(Effect):
    """Agent reasoning/thinking content."""

    effect_type: Literal["agent_thinking"] = "agent_thinking"
    content: str = ""
    is_partial: bool = False  # True if streaming delta


class AgentMessage(Effect):
    """Agent text message/response."""

    effect_type: Literal["agent_message"] = "agent_message"
    content: str = ""
    is_partial: bool = False  # True if streaming delta


# =============================================================================
# Execution Failure and Recovery Effects
# =============================================================================


class ExecutionFailed(Effect):
    """Provider execution failed with potentially recoverable error.

    Emitted when execution fails in a way that may allow recovery,
    such as buffer overflow errors from large tool outputs.

    Attributes:
        error_type: Category of error ("buffer_overflow", "timeout", "api_error")
        error_message: Full error message for debugging
        tool_calls_completed: Number of successful tool calls before failure
        last_tool_name: Name of tool that triggered the failure (if known)
        recoverable: Whether recovery can be attempted (e.g., session_id available)
    """

    effect_type: Literal["execution_failed"] = "execution_failed"
    error_type: str = ""
    error_message: str = ""
    tool_calls_completed: int = 0
    last_tool_name: str | None = None
    recoverable: bool = False


class RecoveryAttempted(Effect):
    """Automatic recovery from failed execution attempted.

    Emitted when the provider attempts to recover from a failure
    by forking the session and retrying with modified context.

    Attributes:
        original_session_id: Session ID being recovered from
        error_type: Type of error that triggered recovery
        last_tool_name: Tool that caused the failure (if known)
        recovery_strategy: Strategy used ("fork_and_retry")
    """

    effect_type: Literal["recovery_attempted"] = "recovery_attempted"
    original_session_id: str = ""
    error_type: str = ""
    last_tool_name: str | None = None
    recovery_strategy: str = "fork_and_retry"


# =============================================================================
# Artifact Effects
# =============================================================================


class ArtifactWritten(Effect):
    """Artifact file was written by the agent (Result Effect).

    Stores full artifact content for verification and replay.

    Attributes:
        filename: Name of the artifact file
        path: Full path where artifact was written
        content_type: Type of content ("text" or "json")
        size_bytes: Size of the artifact in bytes
        field_name: Task output field this artifact populates
        content: Complete artifact content (text or JSON string)
        content_hash: SHA256 hash for verification
        content_truncated: True if content was truncated (for future use)
        caused_by: tool_call_id that wrote this artifact
    """

    effect_type: Literal["artifact_written"] = "artifact_written"
    filename: str = ""
    path: str = ""
    content_type: str = ""  # "text" or "json"
    size_bytes: int = 0
    field_name: str = ""
    content: str = ""
    content_hash: str = ""
    content_truncated: bool = False
    caused_by: str | None = None  # tool_call_id that wrote this artifact

    @property
    def content_preview(self) -> str:
        """Truncated preview for display (computed, not serialized)."""
        if len(self.content) > PREVIEW_LENGTH_ARTIFACT:
            return self.content[:PREVIEW_LENGTH_ARTIFACT] + "..."
        return self.content


class ArtifactMissing(Effect):
    """Expected artifact was not found after execution."""

    effect_type: Literal["artifact_missing"] = "artifact_missing"
    filename: str = ""
    field_name: str = ""
    required: bool = True


# =============================================================================
# Container Execution Effects
# =============================================================================


class ContainerExecutionCompleted(Effect):
    """Emitted when a container task execution completes.

    This effect enables scope-based sandbox tracking for overlay layering.
    When Task B needs to see Task A's file changes, it queries the scope's
    effect stream to find the parent sandbox.

    Attributes:
        sandbox_id: Unique identifier for the sandbox (used for lookup).
        context_name: Name of the context (e.g., "workspace") for filtering.
        task_name: Name of the task that completed (for debugging).
        has_workspace_changes: Whether the overlay upper layer has changes.

    See Also:
        PLAN-workspace-patch-layering.md - Design rationale for overlay stacking
    """

    effect_type: Literal["container_execution_completed"] = "container_execution_completed"
    sandbox_id: str = ""
    context_name: str = ""
    task_name: str = ""  # Name of the task that completed (for debugging)
    has_workspace_changes: bool = False


# =============================================================================
# External API Domain Effects
# =============================================================================


class ExternalAPICall(Effect):
    """External API was called.

    Forward-compatible structure for when external API contexts are implemented.
    Currently no production emission sites exist.

    Attributes:
        service: Name of the external service
        endpoint: API endpoint URL
        method: HTTP method (GET, POST, etc.)
        status_code: HTTP response status code
        request_body: Request body (JSON string or empty)
        response_body: Response body (JSON string or empty)
        response_headers: Key response headers (content-type, etc.)
        duration_ms: Request duration in milliseconds
    """

    effect_type: Literal["external_api_call"] = "external_api_call"
    service: str = ""
    endpoint: str = ""
    method: str = "GET"
    status_code: int = 0
    request_body: str = ""
    response_body: str = ""
    response_headers: dict[str, str] = Field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def request_preview(self) -> str:
        """Truncated request body for display (computed, not serialized)."""
        if len(self.request_body) > PREVIEW_LENGTH_API_BODY:
            return self.request_body[:PREVIEW_LENGTH_API_BODY] + "..."
        return self.request_body

    @property
    def response_preview(self) -> str:
        """Truncated response body for display (computed, not serialized)."""
        if len(self.response_body) > PREVIEW_LENGTH_API_BODY:
            return self.response_body[:PREVIEW_LENGTH_API_BODY] + "..."
        return self.response_body


# =============================================================================
# Effect Registry
# =============================================================================

# All effect types for serialization/deserialization
EFFECT_TYPES: dict[str, type[Effect]] = {
    # Task lifecycle
    "task_started": TaskStarted,
    "task_completed": TaskCompleted,
    "task_failed": TaskFailed,
    "input_provided": InputProvided,
    "output_produced": OutputProduced,
    # Context lifecycle
    "context_configured": ContextConfigured,
    "context_prepared": ContextPrepared,
    "context_captured": ContextCaptured,
    "context_cleaned_up": ContextCleanedUp,
    # Lifecycle phases
    "lifecycle_phase_started": LifecyclePhaseStarted,
    "lifecycle_phase_completed": LifecyclePhaseCompleted,
    "lifecycle_phase_failed": LifecyclePhaseFailed,
    # Tool effects
    "tool_call_started": ToolCallStarted,
    "tool_call_completed": ToolCallCompleted,
    "tool_call_rejected": ToolCallRejected,
    "tool_call_batch": ToolCallBatch,
    # File domain
    "file_read": FileRead,
    "file_create": FileCreate,
    "file_patch": FilePatch,
    "file_delete": FileDelete,
    # Step lifecycle
    "step_started": StepStarted,
    "step_completed": StepCompleted,
    "step_failed": StepFailed,
    # Agent trace
    "prompt_sent": PromptSent,
    "llm_response_received": LLMResponseReceived,
    "agent_thinking": AgentThinking,
    "agent_message": AgentMessage,
    # Execution failure and recovery
    "execution_failed": ExecutionFailed,
    "recovery_attempted": RecoveryAttempted,
    # Artifact
    "artifact_written": ArtifactWritten,
    "artifact_missing": ArtifactMissing,
    # Container execution
    "container_execution_completed": ContainerExecutionCompleted,
    # Workspace materialization
    "workspace_materialized": WorkspaceMaterialized,
    # Context materialization (scope.commit())
    "context_materialized": ContextMaterialized,
    # External API
    "external_api_call": ExternalAPICall,
    # Pipeline stage lifecycle
    "stage_started": StageStarted,
    "stage_completed": StageCompleted,
    "stage_skipped": StageSkipped,
    "stage_failed": StageFailed,
    # Cache effects now live under shepherd_runtime.cache and are composed
    # explicitly into the runtime registry.
    # "cache_hit": CacheHit,
    # "cache_miss": CacheMiss,
    # "cache_stored": CacheStored,
}

KERNEL_EFFECT_REGISTRY = EffectTypeRegistry(EFFECT_TYPES)


def get_effect_type(effect: Effect) -> str:
    """Get the effect type string for an effect instance."""
    return effect.effect_type


def effect_from_dict(data: dict[str, Any], *, registry: EffectTypeRegistry | None = None) -> Effect:
    """Deserialize an effect from a dictionary.

    Uses the effect_type field to determine the correct class,
    then delegates to Pydantic's model_validate().

    Args:
        data: Dictionary with effect_type and effect fields.
        registry: Optional explicit registry snapshot for decode. Defaults to
            the kernel-only registry.

    Returns:
        Appropriate Effect subclass instance
    """
    return decode_effect(data, registry=registry)


def register_effect(cls: type[Effect]) -> type[Effect]:
    """Register a custom effect type for serialization.

    Use as a decorator:
        @register_effect
        class MyEffect(Effect):
            effect_type: Literal["my_effect"] = "my_effect"
            ...

    The effect is registered by its effect_type default value.
    """
    # Get the effect type from the model field's default
    effect_type_field = cls.model_fields.get("effect_type")
    if effect_type_field is None:
        raise ValueError(f"Effect class {cls.__name__} must have an effect_type field")

    effect_type_key = effect_type_field.default
    if effect_type_key is None:
        raise ValueError(f"Effect class {cls.__name__} must have a default value for effect_type")

    # Warn if overwriting an existing registration with a different class
    if effect_type_key in EFFECT_TYPES and EFFECT_TYPES[effect_type_key] is not cls:
        warnings.warn(
            f"Effect type '{effect_type_key}' is already registered to "
            f"{EFFECT_TYPES[effect_type_key].__name__}, overwriting with {cls.__name__}",
            stacklevel=2,
        )

    # Register by effect_type
    EFFECT_TYPES[effect_type_key] = cls

    return cls


def get_effect_class(name: str, *, registry: EffectTypeRegistry | None = None) -> type[Effect]:
    """Get effect class by name from registry.

    Args:
        name: The effect_type value (e.g., "task_started")
        registry: Optional explicit registry snapshot for lookup. Defaults to
            the kernel-only registry.

    Returns:
        The registered Effect class, or base Effect if not found.
    """
    return resolve_effect_class(name, registry=registry)


__all__ = [  # noqa: RUF022
    # Registry and utilities
    "EFFECT_TYPES",
    "EffectTypeRegistry",
    "KERNEL_EFFECT_REGISTRY",
    # Intent effect classification
    "INTENT_EFFECT_TYPES",
    # Large content handling
    "MAX_CONTENT_SIZE",
    "PREVIEW_LENGTH_API_BODY",
    "PREVIEW_LENGTH_ARTIFACT",
    "PREVIEW_LENGTH_FILE_CONTENT",
    "PREVIEW_LENGTH_PROMPT",
    "PREVIEW_LENGTH_STEP_SUMMARY",
    # Preview length constants
    "PREVIEW_LENGTH_TOOL_OUTPUT",
    "TRUNCATE_HEAD_SIZE",
    "TRUNCATE_TAIL_SIZE",
    "AgentMessage",
    "AgentThinking",
    "ArtifactMissing",
    # Artifact
    "ArtifactWritten",
    # Container execution
    "ContainerExecutionCompleted",
    "ContextCaptured",
    "ContextCleanedUp",
    # Context lifecycle
    "ContextConfigured",
    # Context materialization
    "ContextMaterialized",
    "ContextPrepared",
    # Data types
    "DiffPatch",
    # Base
    "Effect",
    # Execution failure and recovery
    "ExecutionFailed",
    # External API
    "ExternalAPICall",
    "FileCreate",
    "FileDelete",
    "FilePatch",
    # File domain
    "FileRead",
    "InputProvided",
    # Type aliases
    "LifecyclePhase",
    "LifecyclePhaseCompleted",
    "LifecyclePhaseFailed",
    # Lifecycle phase effects
    "LifecyclePhaseStarted",
    "OutputProduced",
    # Agent trace
    "PromptSent",
    "RecoveryAttempted",
    "StepCompleted",
    "StepFailed",
    # Step lifecycle
    "StepStarted",
    "TaskCompleted",
    "TaskFailed",
    # Task lifecycle
    "TaskStarted",
    "ToolCallCompleted",
    "ToolCallRejected",
    # Tool effects
    "ToolCallStarted",
    # Workspace materialization
    "WorkspaceMaterialized",
    "effect_from_dict",
    "get_effect_class",
    "get_effect_type",
    "is_intent_effect",
    "is_result_effect",
    "register_effect",
    "truncate_with_hash",
]
