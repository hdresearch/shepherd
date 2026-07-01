"""Device protocol - execution backend that provides isolated environments.

Devices determine HOW effects are captured:

| Device    | Effect Capture    | Isolation | Use Case   |
|-----------|-------------------|-----------|------------|
| local     | Git diff          | None      | Development|
| container | OverlayFS diff    | Full      | Production |
| cloud     | Network protocol  | Network   | Scale-out  |

Tasks are device-agnostic. The Device provides the execution environment
and effect capture mechanism.

The PyTorch Parallel
--------------------
Just as PyTorch separates WHAT you compute (tensor ops) from WHERE
it runs (CPU/CUDA), Shepherd separates WHAT you do (tasks) from
WHERE it executes (devices).

    # PyTorch
    model = Model().to("cuda")

    # Shepherd
    with Device("container"):
        result = await FixBug(...)

See Also:
    design/syntax-api/DESIGN-primitives-layer.md - Device protocol specification
    design/device-abstraction/QUICKSTART-device-model.md - Device mental model
    design/containerized-execution/ - Container device implementation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shepherd_core.foundation.protocols.effect import EffectProtocol
    from shepherd_core.foundation.protocols.scope import ScopeProtocol


# =============================================================================
# DEVICE CAPABILITIES
# =============================================================================


@dataclass(frozen=True)
class DeviceCapabilities:
    """Capabilities of a device.

    Devices declare what they support so the framework can:
    - Select appropriate devices for tasks
    - Validate task requirements against device capabilities
    - Provide helpful errors when capabilities are missing

    Attributes:
        isolation_level: How isolated execution is from the host.
            - "none": No isolation (local device)
            - "process": Process-level isolation
            - "container": Container-level isolation (namespaces, cgroups)
            - "vm": Full VM isolation

        effect_capture: How effects are captured.
            - "git": Git diff on working directory
            - "overlay": OverlayFS upper layer diff
            - "explicit": Only explicitly emitted effects
            - "network": Effects captured via network protocol

        supports_checkpoint: Can checkpoint execution state (CRIU).
        supports_restore: Can restore from checkpoint.
        supports_dmtcp: Can use DMTCP for Python state transfer.
        supports_parallel: Can run parallel children efficiently.
    """

    isolation_level: Literal["none", "process", "container", "vm"]
    effect_capture: Literal["git", "overlay", "explicit", "network"]
    supports_checkpoint: bool = False
    supports_restore: bool = False
    supports_dmtcp: bool = False
    supports_parallel: bool = True


# =============================================================================
# SANDBOX CONFIGURATION
# =============================================================================


@dataclass(frozen=True)
class ResourceLimits:
    """Resource limits for sandbox execution.

    Attributes:
        cpu_cores: Maximum CPU cores (None = unlimited)
        memory_mb: Maximum memory in MB (None = unlimited)
        disk_mb: Maximum disk in MB (None = unlimited)
        timeout_seconds: Maximum execution time (None = unlimited)
        network_enabled: Whether network access is allowed
    """

    cpu_cores: int | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None
    timeout_seconds: int | None = None
    network_enabled: bool = True


@dataclass(frozen=True)
class SandboxConfig:
    """Configuration for sandbox creation.

    This is the input to Device.create_sandbox(). It captures everything
    the device needs to create an isolated execution environment.

    Attributes:
        context_states: Serialized context states by binding name.
            The device uses these to set up the sandbox environment
            (mount overlays, configure services, etc.)

        parent_sandbox_id: For nested sandboxes, the parent's ID.
            Enables overlay stacking where child sees parent's state.

        resource_limits: Resource constraints for execution.

        environment: Additional environment variables to set.

        working_directory: Working directory path (relative to sandbox).
    """

    context_states: Mapping[str, ContextState]
    parent_sandbox_id: str | None = None
    resource_limits: ResourceLimits | None = None
    environment: Mapping[str, str] | None = None
    working_directory: str | None = None


# =============================================================================
# CONTEXT STATE (for serialization across device boundaries)
# =============================================================================


@runtime_checkable
class ContextState(Protocol):
    """Serializable context state for transfer across device boundaries.

    Each ExecutionContext type defines its own ContextState that captures
    everything needed to reconstruct the context in a sandbox.

    Example:
        @dataclass(frozen=True)
        class WorkspaceState(ContextState):
            context_type: str = "workspace"
            base_commit: str
            pending_patches: tuple[str, ...]
            capabilities: frozenset[str]
    """

    @property
    def context_type(self) -> str:
        """Type discriminator for deserialization."""
        ...

    def rebind(self, env: Mapping[str, str]) -> ContextState:
        """Return state with paths rebound for new environment.

        Called by Device when paths differ between host and sandbox.
        Default: return self unchanged.

        Args:
            env: Environment variables with path mappings
                 WORKSPACE_PATH: /container/workspace
                 SESSION_PATH: /container/.claude
                 etc.

        Returns:
            New state with updated paths
        """
        ...


# =============================================================================
# CONTEXT STATE BASE CLASS
# =============================================================================


@dataclass(frozen=True)
class ContextStateBase:
    """Base class for context states with default rebind() implementation.

    Concrete context states should inherit from this class to get the
    default no-op rebind() behavior. States that have paths to translate
    should override rebind().

    Example:
        @dataclass(frozen=True)
        class MyState(ContextStateBase):
            context_type: str = "my_context"
            data: str = ""

            def rebind(self, env: Mapping[str, str]) -> MyState:
                # Override if paths need translation
                return self
    """

    @property
    def context_type(self) -> str:
        """Type discriminator for deserialization.

        Subclasses must override this property.
        """
        raise NotImplementedError("Subclass must define context_type")

    def rebind(self, env: Mapping[str, str]) -> ContextStateBase:
        """Return state with paths rebound for new environment.

        Default implementation returns self unchanged. Override this
        method in subclasses that have paths requiring translation.

        Args:
            env: Environment variables with path mappings:
                WORKSPACE_PATH: /container/workspace
                SESSION_PATH: /container/.claude

        Returns:
            Self unchanged (default behavior)
        """
        return self


# =============================================================================
# EXECUTION CONTEXT PROTOCOL
# =============================================================================


@runtime_checkable
class ExecutionContextProtocol(Protocol):
    """Minimal scope interface for device execution.

    Subset of ScopeProtocol that Provider.execute_sdk() actually requires.
    Implementations: Scope (in-process), EffectCollector (container).

    This protocol enables the task runner inside containers to provide a
    scope-like interface without full Scope infrastructure.
    """

    @property
    def id(self) -> str:
        """Identifier for effect attribution."""
        ...

    def emit(self, effect: EffectProtocol) -> None:
        """Emit an effect during execution."""
        ...


# =============================================================================
# EXECUTION SPEC
# =============================================================================


@dataclass(frozen=True)
class TaskSpec:
    """Specification for programmatic task execution in a container.

    Encapsulates everything needed to reconstruct and run a programmatic
    task (one with a custom execute() method) inside a container sandbox.
    The container reconstructs the task class from source code, instantiates
    it with serialized inputs, attaches contexts, calls execute(), and
    returns serialized outputs.
    """

    task_source: str
    task_class_name: str
    task_imports: tuple[str, ...]
    task_inputs: Mapping[str, Any]
    output_fields: tuple[str, ...]
    context_fields: Mapping[str, str]  # task field name -> binding name
    is_async: bool = False


@dataclass(frozen=True)
class ExecutionSpec:
    """Specification for what to execute in a sandbox.

    This separates WHAT to execute from WHERE (the device) and
    WITH WHAT STATE (the sandbox).

    When task_spec is set, the container runs in programmatic mode
    (reconstructing and executing a @task class). When task_spec is None,
    the container runs in LLM mode (delegating to a provider).

    Attributes:
        prompt: The user prompt to execute (empty string for programmatic specs).
        provider_config: Provider-specific configuration (empty dict for programmatic specs).
        tools: Tools available during execution.
        output_format: Expected output schema (for structured output).
        task_spec: Programmatic task specification (None for LLM specs).
    """

    prompt: str
    provider_config: Mapping[str, Any]
    tools: Sequence[str] | None = None
    output_format: Mapping[str, Any] | None = None
    task_spec: TaskSpec | None = None


# =============================================================================
# EFFECT BUNDLE (device output)
# =============================================================================


@dataclass(frozen=True)
class EffectBundle:
    """Effects extracted from sandbox execution.

    Organizes effects by their source for proper routing:
    - context_effects: Effects targeting specific contexts (by binding name)
    - lifecycle_effects: Task lifecycle effects (TaskStarted, etc.)

    The parent scope uses this to apply effects correctly.

    Attributes:
        context_effects: Effects by binding name.
        lifecycle_effects: Task-level effects (not context-specific).
        execution_metadata: Additional execution info (timing, costs, etc.)
    """

    context_effects: Mapping[str, Sequence[EffectProtocol]]
    lifecycle_effects: Sequence[EffectProtocol]
    execution_metadata: Mapping[str, Any] | None = None


# =============================================================================
# EXECUTION RESULT
# =============================================================================


@dataclass(frozen=True)
class ExecutionResult:
    """Result from device execution.

    Contains outputs only. Intent effects flow through emit() during execution.
    Result effects come from extract_effects() after execution completes.

    This is the return type from DeviceProtocol.execute(), replacing the
    previous `Any` return type for better type safety.

    Attributes:
        success: Whether execution completed successfully.
        output_text: Text output from the execution (e.g., LLM response).
        structured_output: Parsed structured output (if output_format was specified).
        session_id: Session identifier for resumption (if applicable).
        metadata: Additional execution info (timing, costs, token usage, etc.)
    """

    success: bool = True
    output_text: str = ""
    structured_output: Mapping[str, Any] | None = None
    session_id: str | None = None
    metadata: Mapping[str, Any] | None = None


# =============================================================================
# SANDBOX HANDLE
# =============================================================================


@runtime_checkable
class SandboxHandle(Protocol):
    """Opaque handle to a sandbox.

    The handle is created by Device.create_sandbox() and passed to
    other device methods. Its internal structure is device-specific.

    Attributes:
        sandbox_id: Unique identifier for this sandbox.
        device_name: Name of the device that created it.
    """

    @property
    def sandbox_id(self) -> str:
        """Unique identifier for this sandbox."""
        ...

    @property
    def device_name(self) -> str:
        """Name of the device that created this sandbox."""
        ...


# =============================================================================
# DEVICE PROTOCOL
# =============================================================================


@runtime_checkable
class DeviceProtocol(Protocol):
    """Execution backend that provides isolated environments.

    Devices are responsible for:
    1. Creating isolated sandboxes for task execution
    2. Executing tasks within sandboxes
    3. Extracting effects from sandboxes after execution
    4. Cleaning up sandboxes

    The Device abstraction enables:
    - Same task code running on different backends
    - Pluggable isolation strategies
    - Pluggable effect capture mechanisms

    Example:
        device = get_device("container")

        # Create sandbox with context state
        config = SandboxConfig(context_states={"workspace": ws_state})
        sandbox = await device.create_sandbox(scope, config)

        try:
            # Execute in sandbox
            spec = ExecutionSpec(prompt="Fix the bug", provider_config={...})
            result = await device.execute(sandbox, spec)

            # Extract effects
            bundle = await device.extract_effects(sandbox, result)

            # Apply to scope
            for effect in bundle.lifecycle_effects:
                scope.emit(effect)
            for binding_name, effects in bundle.context_effects.items():
                for effect in effects:
                    scope.emit(effect.with_attribution(binding_name=binding_name))
        finally:
            await device.cleanup(sandbox)
    """

    @property
    def name(self) -> str:
        """Device identifier (e.g., "local", "container", "cloud")."""
        ...

    @property
    def capabilities(self) -> DeviceCapabilities:
        """What this device supports."""
        ...

    # --- Sandbox Lifecycle ---

    async def create_sandbox(
        self,
        scope: ScopeProtocol,
        config: SandboxConfig,
    ) -> SandboxHandle:
        """Create isolated execution environment.

        The sandbox is configured with:
        - Context states from config (mounted as appropriate)
        - Parent sandbox overlay (if parent_sandbox_id set)
        - Resource limits from config

        Args:
            scope: Parent scope (for inheriting provider config, etc.)
            config: Sandbox configuration

        Returns:
            Handle to the created sandbox

        Raises:
            SandboxCreationError: If sandbox creation fails
        """
        ...

    async def execute(
        self,
        sandbox: SandboxHandle,
        spec: ExecutionSpec,
    ) -> ExecutionResult:
        """Execute within sandbox.

        Runs the specified execution in the sandbox. Returns an
        ExecutionResult with outputs and metadata.

        Note: IntentEffects (ToolCallStarted, etc.) are emitted during
        execution by the provider via the ExecutionContextProtocol,
        not extracted afterward.

        Args:
            sandbox: Handle from create_sandbox()
            spec: What to execute

        Returns:
            ExecutionResult with outputs and metadata

        Raises:
            SandboxExecutionError: If execution fails
        """
        ...

    async def extract_effects(
        self,
        sandbox: SandboxHandle,
        execution_result: ExecutionResult,
    ) -> EffectBundle:
        """Extract effects from sandbox after execution.

        This captures ResultEffects by examining sandbox state:
        - Filesystem changes (via overlay diff, git diff, etc.)
        - Service state changes
        - Session artifacts

        The execution_result provides IntentEffects (tool calls) that
        can be used to establish causality via the `caused_by` field.

        Args:
            sandbox: Handle to sandbox after execution
            execution_result: Result from execute()

        Returns:
            Bundle of effects organized by context
        """
        ...

    async def cleanup(
        self,
        sandbox: SandboxHandle,
    ) -> None:
        """Release sandbox resources.

        Called after effects are extracted (normal path) or on error.
        Must be idempotent — safe to call multiple times.

        Args:
            sandbox: Handle to cleanup
        """
        ...


# =============================================================================
# ERRORS
# =============================================================================


class DeviceError(Exception):
    """Base error for device operations."""


class SandboxCreationError(DeviceError):
    """Failed to create sandbox with diagnostic context.

    Attributes:
        phase: Which phase of creation failed ("validation", "overlay_setup", "container_config")
        root_cause: Specific failure reason for programmatic handling
        original_error: The underlying exception if any
    """

    def __init__(
        self,
        message: str,
        *,
        phase: str = "unknown",
        root_cause: str | None = None,
        original_error: Exception | None = None,
    ):
        self.phase = phase
        self.root_cause = root_cause
        self.original_error = original_error
        super().__init__(message)

    def __str__(self) -> str:
        lines = [super().__str__()]
        if self.phase != "unknown":
            lines.append(f"  Phase: {self.phase}")
        if self.root_cause:
            lines.append(f"  Cause: {self.root_cause}")
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Factory methods for common failure cases
    # -------------------------------------------------------------------------

    @classmethod
    def podman_unavailable(cls) -> SandboxCreationError:
        """Podman daemon is not running or not accessible."""
        return cls(
            "Podman is not running or not accessible.\n"
            "To fix:\n"
            "  macOS: podman machine start\n"
            "  Linux: systemctl start podman.socket",
            phase="validation",
            root_cause="podman_unavailable",
        )

    @classmethod
    def image_not_found(cls, image: str) -> SandboxCreationError:
        """Container image not found."""
        if image == "shepherd-sandbox":
            hint = f"Container image not found: {image}\nTo build: podman build -t shepherd-sandbox containers/sandbox/"
        else:
            hint = f"Container image not found: {image}\nTo fix: podman pull {image}"
        return cls(
            hint,
            phase="validation",
            root_cause="image_not_found",
        )

    @classmethod
    def insufficient_disk(
        cls,
        needed_mb: int,
        available_mb: int,
        location: str,
    ) -> SandboxCreationError:
        """Insufficient disk space for sandbox creation."""
        return cls(
            f"Insufficient disk space at {location}:\n"
            f"  Need: {needed_mb} MB\n"
            f"  Have: {available_mb} MB\n"
            f"To fix: Free up disk space or reconfigure overlays_root",
            phase="overlay_setup",
            root_cause="insufficient_disk",
        )

    @classmethod
    def overlay_mount_failed(
        cls,
        mount_point: str,
        reason: str,
    ) -> SandboxCreationError:
        """OverlayFS mount operation failed."""
        return cls(
            f"Failed to mount overlay at {mount_point}: {reason}\n"
            "Possible causes:\n"
            "  - OverlayFS not supported (check kernel version)\n"
            "  - tmpfs upper directory (not supported, use ext4)\n"
            "  - Permission denied (check mount permissions)",
            phase="overlay_setup",
            root_cause="overlay_mount_failed",
        )

    @classmethod
    def container_create_failed(
        cls,
        reason: str,
        stderr: str | None = None,
    ) -> SandboxCreationError:
        """Container creation via Podman failed."""
        msg = f"Failed to create container: {reason}"
        if stderr:
            msg += f"\nPodman stderr: {stderr[:500]}"
        return cls(
            msg,
            phase="container_config",
            root_cause="container_create_failed",
        )


class SandboxExecutionError(DeviceError):
    """Execution within sandbox failed with diagnostic context.

    Attributes:
        phase: Which phase of execution failed ("container_start", "task_execution", "output_reading")
        exit_code: Container exit code if available
        stderr: Container stderr if available
        container_id: Container ID for debugging
    """

    def __init__(
        self,
        message: str,
        *,
        phase: str = "unknown",
        exit_code: int | None = None,
        stderr: str | None = None,
        container_id: str | None = None,
    ):
        self.phase = phase
        self.exit_code = exit_code
        self.stderr = stderr
        self.container_id = container_id
        super().__init__(message)

    def __str__(self) -> str:
        lines = [super().__str__()]
        if self.exit_code is not None:
            lines.append(f"  Exit code: {self.exit_code}")
        if self.container_id:
            lines.append(f"  Container: {self.container_id}")
        if self.stderr:
            # Truncate long stderr
            stderr_preview = self.stderr[:300] + "..." if len(self.stderr) > 300 else self.stderr
            lines.append(f"  Stderr: {stderr_preview}")
        return "\n".join(lines)


class EffectExtractionError(DeviceError):
    """Failed to extract effects from sandbox with diagnostic context.

    Attributes:
        phase: Which phase of extraction failed ("overlay_diff", "whiteout_detection", "serialize")
        overlay_path: Path to the overlay if available
        original_error: The underlying exception if any
    """

    def __init__(
        self,
        message: str,
        *,
        phase: str = "extraction",
        overlay_path: str | None = None,
        original_error: Exception | None = None,
    ):
        self.phase = phase
        self.overlay_path = overlay_path
        self.original_error = original_error
        super().__init__(message)

    def __str__(self) -> str:
        lines = [super().__str__()]
        if self.overlay_path:
            lines.append(f"  Overlay: {self.overlay_path}")
        return "\n".join(lines)


__all__ = [
    "ContextState",
    "ContextStateBase",
    "DeviceCapabilities",
    "DeviceError",
    "DeviceProtocol",
    "EffectBundle",
    "EffectExtractionError",
    "ExecutionContextProtocol",
    "ExecutionResult",
    "ExecutionSpec",
    "ResourceLimits",
    "SandboxConfig",
    "SandboxCreationError",
    "SandboxExecutionError",
    "SandboxHandle",
    "TaskSpec",
]
